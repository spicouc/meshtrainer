"""Models Pydantic de producte (Fase 1). Cap import del core aquí."""
from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import Any, Optional

try:
    from pydantic import BaseModel, Field
except ImportError:  # pragma: no cover
    raise RuntimeError("pydantic requerit per a l'app (pip install pydantic)")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class JobStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    VALIDATING = "VALIDATING"
    READY = "READY"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    RECOVERING = "RECOVERING"
    CANCELLING = "CANCELLING"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"
    COMPLETED = "COMPLETED"


class DatasetValidationStatus(str, enum.Enum):
    PENDING = "PENDING"
    PASS = "PASS"
    FAIL = "FAIL"


class WorkerRuntimeState(str, enum.Enum):
    OFFLINE = "OFFLINE"
    IDLE = "IDLE"
    STARTING = "STARTING"
    TRAINING = "TRAINING"
    SUBMITTING = "SUBMITTING"
    RECOVERING = "RECOVERING"
    FAILED = "FAILED"


class TrainingConfig(BaseModel):
    method: str = "lora"
    lora_rank: int = 8
    lora_alpha: int = 16
    lora_dropout: float = 0.05
    learning_rate: float = 1e-4
    rounds: int = 1
    max_seq_len: int = 64
    seed: int = 42


class WorkerConfig(BaseModel):
    workers: int = 2
    concurrency: int = 1
    device: str = "cpu"
    memory_mb: Optional[int] = None


class TrainingJob(BaseModel):
    job_id: str
    name: str
    status: JobStatus = JobStatus.DRAFT
    backend_id: str
    model_id: str
    revision: str = ""
    local_path: Optional[str] = None
    base_model_hash: str = ""
    dataset_id: str = ""
    training: TrainingConfig = Field(default_factory=TrainingConfig)
    workers_cfg: WorkerConfig = Field(default_factory=WorkerConfig)
    output_dir: str = ""
    last_error: str = ""
    validation: DatasetValidationStatus = DatasetValidationStatus.PENDING
    validation_errors: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=_now)
    started_at: str = ""
    finished_at: str = ""


class Dataset(BaseModel):
    dataset_id: str
    name: str
    source_path: str
    format: str = "jsonl"
    size: int = 0
    examples: int = 0
    train_count: int = 0
    validation_count: int = 0
    test_count: int = 0
    dataset_schema: dict[str, Any] = Field(default_factory=dict, alias="schema")
    created_at: str = Field(default_factory=_now)
    validation_status: DatasetValidationStatus = DatasetValidationStatus.PENDING
    validation_errors: list[str] = Field(default_factory=list)


class WorkerDefinition(BaseModel):
    worker_id: str
    host: str = "127.0.0.1"
    port: int = 0
    backend_capabilities: list[str] = Field(default_factory=list)
    device: str = "cpu"
    memory_mb: Optional[int] = None
    enabled: bool = True


class JobEvent(BaseModel):
    event_id: str = ""
    job_id: str
    ts: str = Field(default_factory=_now)
    type: str
    payload: dict[str, Any] = Field(default_factory=dict)


class Artifact(BaseModel):
    artifact_id: str
    job_id: str
    type: str
    path: str
    sha256: str
    size: int
    round: str = ""
    created_at: str = Field(default_factory=_now)


class Metric(BaseModel):
    metric_id: str = ""
    job_id: str
    round: str = ""
    worker_id: str = ""
    ts: str = Field(default_factory=_now)
    loss: Optional[float] = None
    val_loss: Optional[float] = None
    ppl: Optional[float] = None
    ett: Optional[int] = None
    duration_s: Optional[float] = None


class BackendInfo(BaseModel):
    id: str
    display_name: str
    capabilities: list[str] = Field(default_factory=list)
    required_dependencies: list[str] = Field(default_factory=list)
    available: bool = False
    model_constraints: dict[str, Any] = Field(default_factory=dict)


class ModelInfo(BaseModel):
    model_id: str
    backend_id: str
    local_path: str
    exists: bool
    base_model_hash: str = ""
