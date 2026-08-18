#!/usr/bin/env python3
# MUT-R4-23: ignorar rowcount d'expire (expire sense lease retorna OK, no error)
# PATTERN: raise LeaseError(f"No active lease for unit {micro_unit_id}")
# SUBST: return {"unit_id": micro_unit_id, "status": LEASE_EXPIRED}
