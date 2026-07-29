"""RC5.2 P1-02: Standard LoRA module (no activation between A and B)."""
import torch
import torch.nn as nn
import math

class LoRALinear(nn.Module):
    """LoRA applied to a target linear layer.

    output = base_linear(x) + scaling * lora_B(lora_A(x))

    Base weights are frozen. Only lora_A and lora_B are trainable.
    """
    def __init__(self, base_linear: nn.Linear, rank: int, alpha: float, scaling: float):
        super().__init__()
        self.base_linear = base_linear
        self.in_features = base_linear.in_features
        self.out_features = base_linear.out_features
        self.rank = rank
        self.alpha = alpha
        self.scaling = scaling

        # Freeze base weights
        for p in self.base_linear.parameters():
            p.requires_grad = False

        self.lora_A = nn.Linear(self.in_features, rank, bias=False)
        self.lora_B = nn.Linear(rank, self.out_features, bias=False)

        # Initialize
        nn.init.kaiming_uniform_(self.lora_A.weight, a=math.sqrt(5))
        nn.init.zeros_(self.lora_B.weight)

    def forward(self, x):
        base_out = self.base_linear(x)
        lora_out = self.lora_B(self.lora_A(x)) * self.scaling
        return base_out + lora_out

    def get_delta(self):
        """Return delta_A and delta_B tensors."""
        # In a real flow, capture before/after. Here we return the actual weights
        # because this module directly holds them.
        return self.lora_A.weight, self.lora_B.weight
