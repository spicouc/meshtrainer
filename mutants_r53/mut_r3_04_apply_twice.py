PATTERN = "post_A, post_B = pre_A + dA, pre_B + dB"
SUBST = "post_A, post_B = pre_A + dA + dA, pre_B + dB + dB"
with open("rc5_3_multiworker.py") as f: c = f.read()
assert c.count(PATTERN) >= 1, "pattern not found"
c = c.replace(PATTERN, SUBST, 1)
with open("rc5_3_multiworker.py","w") as f: f.write(c)
