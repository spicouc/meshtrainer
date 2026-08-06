#!/usr/bin/env python3
# MUT-R4-24: permetre pipeline sense lease (step_open sense _require_lease_params)
# PATTERN: self._require_lease_params(p)
# SUBST: pass  # no lease check
