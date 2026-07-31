
with open("rc5_3_multiworker.py") as f: c = f.read()
c = c.replace("post_A, post_B = pre_A + dA, pre_B + dB",
              "post_A, post_B = pre_A + dA + dA, pre_B + dB + dB")
with open("rc5_3_multiworker.py","w") as f: f.write(c)
