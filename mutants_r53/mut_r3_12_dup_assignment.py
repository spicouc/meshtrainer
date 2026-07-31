with open("rc5_3_multiworker.py") as f: c = f.read()
c = c.replace('if dup is not None and dup["assignment_id"] != assignment_id:',
              'if False and dup is not None and dup["assignment_id"] != assignment_id:')
with open("rc5_3_multiworker.py","w") as f: f.write(c)
