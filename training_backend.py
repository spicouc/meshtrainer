"""training_backend.py — INTERFÍCIE GENERICA TrainingBackend (R2.1).

Contracte mínim que qualsevol backend de model (Qwen, MiniCPM, Llama, Gemma,
Dummy de test) ha d'implementar. Cap component Coordinator/HTTP/Worker pot
importar un backend concret: només aquest ABC i adapter_codec.

El Coordinator treballa exclusivament amb:
  - tensor name / shape / dtype / bytes / SHA   (via AdapterCodec)
  - request_sha / adapter_pre_hash / delta_bundle_sha256  (strings)
"""
import abc


class TrainingBackend(abc.ABC):
    """Contrat mínim model-agnostic per a MeshTrainer."""

    # ── càrrega / identitat ──────────────────────────────────────────────
    @abc.abstractmethod
    def load_model(self):
        """Carrega el model base + prepara l'entrenament (LoRA/adapters)."""

    @abc.abstractmethod
    def base_model_identity(self) -> dict:
        """Fingerprint canònic del model BASE (pesos no-adapter):
        {model_identifier, config_sha, weight_manifest_sha,
         base_model_hash, seed, ...}. El base_model_hash ha de ser estable
        entre càrregues del mateix model."""

    @abc.abstractmethod
    def base_model_hash(self) -> str:
        """Hash canònic del model base (curtcircuit de base_model_identity)."""

    # ── adapters ─────────────────────────────────────────────────────────
    @abc.abstractmethod
    def load_adapter(self, data: bytes, expected_sha: str | None = None,
                     strict: bool = True):
        """Carrega un adapter des del bundle. strict=True: expected_sha
        OBLIGATORI i schema complet (cap tensor absent/extra, shapes exactes,
        dtype admès) -> REJECTED en cas contrari."""

    @abc.abstractmethod
    def adapter_to_bundle(self) -> bytes:
        """Serialitza l'estat adapter complet (adapter_0/1/2) en bytes."""

    @abc.abstractmethod
    def adapter_hash(self) -> str:
        """Hash de l'estat adapter ACTUAL del model carregat."""

    # ── dades / entrenament ──────────────────────────────────────────────
    @abc.abstractmethod
    def tokenize_example(self, instruction: str, response: str | None = None):
        """Tokenitza un exemple (format propi del model). Retorna
        (input_ids, attention_mask, labels) amb labels -100 al prompt."""

    @abc.abstractmethod
    def compute_ett(self, labels) -> int:
        """Effective Trainable Tokens d'uns labels (>=1)."""

    @abc.abstractmethod
    def train_step(self, update_id: str, instruction: str, response: str, *,
                   worker_id: str = "", assignment_id: str = "",
                   shard_id: str = "", example_id: str = "",
                   request_sha: str | None = None):
        """UN forward + UN backward + UN optimizer.step. Exactly-once:
        mateix update_id + mateix request_sha -> MATEIX delta (sense 2n pas);
        mateix update_id + request_sha DIFERENT -> REJECTED.
        Retorna (delta_b64, delta_sha)."""

    @abc.abstractmethod
    def recover_applied(self, update_id: str) -> dict | None:
        """Recovery POST-APPLIED: reconstrueix adapter_post = adapter_pre +
        delta SENSE optimizer.step (journal persistent). Verifica pre/post
        hash. Retorna {update_id, status, adapter_post_hash} o None."""

    @abc.abstractmethod
    def delta_tensors(self, update_id: str) -> dict:
        """Retorna el delta com a dict de np.ndarray (per al FedAvg real)."""

    # ── codec (normalment delegat a AdapterCodec) ─────────────────────────
    @abc.abstractmethod
    def serialize_adapter(self, tensors: dict) -> bytes:
        """Serialitza un dict de tensors en bytes (format codec genèric)."""

    @abc.abstractmethod
    def deserialize_adapter(self, data: bytes) -> dict:
        """Deserialitza bytes en dict de tensors (format codec genèric)."""

    @abc.abstractmethod
    def validate_adapter_schema(self, data: bytes, expected_sha: str | None = None):
        """Valida el schema del bundle SENSE carregar-lo: SHA (si es dóna),
        tensors (noms/shapes/dtypes) contra el model carregat. REJECTED si
        falta/sobra cap tensor o els shapes/dtypes no coincideixen."""

    # ── cicle de vida ────────────────────────────────────────────────────
    @abc.abstractmethod
    def close(self):
        """Allibera recursos (connexions, memòria, fitxers)."""
