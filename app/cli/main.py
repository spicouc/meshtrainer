#!/usr/bin/env python3
"""CLI MVP — meshtrainer (Fase 1). Mateixa application layer que l'API.

Subcomandes:
  backend list
  dataset add|validate|list
  job create|validate|start|status|logs|cancel|artifacts
"""
from __future__ import annotations

import argparse
import json
import os
import sys

_CORE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
if _CORE_DIR not in sys.path:
    sys.path.insert(0, _CORE_DIR)

from app.services.mesh_trainer_service import MeshTrainerService  # noqa: E402


def _p(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))


def main():
    ap = argparse.ArgumentParser(prog="meshtrainer",
                                 description="MeshTrainer App MVP (Fase 1)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    # backend
    p = sub.add_parser("backend", help="gestió de backends")
    bs = p.add_subparsers(dest="sub", required=True)
    bs.add_parser("list", help="llista backends disponibles")

    # dataset
    p = sub.add_parser("dataset", help="gestió de datasets")
    ds = p.add_subparsers(dest="sub", required=True)
    d_add = ds.add_parser("add", help="registra un dataset JSONL")
    d_add.add_argument("path")
    d_add.add_argument("--name", default="")
    d_val = ds.add_parser("validate", help="valida un dataset")
    d_val.add_argument("dataset_id")
    ds.add_parser("list", help="llista datasets")

    # job
    p = sub.add_parser("job", help="gestió de jobs")
    js = p.add_subparsers(dest="sub", required=True)
    j_create = js.add_parser("create", help="crea un job")
    j_create.add_argument("--name", required=True)
    j_create.add_argument("--backend", required=True)
    j_create.add_argument("--model", required=True)
    j_create.add_argument("--dataset", required=True)
    j_create.add_argument("--lora-rank", type=int, default=8)
    j_create.add_argument("--lora-alpha", type=int, default=16)
    j_create.add_argument("--lora-dropout", type=float, default=0.05)
    j_create.add_argument("--lr", type=float, default=1e-4)
    j_create.add_argument("--rounds", type=int, default=1)
    j_create.add_argument("--seq-len", type=int, default=64)
    j_create.add_argument("--workers", type=int, default=2)
    j_validate = js.add_parser("validate", help="valida un job")
    j_validate.add_argument("job_id")
    j_start = js.add_parser("start", help="inicia un job")
    j_start.add_argument("job_id")
    j_status = js.add_parser("status", help="estat d'un job")
    j_status.add_argument("job_id")
    j_status.add_argument("--follow", action="store_true")
    j_logs = js.add_parser("logs", help="logs d'un job")
    j_logs.add_argument("job_id")
    j_logs.add_argument("--worker", default="")
    j_cancel = js.add_parser("cancel", help="cancela un job")
    j_cancel.add_argument("job_id")
    j_art = js.add_parser("artifacts", help="artefactes d'un job")
    j_art.add_argument("job_id")
    js.add_parser("list", help="llista jobs")

    args = ap.parse_args()
    svc = MeshTrainerService()

    try:
        if args.cmd == "backend":
            if args.sub == "list":
                _p([{"id": b.id, "display_name": b.display_name,
                     "available": b.available,
                     "deps": b.required_dependencies}
                    for b in svc.discover_backends()])
        elif args.cmd == "dataset":
            if args.sub == "add":
                _p(svc.add_dataset(args.path, args.name))
            elif args.sub == "validate":
                _p(svc.validate_dataset(args.dataset_id))
            elif args.sub == "list":
                _p(svc.list_datasets())
        elif args.cmd == "job":
            if args.sub == "create":
                _p(svc.create_training_job(
                    args.name, args.backend, args.model, args.dataset,
                    {"lora_rank": args.lora_rank, "lora_alpha": args.lora_alpha,
                     "lora_dropout": args.lora_dropout,
                     "learning_rate": args.lr, "rounds": args.rounds,
                     "max_seq_len": args.seq_len},
                    {"workers": args.workers}))
            elif args.sub == "validate":
                _p(svc.validate_job(args.job_id))
            elif args.sub == "start":
                _p(svc.start_job(args.job_id))
            elif args.sub == "status":
                _p(svc.get_job(args.job_id))
            elif args.sub == "logs":
                _p(svc.get_logs(args.job_id, worker=args.worker))
            elif args.sub == "cancel":
                _p(svc.cancel_job(args.job_id))
            elif args.sub == "artifacts":
                _p(svc.list_artifacts(args.job_id))
            elif args.sub == "list":
                _p(svc.list_jobs())
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
