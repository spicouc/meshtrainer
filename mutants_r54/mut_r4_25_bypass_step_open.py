#!/usr/bin/env python3
# MUT-R4-25: bypass lease a step.open (dispatcher real)
# PATTERN: if self.rc54_enabled and method in RC54_GATED:
# SUBST: if self.rc54_enabled and method in RC54_GATED and method != "step.open":
