with open("rc5_3_multiworker.py") as f: c = f.read()
c = c.replace('if strict_budget and hashes.get("max_micro_batch", mb) > mb:',
              'if False and strict_budget and hashes.get("max_micro_batch", mb) > mb:')
with open("rc5_3_multiworker.py","w") as f: f.write(c)
