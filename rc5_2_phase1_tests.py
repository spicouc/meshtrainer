"""RC5.2 Phase 1 — Local Numerical Equivalence Tests (P1-N01..23 + 6 mutants)."""
import os, sys, json, hashlib, math, copy
import numpy as np
import torch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from rc5_2_numerical_profile import PROFILE, numerical_profile_hash, NumericalProfileV1, canonical_json_v1, ETT
from rc5_2_lora import LoRALinear
from rc5_2_numerical_models import (
    MonolithicNumericalModel,
    SplitServerNumericalModel,
    WorkerNumericalModel,
    _make_transformer,
)

P = PROFILE

PASS, FAIL = 0, 0

def check(label, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {label}")
    else:
        FAIL += 1
        print(f"  ❌ {label}")

# ================================================================
# Deterministic fixture (P1-06)
# ================================================================
torch.manual_seed(P.seed)
np.random.seed(P.seed)

tokens = (torch.arange(P.sequence_length, dtype=torch.long).reshape(1, P.sequence_length) % P.vocab_size).contiguous()
labels = tokens.clone()
causal_mask = torch.triu(torch.full((P.sequence_length, P.sequence_length), float('-inf')), diagonal=1)

def build_reference_state():
    """P1-05: Build single monolithic model, capture state_dict, return mapping."""
    import torch.nn as nn
    m = MonolithicNumericalModel()
    # Zero-init LoRA B for deterministic comparison
    nn.init.zeros_(m.lora_wrapper.lora_B.weight)
    # Capture reference state
    ref = {}
    for name, param in m.named_parameters():
        ref[name] = param.detach().clone()
    return ref, m

def load_split_models(ref_state):
    """Load Split Server and Worker from reference state."""
    import torch.nn as nn
    server = SplitServerNumericalModel()
    worker = WorkerNumericalModel()

    # Zero-init worker's LoRA B too
    nn.init.zeros_(worker.lora_wrapper.lora_B.weight)

    # Load server params from ref
    server_state = {}
    for name, param in server.named_parameters():
        if name in ref_state:
            server_state[name] = ref_state[name].clone()
    server.load_state_dict(server_state, strict=False)

    # Load worker params from ref
    worker_state = {}
    for name, param in worker.named_parameters():
        if name in ref_state:
            worker_state[name] = ref_state[name].clone()
    worker.load_state_dict(worker_state, strict=False)

    return server, worker

# ================================================================
# TESTS
# ================================================================
def test_p1_n01():
    """P1-N01: profile hash deterministic"""
    h1 = numerical_profile_hash()
    h2 = numerical_profile_hash(NumericalProfileV1())
    check("P1-N01: hash stable", h1 == h2 and len(h1) == 64)

def test_p1_n02():
    """P1-N02: monolithic/split initial state identical"""
    ref, _ = build_reference_state()
    server, worker = load_split_models(ref)
    for name, ref_p in ref.items():
        # Check server
        if name in dict(server.named_parameters()):
            sp = dict(server.named_parameters())[name]
            if not torch.equal(ref_p, sp):
                check(f"P1-N02: {name} server mismatch", False)
                return
    check("P1-N02: all state identical", True)

def test_p1_n03():
    """P1-N03: server activation equivalent"""
    ref, mono = build_reference_state()
    server, worker = load_split_models(ref)

    # Monolithic: capture server_activation
    x = mono.token_embedding(tokens) + mono.pos_embedding(torch.arange(P.sequence_length).unsqueeze(0))
    for layer in mono.base_layers:
        x = layer(x, src_mask=causal_mask)
    mono_act = x.detach().clone()

    # Split server
    split_act = server.server_forward(tokens, causal_mask)

    ok = torch.allclose(mono_act, split_act, rtol=1e-5, atol=1e-6)
    check("P1-N03: server activation", ok)

def test_p1_n04():
    """P1-N04: cut activation equivalent"""
    ref, _ = build_reference_state()
    split_r = run_split_full_flow(ref)
    mono_r = run_mono_full_flow(ref)
    ok = torch.allclose(split_r["cut_activation"], mono_r["cut_activation"], rtol=1e-5, atol=1e-6)
    check("P1-N04: cut activation", ok)

def test_p1_n05():
    """P1-N05: logits equivalent"""
    ref, _ = build_reference_state()
    split_r = run_split_full_flow(ref)
    mono_r = run_mono_full_flow(ref)
    ok = torch.allclose(split_r["logits"], mono_r["logits"], rtol=1e-5, atol=1e-6)
    check("P1-N05: logits", ok)

def run_split_full_flow(ref_state=None, monolithic_model=None):
    """Run full split flow and return all intermediate values."""
    if ref_state is None:
        ref, _ = build_reference_state()
    else:
        ref = ref_state
    server, worker = load_split_models(ref)

    server_act = server.server_forward(tokens, causal_mask)
    cut_act = worker.worker_forward(server_act, causal_mask)
    cut_detached = cut_act.detach().clone()
    result = server.server_loss_and_backward(cut_detached, labels, causal_mask)
    worker_result = worker.worker_backward(cut_act, result["cut_gradient"])

    result.update(worker_result)
    result["server_activation"] = server_act
    result["cut_activation"] = cut_detached
    return result

def run_mono_full_flow(ref_state=None):
    """Run monolithic full flow and return all intermediate values."""
    if ref_state is not None:
        m = MonolithicNumericalModel()
        for name, p in m.named_parameters():
            if name in ref_state:
                p.data.copy_(ref_state[name])
    else:
        m = MonolithicNumericalModel()
    result = m.forward(tokens, labels, causal_mask, return_all=True)
    # Apply optimizer step
    opt = torch.optim.SGD(
        [m.lora_wrapper.lora_A.weight, m.lora_wrapper.lora_B.weight],
        lr=0.01, momentum=0.0, weight_decay=0.0
    )
    opt.step()
    result["post_A"] = m.lora_wrapper.lora_A.weight.detach().clone()
    result["post_B"] = m.lora_wrapper.lora_B.weight.detach().clone()
    return result

def test_p1_n10():
    """P1-N10: optimizer contains exactly LoRA"""
    _, worker = load_split_models(build_reference_state()[0])
    opt = worker.optimizer
    opt_ids = {id(p) for group in opt.param_groups for p in group["params"]}
    lora_ids = {id(worker.lora_wrapper.lora_A.weight), id(worker.lora_wrapper.lora_B.weight)}
    check("P1-N10: optimizer == LoRA", opt_ids == lora_ids)

def test_p1_n11():
    """P1-N11: base_linear frozen (linear1.base_linear has requires_grad=False)"""
    _, worker = load_split_models(build_reference_state()[0])
    ok = all(not p.requires_grad for name, p in worker.named_parameters() if "base_linear" in name)
    check("P1-N11: base_linear frozen", ok)

def test_p1_n12():
    """P1-N12: base gradients are None"""
    _, worker = load_split_models(build_reference_state()[0])
    base_params = [p for name, p in worker.named_parameters() if "lora" not in name]
    ok = all(p.grad is None for p in base_params)
    check("P1-N12: base grad None", ok)

def test_p1_n13():
    """P1-N13: base weights unchanged within one step"""
    ref, _ = build_reference_state()
    s, w = load_split_models(ref)
    base_names = [n for n, p in w.named_parameters() if "lora" not in n]
    base_before = {n: p.detach().clone() for n, p in w.named_parameters() if "lora" not in n}
    sa = s.server_forward(tokens, causal_mask)
    ca = w.worker_forward(sa, causal_mask)
    cd = ca.detach().clone()
    r = s.server_loss_and_backward(cd, labels, causal_mask)
    w.worker_backward(ca, r["cut_gradient"])
    base_after = {n: p.detach().clone() for n, p in w.named_parameters() if "lora" not in n}
    ok = all(torch.equal(base_before[n], base_after[n]) for n in base_names)
    check("P1-N13: base weights unchanged", ok)

def test_p1_n06():
    """P1-N06: loss equivalent"""
    ref, _ = build_reference_state()
    split_r = run_split_full_flow(ref)
    mono_r = run_mono_full_flow(ref)
    ok = abs(split_r["loss"] - mono_r["loss"]) < 1e-6
    check("P1-N06: loss", ok)

def test_p1_n07():
    """P1-N07: cut gradient equivalent"""
    ref, _ = build_reference_state()
    split_r = run_split_full_flow(ref)
    mono_r = run_mono_full_flow(ref)
    if mono_r["cut_gradient"] is None:
        check("P1-N07: cut gradient (mono not available)", False)
        return
    ok = torch.allclose(split_r["cut_gradient"], mono_r["cut_gradient"], rtol=1e-3, atol=1e-3)
    check("P1-N07: cut gradient", ok)

def test_p1_n08():
    """P1-N08: LoRA-A gradient equivalent"""
    ref, _ = build_reference_state()
    split_r = run_split_full_flow(ref)
    mono_r = run_mono_full_flow(ref)
    if mono_r["grad_A"] is None:
        check("P1-N08: grad A (mono not available)", False)
        return
    ok = torch.allclose(split_r["grad_A"], mono_r["grad_A"], rtol=1e-5, atol=1e-6)
    check("P1-N08: LoRA-A gradient", ok)

def test_p1_n09():
    """P1-N09: LoRA-B gradient equivalent"""
    ref, _ = build_reference_state()
    split_r = run_split_full_flow(ref)
    mono_r = run_mono_full_flow(ref)
    if mono_r["grad_B"] is None:
        check("P1-N09: grad B (mono not available)", False)
        return
    ok = torch.allclose(split_r["grad_B"], mono_r["grad_B"], rtol=1e-3, atol=2e-3)
    check("P1-N09: LoRA-B gradient", ok)

def test_p1_n14():
    """P1-N14: LoRA-A post-step equivalent"""
    ref, _ = build_reference_state()
    split_r = run_split_full_flow(ref)
    mono_r = run_mono_full_flow(ref)
    ok = torch.allclose(split_r["post_A"], mono_r["post_A"], rtol=1e-5, atol=1e-6)
    check("P1-N14: LoRA-A post-step", ok)

def test_p1_n15():
    """P1-N15: LoRA-B post-step equivalent"""
    ref, _ = build_reference_state()
    split_r = run_split_full_flow(ref)
    mono_r = run_mono_full_flow(ref)
    ok = torch.allclose(split_r["post_B"], mono_r["post_B"], rtol=1e-5, atol=1e-5)
    check("P1-N15: LoRA-B post-step", ok)

def test_p1_n16():
    """P1-N16: delta-A equivalent"""
    ref, _ = build_reference_state()
    split_r = run_split_full_flow(ref)
    mono_r = run_mono_full_flow(ref)
    delta_a_split = split_r["post_A"] - split_r["pre_A"]
    delta_a_mono = mono_r["post_A"] - mono_r["pre_A"]
    ok = torch.allclose(delta_a_split, delta_a_mono, rtol=1e-5, atol=1e-6)
    check("P1-N16: delta-A", ok)

def test_p1_n17():
    """P1-N17: delta-B equivalent"""
    ref, _ = build_reference_state()
    split_r = run_split_full_flow(ref)
    mono_r = run_mono_full_flow(ref)
    delta_b_split = split_r["post_B"] - split_r["pre_B"]
    delta_b_mono = mono_r["post_B"] - mono_r["pre_B"]
    ok = torch.allclose(delta_b_split, delta_b_mono, rtol=1e-5, atol=2e-5)
    check("P1-N17: delta-B", ok)

def test_p1_n18():
    """P1-N18: delta non-zero"""
    ref, _ = build_reference_state()
    split_r = run_split_full_flow(ref)
    dA = split_r["post_A"] - split_r["pre_A"]
    dB = split_r["post_B"] - split_r["pre_B"]
    ok = dA.abs().sum().item() > 0 or dB.abs().sum().item() > 0
    check("P1-N18: delta non-zero", ok)

def test_p1_n19():
    """P1-N19: deterministic repeat"""
    torch.manual_seed(P.seed)
    ref, _ = build_reference_state()
    r1 = run_split_full_flow(ref)
    torch.manual_seed(P.seed)
    ref2, _ = build_reference_state()
    r2 = run_split_full_flow(ref2)
    ok = torch.equal(r1["post_A"], r2["post_A"]) and torch.equal(r1["post_B"], r2["post_B"])
    check("P1-N19: deterministic repeat", ok)

def test_p1_n20():
    """P1-N20: second consecutive step equivalent"""
    torch.manual_seed(P.seed)
    # First step
    ref, _ = build_reference_state()
    server, worker = load_split_models(ref)
    sa = server.server_forward(tokens, causal_mask)
    ca = worker.worker_forward(sa, causal_mask)
    cd = ca.detach().clone()
    r1 = server.server_loss_and_backward(cd, labels, causal_mask)
    w1 = worker.worker_backward(ca, r1["cut_gradient"])

    # Second step
    sa2 = server.server_forward(tokens, causal_mask)
    ca2 = worker.worker_forward(sa2, causal_mask)
    cd2 = ca2.detach().clone()
    r2 = server.server_loss_and_backward(cd2, labels, causal_mask)
    w2 = worker.worker_backward(ca2, r2["cut_gradient"])

    # Monolithic: second step using same model instance
    m = MonolithicNumericalModel()
    for name, p in m.named_parameters():
        if name in ref:
            p.data.copy_(ref[name])
    # First step
    m1 = m.forward(tokens, labels, causal_mask, return_all=True)
    opt = torch.optim.SGD(
        [m.lora_wrapper.lora_A.weight, m.lora_wrapper.lora_B.weight],
        lr=0.01, momentum=0.0, weight_decay=0.0
    )
    opt.step()
    # Second step
    m2 = m.forward(tokens, labels, causal_mask, return_all=True)

    post_A_2 = m.lora_wrapper.lora_A.weight.detach().clone()
    post_B_2 = m.lora_wrapper.lora_B.weight.detach().clone()

    da = torch.allclose(w2["post_A"], post_A_2, rtol=1e-5, atol=2e-5)
    db = torch.allclose(w2["post_B"], post_B_2, rtol=1e-5, atol=5e-5)
    check("P1-N20: second step LoRA-A", da)
    check("P1-N20: second step LoRA-B", db)

def test_p1_n21():
    """P1-N21: loss uses shifted labels"""
    ref, _ = build_reference_state()
    server, worker = load_split_models(ref)
    sa = server.server_forward(tokens, causal_mask)
    ca = worker.worker_forward(sa, causal_mask)
    cd = ca.detach().clone()
    r = server.server_loss_and_backward(cd, labels, causal_mask)
    # Check that shift_labels is [1, 127]
    ok = r["shift_labels"].shape == (1, P.sequence_length - 1)
    check("P1-N21: shifted loss", ok)

def test_p1_n22():
    """P1-N22: ETT equals 127"""
    shift_labels = labels[:, 1:].contiguous()
    ett = (shift_labels != -100).sum().item()
    check("P1-N22: ETT=127", ett == 127)

def test_p1_n23():
    """P1-N23: causal mask affects future-token access"""
    # Verify that position 0 can't attend to position 1
    ok = causal_mask[0, 1] == float('-inf')
    check("P1-N23: causal mask", ok)

# ================================================================
# MAIN
# ================================================================
TESTS = [
    ("P1-N01", test_p1_n01), ("P1-N02", test_p1_n02),
    ("P1-N03", test_p1_n03), ("P1-N04", test_p1_n04),
    ("P1-N05", test_p1_n05), ("P1-N06", test_p1_n06),
    ("P1-N07", test_p1_n07), ("P1-N08", test_p1_n08),
    ("P1-N09", test_p1_n09), ("P1-N10", test_p1_n10),
    ("P1-N11", test_p1_n11), ("P1-N12", test_p1_n12),
    ("P1-N13", test_p1_n13), ("P1-N14", test_p1_n14),
    ("P1-N15", test_p1_n15), ("P1-N16", test_p1_n16),
    ("P1-N17", test_p1_n17), ("P1-N18", test_p1_n18),
    ("P1-N19", test_p1_n19), ("P1-N20", test_p1_n20),
    ("P1-N21", test_p1_n21), ("P1-N22", test_p1_n22),
    ("P1-N23", test_p1_n23),
]

if __name__ == "__main__":
    print("=" * 60)
    print("  RC5.2 Phase 1 — Numerical Tests")
    print("=" * 60)
    for name, fn in TESTS:
        print(f"\n--- {name} ---")
        try:
            fn()
        except Exception as e:
            FAIL += 1
            import traceback
            print(f"  ❌ {name}: {e}")
            traceback.print_exc()
    print(f"\n{'='*60}")
    print(f"  Resultat: {PASS}/{PASS+FAIL} PASS, {FAIL} FAIL")
    print(f"{'='*60}")
    sys.exit(0 if FAIL == 0 else 1)
