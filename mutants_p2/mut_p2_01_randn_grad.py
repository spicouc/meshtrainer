import sys; sys.path.insert(0,".")
with open("rc5_2_http_server.py") as f: c = f.read()
c = c.replace("cut_gradient = server.backward_fetch()", "import torch; cut_gradient = torch.randn_like(torch.zeros(1,128,256))")
with open("rc5_2_http_server.py","w") as f: f.write(c)
