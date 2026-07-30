
import os
with open("rc5_2_worker_runtime.py") as f: c = f.read()
idx = c.find("def worker_forward")
idx2 = c.find("def worker_backward", idx)
body = c[idx:idx2]
new_body = body.replace("self.worker.worker_forward(activation, mask)", "activation.detach().requires_grad_(True)  # bypass")
if new_body != body:
    c = c[:idx] + new_body + c[idx2:]
    with open("rc5_2_worker_runtime.py","w") as f: f.write(c)
