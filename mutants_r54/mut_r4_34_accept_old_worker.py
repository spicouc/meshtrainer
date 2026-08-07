#!/usr/bin/env python3
# MUT-R4-34: acceptar old worker després de reassignació (worker_id no comprovat)
# PATTERN: ("worker_id", row["worker_id"] == worker_id),
# SUBST: ("worker_id", True),
