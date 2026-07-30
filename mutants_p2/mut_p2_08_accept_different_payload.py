import sys; sys.path.insert(0,".")
with open("rc5_2_http_server.py") as f: c = f.read()
c = c.replace('if r2: raise ValueError("Same key, different payload")',
    "if r2: pass  # accept different payload")
with open("rc5_2_http_server.py","w") as f: f.write(c)
