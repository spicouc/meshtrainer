#!/usr/bin/env python3
# MUT-R4-31: acceptar RELEASED (check_lease — únic amb context de línia anterior)
# PATTERN: raise RecoveryError("lease_binding_mismatch", fields=bad)\n        if row["status"] not in (LEASE_ACTIVE, LEASE_RENEWED):
# SUBST: raise RecoveryError("lease_binding_mismatch", fields=bad)\n        if row["status"] not in (LEASE_ACTIVE, LEASE_RENEWED, LEASE_RELEASED):
