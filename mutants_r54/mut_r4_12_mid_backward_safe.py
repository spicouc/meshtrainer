#!/usr/bin/env python3
# MUT-R4-12: recuperar mid-backward com a segur (_expire_overdue no expira mai)
# PATTERN: "SELECT lease_id FROM leases_r54 WHERE status IN (?,?) AND expires_at <= ?",
# SUBST: "SELECT lease_id FROM leases_r54 WHERE status IN (?,?) AND expires_at <= ? AND 0",
