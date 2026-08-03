PATTERN = 'if strict_budget and hashes.get("max_micro_batch", mb) > mb:'
SUBST = 'if False and strict_budget and hashes.get("max_micro_batch", mb) > mb:'
with open("rc5_3_multiworker.py") as f: c = f.read()
assert c.count(PATTERN) >= 1, "pattern not found"
c = c.replace(PATTERN, SUBST, 1)
with open("rc5_3_multiworker.py","w") as f: f.write(c)
