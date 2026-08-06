#!/usr/bin/env python3
# MUT-R4-13: eliminar run binding (journal_set no valida run_id)
# PATTERN: ("run_id", lease["run_id"] == run_id),
# SUBST: ("run_id", True),
