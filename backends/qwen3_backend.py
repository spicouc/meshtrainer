"""backends/qwen3_backend.py — Qwen3 com a PLUGIN TrainingBackend (R2.1).

Embolcalla el Qwen3TrainingBackend certificat (qwen3_training_backend.py,
que queda intacte) exposant el contracte genèric TrainingBackend.

Aquest mòdul POT importar transformers/peft/torch/template Qwen (és el
plugin). Coordinator/HTTP/Worker genèrics NO importen aquest fitxer
directament: el carreguen via registry (model_worker.BACKENDS).
"""
import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from qwen3_training_backend import Qwen3TrainingBackend  # noqa: E402
from training_backend import TrainingBackend             # noqa: E402


class Qwen3Backend(Qwen3TrainingBackend, TrainingBackend):
    """Qwen3-0.6B + LoRA implementant el contracte genèric.

    Reutilitza TOT el codi certificat de Qwen3TrainingBackend (journal
    SQLite, exactly-once, recovery, optimizer state, etc.) i afegeix els
    noms del contracte TrainingBackend.
    """

    name = "qwen3"
    journal_table = "qwen_journal"   # taula real del Qwen3TrainingBackend

    def __init__(self, model_path: str, db_path: str = "qwen_worker_journal.db",
                 seq_len: int = 256, lora: dict | None = None, seed: int = 424242):
        Qwen3TrainingBackend.__init__(self, model_path, db_path=db_path,
                                      seq_len=seq_len, lora=lora, seed=seed)

    # ── contracte TrainingBackend ────────────────────────────────────────
    def load_model(self):
        return self.load()

    def base_model_identity(self) -> dict:
        if self._base_identity is None:
            self._base_identity = self._compute_base_identity()
        return dict(self._base_identity)

    def load_adapter(self, data: bytes, expected_sha: str | None = None,
                     strict: bool = True):
        # 1) SHA sobre els bytes ORIGINALS (els que el Coordinator va
        #    hashejar) — abans de cap conversió de format.
        if strict and expected_sha is None:
            raise ValueError("expected_sha OBLIGATORI en mode MeshTrainer — REJECTED")
        if expected_sha is not None:
            actual = hashlib.sha256(data).hexdigest()
            if actual != expected_sha:
                raise ValueError(
                    f"adapter SHA mismatch: {actual[:12]} != "
                    f"{expected_sha[:12]} — REJECTED")
        # 2) Conversió de format si cal: el FedAvg genèric (adapter_codec)
        #    produeix 'adapter_bundle_v1'; el backend certificat espera
        #    'qwen_bundle_v1'. Els tensors són els mateixos.
        try:
            self._deserialize_delta(data)   # schema qwen_bundle_v1?
        except ValueError:
            from adapter_codec import unpack_tensors
            tensors = unpack_tensors(data)  # adapter_bundle_v1 (o qwen)
            data = self._serialize_delta(tensors)
        # 3) Validació d'schema estricta (SHA ja verificat sobre originals)
        return self.load_adapter_from_bundle(data, expected_sha=None,
                                             strict=False)

    def adapter_hash(self) -> str:
        return self.hash_adapter()

    def post_hash(self) -> str:
        """R2.3 (punt 10): hash de l'estat post-entrenament amb el MATEIX
        mètode que el backend certificat usa per verificar la reconstrucció
        (hash_adapter_tolerant). El recovery reconstrueix pre+delta amb 1 ulp
        d'arrodoniment; l'evidència ha de comparar amb el mateix criteri,
        altrament un hash estricte canviaria tot el digest per un bit."""
        return self.hash_adapter_tolerant()

    def tokenize_example(self, instruction: str, response: str | None = None):
        if response is None:
            return self.tokenize_chat(instruction)
        return self.tokenize_chat(instruction, response)

    def serialize_adapter(self, tensors: dict) -> bytes:
        # accepta torch.Tensor o np.ndarray (el worker genèric passa numpy)
        import torch as _t
        conv = {n: (_t.as_tensor(v) if not hasattr(v, "detach") else v)
                for n, v in tensors.items()}
        return self._serialize_delta(conv)

    def deserialize_adapter(self, data: bytes) -> dict:
        try:
            return self._deserialize_delta(data)
        except ValueError:
            from adapter_codec import unpack_tensors
            return unpack_tensors(data)

    def validate_adapter_schema(self, data: bytes,
                                expected_sha: str | None = None):
        # reutilitza la validació estricta de load_adapter_from_bundle
        self.load_adapter_from_bundle(data, expected_sha, strict=True)
        return {"missing": [], "unexpected": []}

    def close(self):
        try:
            self._conn.close()
        except Exception:
            pass


def build(model_path: str, db_path: str, seq_len: int = 256,
          lora: dict | None = None, seed: int = 424242) -> Qwen3Backend:
    return Qwen3Backend(model_path, db_path=db_path, seq_len=seq_len,
                        lora=lora, seed=seed)
