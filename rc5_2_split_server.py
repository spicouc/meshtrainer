"""RC5.2 Phase 2 W5: Split server runtime."""
import torch, torch.nn as nn
from rc5_2_numerical_models import SplitServerNumericalModel
from rc5_2_numerical_profile import PROFILE as P
from rc5_2_tensor_bundle import tensor_bundle_v1_pack, tensor_bundle_v1_unpack, delta_bundle_pack

class SplitServerRuntime:
    """Wraps SplitServerNumericalModel with protocol-compatible interface."""
    def __init__(self):
        self.server = SplitServerNumericalModel()
        self._cut_activation = None
        self._cut_gradient = None

    def load_state(self, ref_state: dict):
        for n, p in self.server.named_parameters():
            if n in ref_state: p.data.copy_(ref_state[n].clone())

    def step_open(self, tokens, labels, causal_mask):
        """Compute server activation and return as bundle."""
        sa = self.server.server_forward(tokens, causal_mask)
        return sa

    def worker_forward_submit(self, cut_activation, tokens, labels, causal_mask):
        """Re-attach, compute loss+backward, cache gradient."""
        self._cut_activation = cut_activation
        result = self.server.server_loss_and_backward(cut_activation, labels, causal_mask)
        self._cut_gradient = result["cut_gradient"]
        return {
            "loss": result["loss"],
            "ett": 127,
            "backward_id": __import__("hashlib").sha256(repr(cut_activation.shape).encode()).hexdigest()[:16],
            "logits": result["logits"],
        }

    def backward_fetch(self):
        """Return cached cut gradient."""
        return self._cut_gradient

    def verify_delta(self, bundle_data: bytes) -> bool:
        """Verify delta bundle can be unpacked."""
        try:
            tensors = tensor_bundle_v1_unpack(bundle_data)
            return "local_layers.0.linear1.lora_A.weight" in tensors
        except Exception:
            return False
