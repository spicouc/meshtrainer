import os
with open("rc5_2_split_server.py") as f: c = f.read()
c = c.replace("result['cut_gradient']", "result.get('cg_dummy', result.get('cut_gradient', torch.zeros(1,128,256)))")
# Add alteration after backward
c = c.replace("'cut_gradient': cut_gradient", "'cut_gradient': cut_gradient + 0.1")
with open("rc5_2_split_server.py","w") as f: f.write(c)
