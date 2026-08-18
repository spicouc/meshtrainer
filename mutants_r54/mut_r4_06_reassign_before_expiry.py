#!/usr/bin/env python3
# MUT-R4-06: reassignar abans d'expiry (can_reassign sempre True)
# PATTERN: return row["status"] in (LEASE_EXPIRED, LEASE_ABORTED)
# SUBST: return True
