
import os
with open("rc5_2_numerical_models.py") as f: c = f.read()
idx = c.find("class WorkerNumericalModel")
idx2 = c.find("self.lora_wrapper = LoRALinear(", idx)
nl = c.find("\n", idx2)
c = c[:nl+1] + "        self.lora_wrapper.relu_forward = lambda x: self.lora_wrapper.base_linear(x) + 2.0 * self.lora_wrapper.lora_B(torch.relu(self.lora_wrapper.lora_A(x)))\n" + c[nl+1:]
c = c.replace("self.local_layers[0].linear1 = self.lora_wrapper", "self.local_layers[0].linear1 = lambda x: self.lora_wrapper.base_linear(x) + 2.0 * self.lora_wrapper.lora_B(torch.relu(self.lora_wrapper.lora_A(x)))")
with open("rc5_2_numerical_models.py","w") as f: f.write(c)
