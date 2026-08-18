PATTERN = 'pre_A = pre_tensors["local_layers.0.linear1.lora_A.weight"].clone()'
SUBST = 'pre_A = torch.zeros(8, 256)  # round2 uses old (zero) adapter'
with open("rc5_3_multiworker.py") as f: c = f.read()
assert c.count(PATTERN) >= 1, "pattern not found"
c = c.replace(PATTERN, SUBST, 1)
with open("rc5_3_multiworker.py","w") as f: f.write(c)
