"""model_round_coordinator.py — COORDINATOR GENERIC (R2.1).

Reemplaça el concepte QwenRoundCoordinator per una capa model-agnostic.
El Coordinator NO importa cap backend: treballa amb bundles opacs
(bytes + SHA) i amb adapter_codec per al FedAvg.

Taules (prefís genèric 'model_'):
  model_assignments   (assignment_id, run_id, round_id, worker_id, shard_id,
                       shard_manifest_sha, base_model_hash, adapter_0_sha,
                       ett_registered, status, revision)
  model_contributions (cid, run_id, round_id, assignment_id, worker_id,
                       status, ett, delta_bundle_b64, delta_bundle_sha256,
                       request_sha, adapter_pre_hash, source_sha, revision)

Estats de contribució: RECEIVED -> VALIDATED -> ACTIVE (SUPERSEDED quan una
contribució més nova s'activa per a la mateixa assignació).

fedavg(): adapter_1 = adapter_0 + Σ(delta_i * ETT_i) / ΣETT_i (float64),
fórmula idèntica a aggregation.FedAvgLoRA (RC5.3) i a fedavg_qwen, però
calculada amb adapter_codec.fedavg (sense cap import de model).
"""
import base64
import hashlib
import json
import sqlite3

import adapter_codec


class ModelRecoveryError(Exception):
    def __init__(self, reason, **details):
        super().__init__(reason)
        self.reason = reason
        self.details = details


class ModelRoundCoordinator:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn
        self._init_db()

    def _init_db(self):
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS model_assignments (
                assignment_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                round_id TEXT NOT NULL,
                worker_id TEXT NOT NULL,
                shard_id TEXT NOT NULL,
                shard_manifest_sha TEXT NOT NULL,
                base_model_hash TEXT NOT NULL,
                adapter_0_sha TEXT NOT NULL,
                ett_registered INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'ASSIGNED',
                revision INTEGER DEFAULT 1,
                PRIMARY KEY (run_id, round_id, assignment_id)
            );
            CREATE TABLE IF NOT EXISTS model_contributions (
                cid TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                round_id TEXT NOT NULL,
                assignment_id TEXT NOT NULL,
                worker_id TEXT NOT NULL,
                status TEXT NOT NULL,
                ett INTEGER NOT NULL,
                delta_bundle_b64 TEXT NOT NULL,
                delta_bundle_sha256 TEXT NOT NULL,
                request_sha TEXT NOT NULL,
                adapter_pre_hash TEXT NOT NULL,
                source_sha TEXT NOT NULL,
                revision INTEGER DEFAULT 1
            );
        """)
        self._conn.commit()

    # -- assignacions ------------------------------------------------------
    def assign(self, run_id, round_id, assignment_id, worker_id, shard_id,
               shard_manifest_sha, base_model_hash, adapter_0_sha,
               ett_registered=0, revision=None):
        rev = revision or 1
        with self._tx():
            self._conn.execute(
                "INSERT OR REPLACE INTO model_assignments (assignment_id,"
                " run_id, round_id, worker_id, shard_id, shard_manifest_sha,"
                " base_model_hash, adapter_0_sha, ett_registered, status,"
                " revision) VALUES (?,?,?,?,?,?,?,?,?, 'ASSIGNED', ?)",
                (assignment_id, run_id, round_id, worker_id, shard_id,
                 shard_manifest_sha, base_model_hash, adapter_0_sha,
                 ett_registered, rev))
        return {"assignment_id": assignment_id, "status": "ASSIGNED",
                "revision": rev}

    def get_assignment(self, run_id, round_id, assignment_id, worker_id):
        row = self._conn.execute(
            "SELECT * FROM model_assignments WHERE run_id=? AND round_id=?"
            " AND assignment_id=? AND worker_id=?",
            (run_id, round_id, assignment_id, worker_id)).fetchone()
        return dict(row) if row else None

    def verify_base_model(self, run_id, round_id, assignment_id, worker_id,
                          base_model_hash):
        a = self.get_assignment(run_id, round_id, assignment_id, worker_id)
        if a is None:
            raise ModelRecoveryError("unknown_assignment",
                                     assignment_id=assignment_id)
        if a["base_model_hash"] != base_model_hash:
            raise ModelRecoveryError(
                "base_model_mismatch",
                expected=a["base_model_hash"][:16],
                got=base_model_hash[:16])
        return a

    def verify_shard(self, run_id, round_id, assignment_id, worker_id,
                     shard_manifest_sha):
        a = self.get_assignment(run_id, round_id, assignment_id, worker_id)
        if a is None:
            raise ModelRecoveryError("unknown_assignment",
                                     assignment_id=assignment_id)
        if a["shard_manifest_sha"] != shard_manifest_sha:
            raise ModelRecoveryError(
                "shard_manifest_mismatch",
                expected=a["shard_manifest_sha"][:16],
                got=shard_manifest_sha[:16])
        return a

    def verify_ett(self, run_id, round_id, assignment_id, worker_id, ett):
        a = self.get_assignment(run_id, round_id, assignment_id, worker_id)
        if a is None:
            raise ModelRecoveryError("unknown_assignment",
                                     assignment_id=assignment_id)
        if a["ett_registered"] != ett:
            raise ModelRecoveryError(
                "ett_mismatch", registered=a["ett_registered"], got=ett)
        return a

    # -- contribucions ------------------------------------------------------
    def register_uploaded_contribution(self, cid, run_id, round_id,
                                       assignment_id, worker_id, ett,
                                       delta_bundle_b64, delta_bundle_sha256,
                                       request_sha, adapter_pre_hash,
                                       source_sha):
        ex = self._conn.execute(
            "SELECT * FROM model_contributions WHERE cid=?",
            (cid,)).fetchone()
        if ex:
            if ex["source_sha"] != source_sha:
                raise ModelRecoveryError("source_sha_mismatch", cid=cid)
            return {"contribution_id": cid, "status": ex["status"]}
        if delta_bundle_sha256 != adapter_codec.bundle_sha256(
                base64.b64decode(delta_bundle_b64)):
            raise ModelRecoveryError("delta_sha_mismatch", cid=cid)
        with self._tx():
            self._conn.execute(
                "INSERT INTO model_contributions (cid, run_id, round_id,"
                " assignment_id, worker_id, status, ett, delta_bundle_b64,"
                " delta_bundle_sha256, request_sha, adapter_pre_hash,"
                " source_sha) VALUES (?,?,?,?,?, 'RECEIVED', ?, ?, ?, ?, ?, ?)",
                (cid, run_id, round_id, assignment_id, worker_id, ett,
                 delta_bundle_b64, delta_bundle_sha256, request_sha,
                 adapter_pre_hash, source_sha))
        return {"contribution_id": cid, "status": "RECEIVED"}

    def validate_contribution(self, cid):
        cur = self._conn.execute(
            "SELECT status FROM model_contributions WHERE cid=?",
            (cid,)).fetchone()
        if cur is None or cur["status"] != "RECEIVED":
            raise ModelRecoveryError("cannot_validate", cid=cid,
                                     status=cur["status"] if cur else "MISSING")
        with self._tx():
            self._conn.execute(
                "UPDATE model_contributions SET status='VALIDATED'"
                " WHERE cid=?", (cid,))
        return {"contribution_id": cid, "status": "VALIDATED"}

    def activate_contribution(self, cid):
        cur = self._conn.execute(
            "SELECT * FROM model_contributions WHERE cid=?",
            (cid,)).fetchone()
        if cur is None or cur["status"] != "VALIDATED":
            raise ModelRecoveryError("cannot_activate", cid=cid)
        with self._tx():
            self._conn.execute(
                "UPDATE model_contributions SET status='SUPERSEDED'"
                " WHERE assignment_id=? AND worker_id=? AND status='ACTIVE'"
                " AND cid!=?", (cur["assignment_id"], cur["worker_id"], cid))
            self._conn.execute(
                "UPDATE model_contributions SET status='ACTIVE' WHERE cid=?",
                (cid,))
        return {"contribution_id": cid, "status": "ACTIVE"}

    def contribution(self, cid):
        row = self._conn.execute(
            "SELECT * FROM model_contributions WHERE cid=?",
            (cid,)).fetchone()
        return dict(row) if row else None

    def ready_to_close(self, run_id, round_id):
        rows = self._conn.execute(
            "SELECT * FROM model_assignments WHERE run_id=? AND round_id=?"
            " AND status='ASSIGNED'", (run_id, round_id)).fetchall()
        for a in rows:
            acts = self._conn.execute(
                "SELECT * FROM model_contributions WHERE run_id=? AND"
                " round_id=? AND assignment_id=? AND worker_id=?"
                " AND status='ACTIVE'",
                (run_id, round_id, a["assignment_id"],
                 a["worker_id"])).fetchall()
            if len(acts) != 1:
                raise ModelRecoveryError(
                    "quorum_not_met", assignment_id=a["assignment_id"],
                    active=len(acts))
        return {"state": "READY"}

    # -- FedAvg (model-agnostic via adapter_codec) -------------------------
    def active_deltas(self, run_id, round_id):
        return self._conn.execute(
            "SELECT delta_bundle_b64, delta_bundle_sha256, ett FROM"
            " model_contributions WHERE status='ACTIVE' AND run_id=? AND"
            " round_id=?", (run_id, round_id)).fetchall()

    def fedavg(self, run_id, round_id, adapter_0_bundle_b64: str):
        """adapter_1 = adapter_0 + Σ(delta_i * ETT_i) / ΣETT_i (float64).
        Fórmula idèntica a aggregation.FedAvgLoRA; càlcul via adapter_codec
        (cap import de model)."""
        rows = self.active_deltas(run_id, round_id)
        if not rows:
            raise ModelRecoveryError("no_active_contributions")
        adapter_0 = adapter_codec.unpack_tensors(
            base64.b64decode(adapter_0_bundle_b64))
        adapter_0_np = adapter_codec.to_numpy_dict(adapter_0)
        deltas_ett = []
        for r in rows:
            d = adapter_codec.unpack_tensors(
                base64.b64decode(r["delta_bundle_b64"]))
            deltas_ett.append((adapter_codec.to_numpy_dict(d), r["ett"]))
        out = adapter_codec.fedavg(adapter_0_np, deltas_ett)
        adapter_1_bytes = adapter_codec.numpy_dict_to_bundle(out)
        return {"adapter_1_b64": adapter_codec.b64(adapter_1_bytes),
                "adapter_1_sha": hashlib.sha256(adapter_1_bytes).hexdigest(),
                "total_ett": sum(r["ett"] for r in rows),
                "num_contributions": len(rows)}

    # -- tx ----------------------------------------------------------------
    def _tx(self):
        class _Ctx:
            def __init__(self, conn):
                self.conn = conn
                self.outer = conn.in_transaction

            def __enter__(self):
                if not self.outer:
                    self.conn.execute("BEGIN IMMEDIATE")
                return self.conn

            def __exit__(self, exc_type, exc, tb):
                if not self.outer:
                    if exc_type is None:
                        self.conn.execute("COMMIT")
                    else:
                        try:
                            self.conn.execute("ROLLBACK")
                        except sqlite3.Error:
                            pass
                return False

        return _Ctx(self._conn)
