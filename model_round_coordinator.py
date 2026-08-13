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
            CREATE TABLE IF NOT EXISTS model_rounds (
                run_id TEXT NOT NULL,
                round_id TEXT NOT NULL,
                backend_id TEXT NOT NULL,
                base_model_hash TEXT NOT NULL,
                adapter_0_sha TEXT NOT NULL,
                adapter_pre_hash TEXT NOT NULL,
                dataset_manifest_sha TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'OPEN',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (run_id, round_id)
            );
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
                expected_ett INTEGER NOT NULL DEFAULT 0,
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

    # -- rondes (R2.3: el Coordinator és l'autoritat) ----------------------
    def create_round(self, run_id, round_id, backend_id, base_model_hash,
                     adapter_0_sha, adapter_pre_hash, dataset_manifest_sha=""):
        """Operació Coordinator/admin. Un cop creada, base_model_hash,
        adapter_0_sha i backend_id són IMMUTABLES (AUTH-03/05)."""
        ex = self._conn.execute(
            "SELECT * FROM model_rounds WHERE run_id=? AND round_id=?",
            (run_id, round_id)).fetchone()
        if ex is not None:
            # idempotent només si els camps immutables coincideixen
            if (ex["backend_id"] != backend_id
                    or ex["base_model_hash"] != base_model_hash
                    or ex["adapter_0_sha"] != adapter_0_sha):
                raise ModelRecoveryError(
                    "round_immutable_mismatch", run_id=run_id,
                    round_id=round_id,
                    msg="la ronda ja existeix amb camps immutables DIFERENTS — REJECTED")
            return {"run_id": run_id, "round_id": round_id, "status": ex["status"]}
        with self._tx():
            self._conn.execute(
                "INSERT INTO model_rounds (run_id, round_id, backend_id,"
                " base_model_hash, adapter_0_sha, adapter_pre_hash,"
                " dataset_manifest_sha, status) VALUES (?,?,?,?,?,?,?, 'OPEN')",
                (run_id, round_id, backend_id, base_model_hash, adapter_0_sha,
                 adapter_pre_hash, dataset_manifest_sha))
        return {"run_id": run_id, "round_id": round_id, "status": "OPEN"}

    def get_round(self, run_id, round_id):
        row = self._conn.execute(
            "SELECT * FROM model_rounds WHERE run_id=? AND round_id=?",
            (run_id, round_id)).fetchone()
        return dict(row) if row else None

    def verify_round_base(self, run_id, round_id, base_model_hash):
        """Tots els workers de la ronda han de coincidir amb
        model_rounds.base_model_hash (AUTH-04)."""
        r = self.get_round(run_id, round_id)
        if r is None:
            raise ModelRecoveryError("round_not_found", run_id=run_id,
                                     round_id=round_id)
        if r["base_model_hash"] != base_model_hash:
            raise ModelRecoveryError(
                "round_base_model_mismatch",
                expected=r["base_model_hash"][:16],
                got=base_model_hash[:16],
                msg="base model NO és el de la ronda — REJECTED")
        return r

    def verify_round_adapter(self, run_id, round_id, adapter_0_sha):
        """Tots els workers han de partir dels mateixos bytes d'adapter
        (adapter_0_sha de la ronda) — AUTH-05."""
        r = self.get_round(run_id, round_id)
        if r is None:
            raise ModelRecoveryError("round_not_found", run_id=run_id,
                                     round_id=round_id)
        if r["adapter_0_sha"] != adapter_0_sha:
            raise ModelRecoveryError(
                "round_adapter_mismatch",
                expected=r["adapter_0_sha"][:16],
                got=adapter_0_sha[:16],
                msg="adapter_0 NO és el de la ronda — REJECTED")
        return r

    def round_active_contributions(self, run_id, round_id):
        return self._conn.execute(
            "SELECT * FROM model_contributions WHERE run_id=? AND round_id=?"
            " AND status='ACTIVE'", (run_id, round_id)).fetchall()

    def verify_contributions_homogeneous_prehash(self, run_id, round_id):
        """FedAvg NO pot executar si les contribucions ACTIVE no comparteixen
        adapter_pre_hash (AUTH-13)."""
        rows = self.round_active_contributions(run_id, round_id)
        if not rows:
            raise ModelRecoveryError("no_active_contributions")
        first = rows[0]["adapter_pre_hash"]
        for r in rows[1:]:
            if r["adapter_pre_hash"] != first:
                raise ModelRecoveryError(
                    "heterogeneous_adapter_pre_hash",
                    msg="contribucions ACTIVE amb adapter_pre_hash DIFERENTS — "
                        "FedAvg REJECTED")
        return first

    # -- assignacions ------------------------------------------------------
    def create_assignment(self, run_id, round_id, assignment_id, worker_id,
                          shard_id, shard_manifest_sha, base_model_hash,
                          adapter_0_sha, expected_ett, revision=1):
        """Operació Coordinator/admin. L'assignació ha d'existir ABANS que el
        worker es calibra (AUTH-01/02). Un worker NO pot sobreescriure una
        assignació existent (AUTH-14)."""
        ex = self._conn.execute(
            "SELECT * FROM model_assignments WHERE run_id=? AND round_id=?"
            " AND assignment_id=?",
            (run_id, round_id, assignment_id)).fetchone()
        if ex is not None:
            raise ModelRecoveryError(
                "assignment_exists", assignment_id=assignment_id,
                msg="l'assignació ja existeix — un worker NO la pot "
                    "sobreescriure (AUTH-14)")
        with self._tx():
            self._conn.execute(
                "INSERT INTO model_assignments (assignment_id, run_id,"
                " round_id, worker_id, shard_id, shard_manifest_sha,"
                " base_model_hash, adapter_0_sha, ett_registered,"
                " expected_ett, status, revision) VALUES"
                " (?,?,?,?,?,?,?,?,0,?, 'ASSIGNED', ?)",
                (assignment_id, run_id, round_id, worker_id, shard_id,
                 shard_manifest_sha, base_model_hash, adapter_0_sha,
                 expected_ett, revision))
        return {"assignment_id": assignment_id, "status": "ASSIGNED",
                "revision": revision}

    def assign(self, run_id, round_id, assignment_id, worker_id, shard_id,
               shard_manifest_sha, base_model_hash, adapter_0_sha,
               ett_registered=0, revision=None):
        """DEPRECATED (R2.3): només existeix per compatibilitat amb tests
        antics. La via correcta és create_assignment + verify_calibration.
        NO es crida des de worker.calibrate."""
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

    def verify_calibration(self, run_id, round_id, assignment_id, worker_id,
                           shard_id, shard_manifest_sha, base_model_hash,
                           adapter_0_sha, ett):
        """worker.calibrate = VERIFICACIÓ (R2.3, punt 3).

        El Coordinator carrega l'assignació PREEXISTENT (creada per
        assignment.create) i compara TOT: worker_id, base model, adapter,
        shard, manifest i ETT. Qualsevol diferència -> REJECTED.
        NO crea ni modifica cap assignació."""
        a = self.get_assignment(run_id, round_id, assignment_id, worker_id)
        if a is None:
            raise ModelRecoveryError(
                "unknown_assignment", assignment_id=assignment_id,
                msg="l'assignació no existeix (ha de crear-la el Coordinator "
                    "ABANS del calibrate) — REJECTED (AUTH-01/02)")
        if a["worker_id"] != worker_id:
            raise ModelRecoveryError(
                "worker_mismatch", assignment_id=assignment_id,
                expected=a["worker_id"], got=worker_id)
        if a["base_model_hash"] != base_model_hash:
            raise ModelRecoveryError(
                "base_model_mismatch", assignment_id=assignment_id,
                expected=a["base_model_hash"][:16],
                got=base_model_hash[:16],
                msg="base model NO coincideix amb l'assignació — REJECTED")
        if a["adapter_0_sha"] != adapter_0_sha:
            raise ModelRecoveryError(
                "adapter_0_sha_mismatch", assignment_id=assignment_id,
                expected=a["adapter_0_sha"][:16],
                got=adapter_0_sha[:16],
                msg="adapter_0 NO coincideix amb l'assignació — REJECTED (AUTH-05)")
        if a["shard_id"] != shard_id:
            raise ModelRecoveryError(
                "shard_mismatch", assignment_id=assignment_id,
                expected=a["shard_id"], got=shard_id,
                msg="shard NO coincideix amb l'assignació — REJECTED (AUTH-07)")
        if a["shard_manifest_sha"] != shard_manifest_sha:
            raise ModelRecoveryError(
                "shard_manifest_mismatch", assignment_id=assignment_id,
                expected=a["shard_manifest_sha"][:16],
                got=shard_manifest_sha[:16],
                msg="shard manifest NO coincideix — REJECTED (AUTH-08)")
        if a["expected_ett"] and a["expected_ett"] != ett:
            raise ModelRecoveryError(
                "ett_mismatch", assignment_id=assignment_id,
                expected=a["expected_ett"], got=ett,
                msg="ETT NO coincideix amb el plan (expected_ett) — "
                    "REJECTED (AUTH-09/10)")
        # marquem l'ETT verificat (idempotent)
        if a["ett_registered"] != ett:
            with self._tx():
                self._conn.execute(
                    "UPDATE model_assignments SET ett_registered=?,"
                    " status='CALIBRATED' WHERE run_id=? AND round_id=?"
                    " AND assignment_id=?",
                    (ett, run_id, round_id, assignment_id))
            a = self.get_assignment(run_id, round_id, assignment_id, worker_id)
        return a

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
        if a["expected_ett"] and a["expected_ett"] != ett:
            raise ModelRecoveryError(
                "ett_mismatch", expected=a["expected_ett"], got=ett)
        if a["ett_registered"] and a["ett_registered"] != ett:
            raise ModelRecoveryError(
                "ett_mismatch", registered=a["ett_registered"], got=ett)
        return a

    def verify_contribution_pre_hash(self, run_id, round_id, assignment_id,
                                     worker_id, adapter_pre_hash):
        """R2.3 (AUTH-06/12): la contribució ha de partir del mateix estat
        (adapter_pre_hash) que l'assignació/ronda."""
        r = self.get_round(run_id, round_id)
        if r is not None and r["adapter_pre_hash"]:
            if r["adapter_pre_hash"] != adapter_pre_hash:
                raise ModelRecoveryError(
                    "adapter_pre_hash_mismatch",
                    expected=r["adapter_pre_hash"][:16],
                    got=adapter_pre_hash[:16],
                    msg="adapter_pre_hash NO és el baseline de la ronda — "
                        "REJECTED (AUTH-06)")
            return r["adapter_pre_hash"]
        a = self.get_assignment(run_id, round_id, assignment_id, worker_id)
        if a is None:
            raise ModelRecoveryError("unknown_assignment",
                                     assignment_id=assignment_id)
        return a["adapter_0_sha"]

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
        # R2.3 (AUTH-12): el baseline de la contribució ha de ser el de la
        # ronda (adapter_pre_hash global)
        try:
            self.verify_contribution_pre_hash(
                run_id, round_id, assignment_id, worker_id, adapter_pre_hash)
        except ModelRecoveryError as e:
            raise ModelRecoveryError(
                "adapter_pre_hash_mismatch", cid=cid,
                msg=f"baseline de la contribució NO és el de la ronda — "
                    f"REJECTED (AUTH-12): {e.reason}")
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
        """R2.4 (punt 8): QUÒRUM EXACTE.
        - exactament els assignments requerits (cap absent);
        - exactament UNA contribució ACTIVE per assignment/revision vàlida;
        - zero contribucions alienes; zero duplicates efectius.
        Es compten TOTES les assignacions de la ronda (ASSIGNED/CALIBRATED/
        COMPLETED — el calibrate les marca CALIBRATED)."""
        rows = self._conn.execute(
            "SELECT * FROM model_assignments WHERE run_id=? AND round_id=?"
            " AND status IN ('ASSIGNED','CALIBRATED','COMPLETED')",
            (run_id, round_id)).fetchall()
        if not rows:
            raise ModelRecoveryError("no_assignments", run_id=run_id,
                                     round_id=round_id,
                                     msg="cap assignment a la ronda — "
                                         "ROUND_NOT_READY")
        expected = len(rows)
        for a in rows:
            acts = self._conn.execute(
                "SELECT * FROM model_contributions WHERE run_id=? AND"
                " round_id=? AND assignment_id=? AND worker_id=?"
                " AND status='ACTIVE'",
                (run_id, round_id, a["assignment_id"],
                 a["worker_id"])).fetchall()
            # exactament UNA contribució ACTIVE per assignment
            if len(acts) != 1:
                raise ModelRecoveryError(
                    "quorum_not_met", assignment_id=a["assignment_id"],
                    active=len(acts), expected=1,
                    msg=f"quòrum: {len(acts)}/1 contribucions ACTIVE per "
                        f"assignment {a['assignment_id']} — ROUND_NOT_READY")
        # zero contribucions alienes (ACTIVE fora dels assignments de la ronda)
        total_active = self._conn.execute(
            "SELECT COUNT(*) AS n FROM model_contributions WHERE run_id=?"
            " AND round_id=? AND status='ACTIVE'",
            (run_id, round_id)).fetchone()["n"]
        if total_active != expected:
            raise ModelRecoveryError(
                "quorum_alien_contributions",
                active=total_active, expected=expected,
                msg=f"quòrum: {total_active} contribucions ACTIVE, "
                    f"s'esperaven exactament {expected} — ROUND_NOT_READY")
        return {"state": "READY", "assignments": expected,
                "active": total_active}

    def verify_fedavg_baseline(self, run_id, round_id, adapter_0_bytes):
        """R2.4 (punt 9/10): l'adapter base ha de ser l'AUTORITATIU de la
        ronda (model_rounds.adapter_0_sha). sha256(bytes) == sha, si no
        REJECTED (prova d'injecció de baseline)."""
        r = self.get_round(run_id, round_id)
        if r is None:
            raise ModelRecoveryError("round_not_found", run_id=run_id,
                                     round_id=round_id)
        actual = hashlib.sha256(adapter_0_bytes).hexdigest()
        if actual != r["adapter_0_sha"]:
            raise ModelRecoveryError(
                "fedavg_baseline_mismatch",
                expected=r["adapter_0_sha"][:16], got=actual[:16],
                msg="adapter base NO és el de la ronda (injecció de "
                    "baseline) — REJECTED (SEC-07)")
        return r

    def verify_contributions_homogeneous(self, run_id, round_id):
        """R2.4 (punt 11): TOTES les contribucions ACTIVE han de coincidir
        amb la ronda en run_id, round_id, base_model_hash, adapter_pre_hash,
        revision vàlida i assignment vàlid. Heterogeneïtat -> REJECTED."""
        r = self.get_round(run_id, round_id)
        if r is None:
            raise ModelRecoveryError("round_not_found", run_id=run_id,
                                     round_id=round_id)
        rows = self.round_active_contributions(run_id, round_id)
        if not rows:
            raise ModelRecoveryError("no_active_contributions")
        for c in rows:
            a = self._conn.execute(
                "SELECT * FROM model_assignments WHERE run_id=? AND round_id=?"
                " AND assignment_id=? AND worker_id=?",
                (run_id, round_id, c["assignment_id"],
                 c["worker_id"])).fetchone()
            if a is None:
                raise ModelRecoveryError(
                    "contribution_alien_assignment", cid=c["cid"],
                    msg="contribució ACTIVE sense assignment vàlid — REJECTED")
            if c["adapter_pre_hash"] != r["adapter_pre_hash"]:
                raise ModelRecoveryError(
                    "heterogeneous_adapter_pre_hash", cid=c["cid"],
                    msg="adapter_pre_hash de la contribució ≠ ronda — "
                        "REJECTED (SEC-08)")
            # el base model de l'assignació ha de ser el de la ronda
            if a["base_model_hash"] != r["base_model_hash"]:
                raise ModelRecoveryError(
                    "heterogeneous_base_model", cid=c["cid"],
                    msg="base model de l'assignació ≠ ronda — REJECTED (SEC-09)")
        return {"round": dict(r), "contributions": len(rows)}

    # -- FedAvg (model-agnostic via adapter_codec) -------------------------
    def active_deltas(self, run_id, round_id):
        """R2.4 (punt 12): l'ETT usat pel FedAvg és el del Coordinator
        (expected_ett de l'assignació), MAI cap camp lliure del request."""
        return self._conn.execute(
            "SELECT c.delta_bundle_b64, c.delta_bundle_sha256,"
            " COALESCE(a.expected_ett, c.ett) AS ett"
            " FROM model_contributions c"
            " LEFT JOIN model_assignments a ON a.run_id=c.run_id"
            "  AND a.round_id=c.round_id AND a.assignment_id=c.assignment_id"
            "  AND a.worker_id=c.worker_id"
            " WHERE c.status='ACTIVE' AND c.run_id=? AND c.round_id=?",
            (run_id, round_id)).fetchall()

    def fedavg(self, run_id, round_id, adapter_0_bundle_b64: str):
        """adapter_1 = adapter_0 + Σ(delta_i * ETT_i) / ΣETT_i (float64).
        R2.4 (punt 7/9/11/12): abans d'agregar executa OBLIGATÒRIAMENT:
          1) ready_to_close() — quòrum exacte (SEC-06, Exploit B);
          2) baseline autoritatiu: sha256(adapter_0) == model_rounds.adapter_0_sha
             (SEC-07, Exploit C — no confia en bytes arbitraris del caller);
          3) homogeneïtat completa de contribucions (SEC-08/09);
          4) ETT autoritzat (expected_ett/validated_ett del Coordinator,
             mai cap camp lliure del request)."""
        # 1) quòrum exacte — REJECTED si no ready (mai genera adapter)
        self.ready_to_close(run_id, round_id)
        # 2) baseline autoritatiu (bytes del caller verificats contra la ronda)
        adapter_0_bytes = base64.b64decode(adapter_0_bundle_b64)
        rnd = self.verify_fedavg_baseline(run_id, round_id, adapter_0_bytes)
        # 3) homogeneïtat completa (run/round/base/pre_hash/assignment/revision)
        self.verify_contributions_homogeneous(run_id, round_id)
        # 4) ETT: només els ETT validats pel Coordinator (expected_ett de
        #    l'assignació) — els deltes es ponderen amb l'ETT autoritzat
        rows = self.active_deltas(run_id, round_id)
        if not rows:
            raise ModelRecoveryError("no_active_contributions")
        adapter_0 = adapter_codec.unpack_tensors(adapter_0_bytes)
        adapter_0_np = adapter_codec.to_numpy_dict(adapter_0)
        deltas_ett = []
        for r in rows:
            d = adapter_codec.unpack_tensors(
                base64.b64decode(r["delta_bundle_b64"]))
            deltas_ett.append((adapter_codec.to_numpy_dict(d), r["ett"]))
        out = adapter_codec.fedavg(adapter_0_np, deltas_ett)
        adapter_1_bytes = adapter_codec.numpy_dict_to_bundle(out)
        pre_hash = rnd["adapter_pre_hash"]
        return {"adapter_1_b64": adapter_codec.b64(adapter_1_bytes),
                "adapter_1_sha": hashlib.sha256(adapter_1_bytes).hexdigest(),
                "adapter_pre_hash": pre_hash,
                "total_ett": sum(r["ett"] for r in rows),
                "num_contributions": len(rows),
                "round_status": "READY"}

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
