#!/usr/bin/env python3
# MUT-R4-09: acceptar adapter stale (sobreescriure adapter_hash sempre)
# PATTERN: adapter_hash=CASE WHEN recovery_journal_r54.adapter_hash IS NULL THEN excluded.adapter_hash ELSE recovery_journal_r54.adapter_hash END,
# SUBST: adapter_hash=excluded.adapter_hash,
