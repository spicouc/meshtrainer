#!/usr/bin/env python3
# MUT-R4-36: consultar cache ABANS del lease gate (retry cachejat amb lease
# expirada és acceptat incorrectament: H-25 falla)
# PATTERN:                 self.recovery._validate_lease(params)
#                 # 2) idempotència: retry exacte -> mateixa resposta
#                 cached = self._check_idem(lk, sha)
#                 if cached:
#                     return cached
# SUBST:                 cached = self._check_idem(lk, sha)
#                 if cached:
#                     return cached
#                 self.recovery._validate_lease(params)
