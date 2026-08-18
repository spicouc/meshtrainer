#!/usr/bin/env python3
# MUT-R4-17: eliminar session binding
# PATTERN: ("session_id", lease["session_id"] == session_id),
# SUBST: ("session_id", True),
