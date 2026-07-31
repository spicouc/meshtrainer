with open("rc5_3_multiworker.py") as f: c = f.read()
c = c.replace('if active < mandatory:', 'if False and active < mandatory:')
with open("rc5_3_multiworker.py","w") as f: f.write(c)
