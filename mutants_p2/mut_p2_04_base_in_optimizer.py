
import os
with open("rc5_2_numerical_models.py") as f: c = f.read()
c = c.replace("[self.lora_wrapper.lora_A.weight, self.lora_wrapper.lora_B.weight]",
    "[self.lora_wrapper.lora_A.weight, self.lora_wrapper.lora_B.weight, self.local_layers[0].linear2.weight]")
with open("rc5_2_numerical_models.py","w") as f: f.write(c)
