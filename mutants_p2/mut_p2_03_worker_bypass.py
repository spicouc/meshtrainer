import sys; sys.path.insert(0,".")
with open("rc5_2_worker_runtime.py") as f: c = f.read()
c = c.replace("self.worker.worker_forward(activation, mask)", "activation.detach().requires_grad_(True)")
with open("rc5_2_worker_runtime.py","w") as f: f.write(c)
