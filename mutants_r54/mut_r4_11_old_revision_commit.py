#!/usr/bin/env python3
# MUT-R4-11: permetre commit de revision antiga (journal retorna lease més antiga)
# PATTERN: q += " ORDER BY updated_at DESC LIMIT 1"
# SUBST: q += " ORDER BY updated_at ASC LIMIT 1"
