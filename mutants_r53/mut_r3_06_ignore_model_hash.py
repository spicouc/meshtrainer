with open("rc5_3_multiworker.py") as f: c = f.read()
c = c.replace('if p.get(hk, "") != a[hk]:', 'if hk != "worker_model_hash" and p.get(hk, "") != a[hk]:')
with open("rc5_3_multiworker.py","w") as f: f.write(c)
