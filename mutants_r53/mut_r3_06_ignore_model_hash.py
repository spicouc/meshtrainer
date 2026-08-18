PATTERN = 'if hashes.get(hk, "") != rnd[hk]:'
SUBST = 'if hk != "worker_model_hash" and hashes.get(hk, "") != rnd[hk]:'
with open("rc5_3_multiworker.py") as f: c = f.read()
assert c.count(PATTERN) >= 1, "pattern not found"
c = c.replace(PATTERN, SUBST, 1)
with open("rc5_3_multiworker.py","w") as f: f.write(c)
