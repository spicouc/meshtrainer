#!/usr/bin/env python3
# MUT-R4-35: eliminar _check_idem (el retry exacte no és idempotent:
# H-18/H-19/H-23 fallen perquè s'executa una segona vegada)
# PATTERN:                 cached = self._check_idem(lk, sha)
#                 if cached:
#                     return cached
# SUBST:                 cached = None  # _check_idem eliminat
#                 if cached:
#                     return cached
