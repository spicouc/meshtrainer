with open("rc5_3_multiworker.py") as f: c = f.read()
c = c.replace('(cur["assignment_id"], cur["worker_id"], cid)', '(cur["assignment_id"], cur["assignment_id"], cid)')
with open("rc5_3_multiworker.py","w") as f: f.write(c)
