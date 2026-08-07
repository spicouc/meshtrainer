#!/usr/bin/env python3
# MUT-R4-28: bypass lease a upload (dispatcher real)
# PATTERN: if self.rc54_enabled and method in RC54_GATED:
# SUBST: if self.rc54_enabled and method in RC54_GATED and method != "checkpoint.upload":
