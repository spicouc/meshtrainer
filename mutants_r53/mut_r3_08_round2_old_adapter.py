with open("rc5_3_multiworker.py") as f: c = f.read()
c = c.replace('pre_A = pre_tensors["local_layers.0.linear1.lora_A.weight"].clone()',
              'pre_A = torch.zeros(8, 256)  # round2 uses old (zero) adapter')
with open("rc5_3_multiworker.py","w") as f: f.write(c)
