"""RC5.2 P1-03/04: Monolithic, Split Server, and Worker numerical models."""
import torch
import torch.nn as nn
import torch.nn.functional as F
import copy

from rc5_2_numerical_profile import PROFILE
from rc5_2_lora import LoRALinear

P = PROFILE

def _make_transformer():
    return nn.TransformerEncoderLayer(
        d_model=P.d_model,
        nhead=P.nhead,
        dim_feedforward=P.dim_feedforward,
        dropout=P.dropout,
        activation=P.activation,
        layer_norm_eps=P.layer_norm_eps,
        batch_first=P.batch_first,
        norm_first=P.norm_first,
        bias=P.bias,
    )

class MonolithicNumericalModel(nn.Module):
    def __init__(self):
        super().__init__()
        self.token_embedding = nn.Embedding(P.vocab_size, P.d_model)
        self.pos_embedding = nn.Embedding(P.sequence_length, P.d_model)
        self.base_layers = nn.ModuleList([_make_transformer() for _ in range(P.base_layers)])
        self.local_layers = nn.ModuleList([_make_transformer() for _ in range(P.worker_layers)])
        self.top_layers = nn.ModuleList([_make_transformer() for _ in range(P.top_layers)])
        self.lm_head = nn.Linear(P.d_model, P.vocab_size, bias=True)

        # Wrap local_layers[0].linear1 with LoRA
        orig_linear = self.local_layers[0].linear1
        self.lora_wrapper = LoRALinear(
            base_linear=orig_linear,
            rank=P.lora_rank,
            alpha=P.lora_alpha,
            scaling=P.lora_scaling,
        )
        self.local_layers[0].linear1 = self.lora_wrapper

    def forward(self, tokens, labels, causal_mask, return_all=False):
        B, T = tokens.shape
        pos = torch.arange(T, device=tokens.device).unsqueeze(0)
        x = self.token_embedding(tokens) + self.pos_embedding(pos)
        # Base layers
        for layer in self.base_layers:
            x = layer(x, src_mask=causal_mask)
        server_act = x.detach().clone()
        # Local layers with LoRA
        for layer in self.local_layers:
            x = layer(x, src_mask=causal_mask)
        cut_act = x  # keep reference for gradient
        cut_act.retain_grad()  # ensure grad is retained after backward
        # Top layers
        for layer in self.top_layers:
            x = layer(x, src_mask=causal_mask)
        logits = self.lm_head(x)
        # Loss
        shift_logits = logits[:, :-1, :].contiguous()
        shift_labels = labels[:, 1:].contiguous()
        loss_fn = nn.CrossEntropyLoss(ignore_index=-100)
        loss = loss_fn(shift_logits.view(-1, P.vocab_size), shift_labels.view(-1))
        # Backward
        loss.backward()

        # Capture cut gradient BEFORE optimizer (grad w.r.t. local_layers[0]'s input)
        # local_layers[0] input = base_layers output
        # After loss.backward(), we need gradient at the local_layers entry point
        # For a simple approach, use the first local layer's input grad
        # The first local layer's forward input retains grad if we stored it
        # We'll approximate by reading the gradient of the cut_activation tensor
        cut_grad = cut_act.grad.detach().clone() if cut_act.grad is not None else None

        pre_A = self.lora_wrapper.lora_A.weight.detach().clone()
        pre_B = self.lora_wrapper.lora_B.weight.detach().clone()

        # Capture gradients
        grad_A = self.lora_wrapper.lora_A.weight.grad.detach().clone() if self.lora_wrapper.lora_A.weight.grad is not None else None
        grad_B = self.lora_wrapper.lora_B.weight.grad.detach().clone() if self.lora_wrapper.lora_B.weight.grad is not None else None

        if return_all:
            return {
                "server_activation": server_act,
                "cut_activation": cut_act,
                "logits": logits,
                "loss": loss.item(),
                "shift_logits": shift_logits,
                "shift_labels": shift_labels,
                "cut_gradient": cut_grad,
                "grad_A": grad_A,
                "grad_B": grad_B,
                "pre_A": pre_A,
                "pre_B": pre_B,
            }
        return loss

class SplitServerNumericalModel(nn.Module):
    """Server side: embedding + base + top + LM head + loss."""
    def __init__(self):
        super().__init__()
        self.token_embedding = nn.Embedding(P.vocab_size, P.d_model)
        self.pos_embedding = nn.Embedding(P.sequence_length, P.d_model)
        self.base_layers = nn.ModuleList([_make_transformer() for _ in range(P.base_layers)])
        self.top_layers = nn.ModuleList([_make_transformer() for _ in range(P.top_layers)])
        self.lm_head = nn.Linear(P.d_model, P.vocab_size, bias=True)

    def server_forward(self, tokens, causal_mask):
        B, T = tokens.shape
        pos = torch.arange(T, device=tokens.device).unsqueeze(0)
        x = self.token_embedding(tokens) + self.pos_embedding(pos)
        for layer in self.base_layers:
            x = layer(x, src_mask=causal_mask)
        return x.detach().clone()  # server_activation

    def server_loss_and_backward(self, cut_activation, labels, causal_mask):
        """Take detached cut_activation, re-attach to graph, compute loss & backward."""
        cut_leaf = cut_activation.detach().clone().requires_grad_(True)
        x = cut_leaf
        for layer in self.top_layers:
            x = layer(x, src_mask=causal_mask)
        logits = self.lm_head(x)
        shift_logits = logits[:, :-1, :].contiguous()
        shift_labels = labels[:, 1:].contiguous()
        loss_fn = nn.CrossEntropyLoss(ignore_index=-100)
        loss = loss_fn(shift_logits.view(-1, P.vocab_size), shift_labels.view(-1))
        cut_gradient = torch.autograd.grad(loss, cut_leaf, retain_graph=False)[0].detach().clone()
        return {
            "logits": logits,
            "loss": loss.item(),
            "cut_gradient": cut_gradient,
            "shift_logits": shift_logits,
            "shift_labels": shift_labels,
        }

class WorkerNumericalModel(nn.Module):
    """Worker side: local layers + LoRA + optimizer."""
    def __init__(self):
        super().__init__()
        self.local_layers = nn.ModuleList([_make_transformer() for _ in range(P.worker_layers)])

        # Wrap local_layers[0].linear1 with LoRA
        orig_linear = self.local_layers[0].linear1
        self.lora_wrapper = LoRALinear(
            base_linear=orig_linear,
            rank=P.lora_rank,
            alpha=P.lora_alpha,
            scaling=P.lora_scaling,
        )
        self.local_layers[0].linear1 = self.lora_wrapper

        # Freeze ALL base params (everything except LoRA A/B)
        for name, param in self.named_parameters():
            if "lora" not in name:
                param.requires_grad = False

        self.optimizer = torch.optim.SGD(
            [self.lora_wrapper.lora_A.weight, self.lora_wrapper.lora_B.weight],
            lr=0.01,
            momentum=0.0,
            weight_decay=0.0,
        )

    def worker_forward(self, server_activation, causal_mask):
        """Forward through local layers + LoRA, preserving graph."""
        x = server_activation
        for layer in self.local_layers:
            x = layer(x, src_mask=causal_mask)
        return x  # cut_activation, with grad graph

    def worker_backward(self, cut_activation, cut_gradient):
        """Complete backward and optimizer step."""
        self.optimizer.zero_grad(set_to_none=True)
        pre_A = self.lora_wrapper.lora_A.weight.detach().clone()
        pre_B = self.lora_wrapper.lora_B.weight.detach().clone()

        cut_activation.backward(cut_gradient)

        grad_A = self.lora_wrapper.lora_A.weight.grad.detach().clone() if self.lora_wrapper.lora_A.weight.grad is not None else None
        grad_B = self.lora_wrapper.lora_B.weight.grad.detach().clone() if self.lora_wrapper.lora_B.weight.grad is not None else None

        self.optimizer.step()

        post_A = self.lora_wrapper.lora_A.weight.detach().clone()
        post_B = self.lora_wrapper.lora_B.weight.detach().clone()

        delta_A = post_A - pre_A
        delta_B = post_B - pre_B

        return {
            "pre_A": pre_A,
            "pre_B": pre_B,
            "post_A": post_A,
            "post_B": post_B,
            "delta_A": delta_A,
            "delta_B": delta_B,
            "grad_A": grad_A,
            "grad_B": grad_B,
        }
