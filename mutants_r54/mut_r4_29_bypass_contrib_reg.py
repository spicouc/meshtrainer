#!/usr/bin/env python3
# MUT-R4-29: bypass lease a contribution registration (no _check_lease)
# PATTERN: self._check_lease(lease_id, lease_nonce, worker_id, session_id, run_id, round_id, assignment_id, micro_unit_id)
# SUBST: pass  # self._check_lease(lease_id, lease_nonce, worker_id, session_id, run_id, round_id, assignment_id, micro_unit_id)
