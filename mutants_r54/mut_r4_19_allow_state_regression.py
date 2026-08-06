#!/usr/bin/env python3
# MUT-R4-19: permetre state regression (COMMITTED -> PREPARED)
# PATTERN: if state not in allowed and state != cur_state:
# SUBST: if False:
