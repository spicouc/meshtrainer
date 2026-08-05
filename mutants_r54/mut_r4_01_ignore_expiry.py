#!/usr/bin/env python3
# MUT-R4-01: ignorar expiry (no expirar leases overdue)
# PATTERN: now = self._now_f()
# SUBST: now = self._now_f() - 1e9
