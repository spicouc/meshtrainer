PATTERN = "WHERE status='ACTIVE' AND run_id=? AND round_id=?"
SUBST = "WHERE status IN ('ACTIVE','SUPERSEDED') AND run_id=? AND round_id=?"
with open("rc5_3_multiworker.py") as f: c = f.read()
assert c.count(PATTERN) >= 1, "pattern not found"
c = c.replace(PATTERN, SUBST, 1)
with open("rc5_3_multiworker.py","w") as f: f.write(c)
