#!/usr/bin/env python3
# MUT-R4-05: ignorar lease_nonce (no validar nonce)
# PATTERN: if row["lease_nonce"] != lease_nonce:
# SUBST: if False:
