#!/usr/bin/env python3
# MUT-R4-04: permetre renewal aliena (no validar worker/session)
# PATTERN: if row["worker_id"] != worker_id or row["session_id"] != session_id:
# SUBST: if False:
