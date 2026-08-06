#!/usr/bin/env python3
# MUT-R4-22: expiry amb "<" en lloc de "<=" (el moment exacte NO expira)
# PATTERN: AND expires_at <= ?",
# SUBST: AND expires_at < ?",
