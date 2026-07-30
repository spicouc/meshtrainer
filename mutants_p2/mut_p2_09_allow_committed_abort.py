import sys; sys.path.insert(0,".")
with open("rc5_2_http_server.py") as f: c = f.read()
c = c.replace('if state in TERMINAL:', 'if False:  # allow any abort')
with open("rc5_2_http_server.py","w") as f: f.write(c)
