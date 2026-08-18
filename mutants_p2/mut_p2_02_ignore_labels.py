
import os
with open("rc5_2_http_server.py") as f: c = f.read()
c = c.replace('labels = tokens.clone()', 'labels = torch.full_like(tokens, -100)')
with open("rc5_2_http_server.py","w") as f: f.write(c)
