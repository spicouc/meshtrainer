
import os
with open("rc5_2_http_server.py") as f: c = f.read()
c = c.replace('if s in TERMINAL:', 'if False:  # allow abort from terminal')
with open("rc5_2_http_server.py","w") as f: f.write(c)
