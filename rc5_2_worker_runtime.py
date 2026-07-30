"""RC5.2 Phase 2 W5: Worker runtime."""
import torch, json, copy, hashlib
from rc5_2_numerical_models import WorkerNumericalModel
from rc5_2_numerical_profile import PROFILE as P
from rc5_2_tensor_bundle import (
    tensor_bundle_v1_pack, tensor_bundle_v1_unpack,
    delta_bundle_pack, bundle_sha256, verify_adapter_schema,
    bundle_sha256,
)

class WorkerRuntime:
    """Wraps WorkerNumericalModel with protocol-compatible interface."""
    def __init__(self):
        self.worker = WorkerNumericalModel()
        self._journal = {}  # update_id -> {status, pre, post, delta_data}
        self._pre_adapter = None
        self._cut_activation = None
        self._optimizer_applied = False

    def load_state(self, ref_state: dict):
        for n, p in self.worker.named_parameters():
            if n in ref_state: p.data.copy_(ref_state[n].clone())

    def worker_forward(self, activation, mask=None):
        """Forward through local layers, preserve graph."""
        self._cut_activation = self.worker.worker_forward(activation, mask)
        return self._cut_activation

    def worker_backward(self, cut_gradient):
        """Backward + optimizer step ONE TIME."""
        self._pre_adapter = {
            "local_layers.0.linear1.lora_A.weight": self.worker.lora_wrapper.lora_A.weight.detach().clone(),
            "local_layers.0.linear1.lora_B.weight": self.worker.lora_wrapper.lora_B.weight.detach().clone(),
        }
        wr = self.worker.worker_backward(self._cut_activation, cut_gradient)
        self._optimizer_applied = True
        return wr

    def compute_delta_bundle(self) -> bytes:
        """Compute delta = post - pre, return as tensor_bundle_v1."""
        post_A = self.worker.lora_wrapper.lora_A.weight.detach().clone()
        post_B = self.worker.lora_wrapper.lora_B.weight.detach().clone()
        pre_A = self._pre_adapter["local_layers.0.linear1.lora_A.weight"]
        pre_B = self._pre_adapter["local_layers.0.linear1.lora_B.weight"]
        delta_data = delta_bundle_pack(post_A - pre_A, post_B - pre_B)
        return delta_data

    def journal_apply(self, update_id: str, delta_data: bytes):
        """Record in journal. Prevents double optimizer step."""
        if update_id in self._journal:
            existing = self._journal[update_id]
            sha = bundle_sha256(delta_data)
            if existing["delta_sha"] != sha:
                raise ValueError("Same update_id, different payload — REJECTED")
            return existing["delta_data"]  # return cached
        self._journal[update_id] = {
            "status": "APPLIED",
            "pre": self._pre_adapter,
            "delta_data": delta_data,
            "delta_sha": bundle_sha256(delta_data),
        }
        return delta_data

    def get_journal_status(self, update_id: str) -> str:
        return self._journal.get(update_id, {}).get("status", "UNKNOWN")
