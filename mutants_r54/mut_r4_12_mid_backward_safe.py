#!/usr/bin/env python3
# MUT-R4-12: recuperar mid-backward com si fos segur (no expirar la lease en crash)
# PATTERN: if row["status"] not in (LEASE_ACTIVE, LEASE_RENEWED):
# SUBST: if False:
