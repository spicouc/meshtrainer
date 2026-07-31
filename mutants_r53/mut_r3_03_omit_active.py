with open("rc5_3_multiworker.py") as f: c = f.read()
c = c.replace('WHERE status=\'ACTIVE\' AND run_id=? AND round_id=?',
              'WHERE status=\'ACTIVE\' AND run_id=? AND round_id=? LIMIT 1')
with open("rc5_3_multiworker.py","w") as f: f.write(c)
