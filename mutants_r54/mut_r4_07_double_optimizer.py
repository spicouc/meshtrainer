#!/usr/bin/env python3
# MUT-R4-07: doble optimizer en APPLIED (sobreescriure el delta sempre)
# PATTERN: delta_bundle_sha256=CASE WHEN recovery_journal_r54.delta_bundle_sha256 IS NULL THEN excluded.delta_bundle_sha256 ELSE recovery_journal_r54.delta_bundle_sha256 END,
# SUBST: delta_bundle_sha256=excluded.delta_bundle_sha256,
