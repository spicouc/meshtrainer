
import os
with open("rc5_2_split_server.py") as f: c = f.read()
c = c.replace('result["cut_gradient"]', 'result.get("cut_gradient", torch.zeros(1,128,256)) + torch.randn(1,128,256)')
with open("rc5_2_split_server.py","w") as f: f.write(c)
