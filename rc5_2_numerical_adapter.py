"""RC5.2 Phase 2 R10: numerical adapter helpers (OUTSIDE the frozen kernel)."""
import torch
import torch.nn as nn
from rc5_2_numerical_profile import PROFILE as P
from rc5_2_numerical_models import SplitServerNumericalModel, WorkerNumericalModel, MonolithicNumericalModel

def full_server_forward(server: SplitServerNumericalModel, tokens, labels, causal_mask):
    """Server pass: server_forward + loss/backward on a fresh cut leaf (Phase 2 helper)."""
    sa = server.server_forward(tokens, causal_mask)
    cut_leaf = sa.detach().clone().requires_grad_(True)
    x = cut_leaf
    for layer in server.top_layers:
        x = layer(x, src_mask=causal_mask)
    logits = server.lm_head(x)
    shift_logits = logits[:, :-1, :].contiguous()
    shift_labels = labels[:, 1:].contiguous()
    loss = nn.functional.cross_entropy(shift_logits.view(-1, P.vocab_size), shift_labels.view(-1), ignore_index=-100)
    cut_grad = torch.autograd.grad(loss, cut_leaf, retain_graph=False)[0].detach().clone()
    return {"server_activation": sa, "cut_activation": cut_leaf.detach(),
            "logits": logits, "loss": loss.item(), "cut_gradient": cut_grad}

def make_causal_fixture():
    tokens = (torch.arange(P.sequence_length, dtype=torch.long).reshape(1, P.sequence_length) % P.vocab_size).contiguous()
    labels = tokens.clone()
    causal_mask = torch.triu(torch.full((P.sequence_length, P.sequence_length), float('-inf')), diagonal=1)
    return tokens, labels, causal_mask

def ett_from_labels(labels):
    shift = labels[:, 1:].contiguous()
    return int((shift != -100).sum().item())

def build_reference_state():
    torch.manual_seed(P.seed)
    m = MonolithicNumericalModel()
    ref = {name: p.detach().clone() for name, p in m.named_parameters()}
    return ref, m

def load_split_models(ref_state):
    """Load server + worker from a reference state dict (Phase 2 tests helper)."""
    server = SplitServerNumericalModel()
    worker = WorkerNumericalModel()
    server_keys = {n for n, _ in server.named_parameters()}
    worker_keys = {n for n, _ in worker.named_parameters()}
    server_missing = [k for k in ref_state if k in server_keys]
    worker_missing = [k for k in ref_state if k in worker_keys]
    server_unexpected = [k for k in server_keys if k not in ref_state]
    worker_unexpected = [k for k in worker_keys if k not in ref_state]
    for name, p in server.named_parameters():
        if name in ref_state: p.data.copy_(ref_state[name])
    for name, p in worker.named_parameters():
        if name in ref_state: p.data.copy_(ref_state[name])
    return server, worker, {
        "server_missing": server_missing, "worker_missing": worker_missing,
        "server_unexpected": server_unexpected, "worker_unexpected": worker_unexpected,
    }

def split_step(server, worker, tokens, labels, causal_mask):
    """One full split step: forward + backward + optimizer step + delta."""
    sa = server.server_forward(tokens, causal_mask)
    cut = worker.worker_forward(sa, causal_mask)
    res = server.server_loss_and_backward(cut, labels, causal_mask)
    cg = res["cut_gradient"]
    pre_A = worker.lora_wrapper.lora_A.weight.detach().clone()
    pre_B = worker.lora_wrapper.lora_B.weight.detach().clone()
    worker.worker_backward(cut, cg)
    post_A = worker.lora_wrapper.lora_A.weight.detach().clone()
    post_B = worker.lora_wrapper.lora_B.weight.detach().clone()
    return {
        "server_activation": sa, "cut_activation": cut, "loss": res["loss"],
        "cut_gradient": cg, "pre_A": pre_A, "pre_B": pre_B,
        "post_A": post_A, "post_B": post_B,
        "delta_A": post_A - pre_A, "delta_B": post_B - pre_B,
    }

def mono_step(ref_model, tokens, labels, causal_mask):
    """One monolithic step for oracle comparison."""
    import copy
    m = copy.deepcopy(ref_model)
    opt = torch.optim.SGD([m.lora_wrapper.lora_A.weight, m.lora_wrapper.lora_B.weight], lr=0.01)
    opt.zero_grad(set_to_none=True)
    out = m.forward(tokens, labels, causal_mask, return_all=True)
    loss = out["loss"]
    grad_A = m.lora_wrapper.lora_A.weight.grad.detach().clone()
    grad_B = m.lora_wrapper.lora_B.weight.grad.detach().clone()
    pre_A = m.lora_wrapper.lora_A.weight.detach().clone()
    pre_B = m.lora_wrapper.lora_B.weight.detach().clone()
    opt.step()
    post_A = m.lora_wrapper.lora_A.weight.detach().clone()
    post_B = m.lora_wrapper.lora_B.weight.detach().clone()
    return {
        "server_activation": out.get("server_activation"), "cut_activation": out.get("cut_activation"),
        "loss": loss, "grad_A": grad_A, "grad_B": grad_B,
        "pre_A": pre_A, "pre_B": pre_B, "post_A": post_A, "post_B": post_B,
        "delta_A": post_A - pre_A, "delta_B": post_B - pre_B,
    }
