PATTERN = '(cur["assignment_id"], cur["worker_id"], cid)'
SUBST = '(cur["assignment_id"], cur["assignment_id"], cid)'
with open("rc5_3_multiworker.py") as f: c = f.read()
assert c.count(PATTERN) >= 1, "pattern not found"
c = c.replace(PATTERN, SUBST, 1)
with open("rc5_3_multiworker.py","w") as f: f.write(c)
