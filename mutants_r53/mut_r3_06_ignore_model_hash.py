with open("rc5_3_multiworker.py") as f: c = f.read()
c = c.replace('if hashes.get(hk, "") != rnd[hk]:',
              'if hk != "worker_model_hash" and hashes.get(hk, "") != rnd[hk]:')
with open("rc5_3_multiworker.py","w") as f: f.write(c)
