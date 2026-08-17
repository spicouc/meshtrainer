"""DatasetService — registre i validació de datasets (Fase 1)."""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone

from app.datasets.jsonl_adapter import DatasetAdapter, DatasetValidationError
from app.models.schemas import Dataset, DatasetValidationStatus
from app.storage.db import AppDB


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class DatasetService:
    def __init__(self, db: AppDB):
        self.db = db

    def add_dataset(self, source_path: str, name: str = "") -> dict:
        if not os.path.exists(source_path):
            raise DatasetValidationError([f"fitxer no existeix: {source_path}"])
        ds_id = f"ds-{uuid.uuid4().hex[:12]}"
        adapter = DatasetAdapter(source_path)
        try:
            n = adapter.count_examples()
            parse_ok = True
        except DatasetValidationError:
            n = 0
            parse_ok = False
        rec = {
            "dataset_id": ds_id,
            "name": name or os.path.basename(source_path),
            "source_path": os.path.abspath(source_path),
            "format": "jsonl",
            "size": os.path.getsize(source_path),
            "examples": n,
            "train_count": n if parse_ok else 0,
            "validation_count": 0,
            "test_count": 0,
            "schema_json": {"format": "jsonl", "fields": ["instruction", "response"]},
            "created_at": _now(),
            "validation_status": "PENDING",
            "validation_errors": [] if parse_ok else ["JSONL no parseja"],
        }
        self.db.insert_dataset(rec)
        return rec

    def validate_dataset(self, dataset_id: str, tokenize_fn=None,
                         max_seq_len=None) -> dict:
        ds = self.db.get_dataset(dataset_id)
        if not ds:
            raise KeyError(f"dataset no trobat: {dataset_id}")
        adapter = DatasetAdapter(ds["source_path"])
        try:
            res = adapter.validate(tokenize_fn=tokenize_fn,
                                   max_seq_len=max_seq_len)
            errors = res["errors"]
            info = res["info"]
        except DatasetValidationError as e:
            errors = e.errors
            info = {}
        status = "PASS" if not errors else "FAIL"
        fields = {
            "validation_status": status,
            "validation_errors": errors,
            "examples": info.get("examples", ds["examples"]),
            "train_count": info.get("train_count", ds["train_count"]),
            "validation_count": info.get("validation_count", ds["validation_count"]),
            "test_count": info.get("test_count", ds["test_count"]),
            "schema_json": json.dumps(info.get("schema", ds["schema_json"])),
        }
        self.db.update_dataset(dataset_id, **fields)
        return self.db.get_dataset(dataset_id)

    def get_dataset(self, dataset_id: str) -> dict:
        ds = self.db.get_dataset(dataset_id)
        if not ds:
            raise KeyError(f"dataset no trobat: {dataset_id}")
        return ds

    def list_datasets(self) -> list[dict]:
        return self.db.list_datasets()
