#!/usr/bin/env python3
# MUT-R4-37: acceptar same logical key + different payload (H-24 falla:
# el payload conflictiu crea una segona operació/unitat)
# PATTERN:                 cached = self._check_idem(lk, sha)
#                 if cached:
#                     return cached
# SUBST:                 cached = None  # accepta sempre, sense REJECTED
#                 if cached:
#                     return cached
