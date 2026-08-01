with open("rc5_3_multiworker.py") as f: c = f.read()
c = c.replace('if len(acts) != 1:', 'if False and len(acts) != 1:')
with open("rc5_3_multiworker.py","w") as f: f.write(c)
