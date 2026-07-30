import sys; sys.path.insert(0,".")
with open("rc5_2_numerical_models.py") as f: c = f.read()
idx = c.find("class WorkerNumericalModel")
if idx >= 0:
    idx2 = c.find("self.lora_wrapper = LoRALinear(", idx)
    if idx2 >= 0:
        nl = c.find("\n", idx2)
        c = c[:nl+1] + "        self.lora_wrapper._forward = lambda x: self.lora_wrapper.base_linear(x) + 2.0 * self.lora_wrapper.lora_B(torch.relu(self.lora_wrapper.lora_A(x)))\n" + c[nl+1:]
        with open("rc5_2_numerical_models.py","w") as f: f.write(c)
