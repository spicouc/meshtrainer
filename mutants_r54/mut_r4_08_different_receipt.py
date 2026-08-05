#!/usr/bin/env python3
# MUT-R4-08: receipt diferent després de restart (sobreescriure receipt sempre)
# PATTERN: receipt_json=CASE WHEN recovery_journal_r54.receipt_json IS NULL THEN excluded.receipt_json ELSE recovery_journal_r54.receipt_json END,
# SUBST: receipt_json=excluded.receipt_json,
