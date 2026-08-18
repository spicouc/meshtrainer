"""MUT-P1-06: Insert ReLU between LoRA A and B only in WorkerNumericalModel."""
with open('rc5_2_numerical_models.py') as f:
    code = f.read()

# Find the exact line in WorkerNumericalModel
target = "        self.local_layers[0].linear1 = self.lora_wrapper"
replacement = target + "\n        # mut: ReLU between LoRA A and B (worker only)\n        self.lora_wrapper.forward = lambda x: (\n            self.lora_wrapper.base_linear(x)\n            + self.lora_wrapper.lora_B(torch.relu(self.lora_wrapper.lora_A(x)))\n            * self.lora_wrapper.scaling\n        )"

# Count occurrences and ensure we replace only within WorkerNumericalModel
# Both classes have this line, but only WorkerNumericalModel's is within the right context
# Find the second occurrence (WorkerNumericalModel.__init__ is AFTER MonolithicNumericalModel.__init__)
count = code.count(target)
idx = code.find(target)

# Find the SECOND occurrence (worker, not monolithic)
second_idx = code.find(target, idx + len(target)) if count > 1 else idx

if second_idx >= 0:
    # Verify we're inside WorkerNumericalModel (second occurrence is after WorkerNumericalModel class)
    if second_idx > code.find("class WorkerNumericalModel"):
        code = code[:second_idx] + replacement + code[second_idx + len(target):]
        with open('rc5_2_numerical_models.py', 'w') as f:
            f.write(code)
        print("MUT-06 applied: ReLU in WorkerNumericalModel only")
    else:
        print("ERROR: target line not in WorkerNumericalModel context")
        exit(1)
else:
    print("ERROR: target line not found")
    exit(1)
