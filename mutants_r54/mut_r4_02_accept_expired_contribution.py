#!/usr/bin/env python3
# MUT-R4-02: acceptar contribució EXPIRED (journal_set accepta EXPIRED)
# PATTERN: if lease["status"] not in (LEASE_ACTIVE, LEASE_RENEWED):
# SUBST: if lease["status"] not in (LEASE_ACTIVE, LEASE_RENEWED, LEASE_EXPIRED):
