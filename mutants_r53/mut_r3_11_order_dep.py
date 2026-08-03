PATTERN = 'avg_A += w * tensors["local_layers.0.linear1.lora_A.weight"]'
SUBST = 'avg_A += w * 0.5 * tensors["local_layers.0.linear1.lora_A.weight"]'
with open("rc5_3_multiworker.py") as f: c = f.read()
assert c.count(PATTERN) >= 1, "pattern not found"
c = c.replace(PATTERN, SUBST, 1)
with open("rc5_3_multiworker.py","w") as f: f.write(c)
