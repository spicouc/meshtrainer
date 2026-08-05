#!/usr/bin/env python3
# MUT-R4-02: acceptar contribution EXPIRED (eliminar el guard de journal_set)
# PATTERN: if lease["status"] in (LEASE_EXPIRED, LEASE_ABORTED):
# SUBST: if lease["status"] in (LEASE_ABORTED,):
