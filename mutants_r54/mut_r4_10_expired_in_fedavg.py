#!/usr/bin/env python3
# MUT-R4-10: incloure EXPIRED al FedAvg (EXPIRED no reassignable -> es queda al FedAvg)
# PATTERN: return row["status"] in (LEASE_EXPIRED, LEASE_ABORTED)
# SUBST: return row["status"] in (LEASE_ABORTED,)
