"""RC5.2 Phase 1 R2 — Local Numerical Equivalence Tests (24 tests + 6 mutants)."""
import os, sys, json, hashlib, math, copy
import numpy as np
import torch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rc5_2_numerical_profile as _profile
from rc5_2_lora import LoRALinear
from rc5_2_numerical_models import (MonolithicNumericalModel, SplitServerNumericalModel, WorkerNumericalModel)
P = _profile.PROFILE

PASS, FAIL = 0, 0

def check(label, cond):
    global PASS, FAIL
    if cond: PASS += 1; print(f"  ✅ {label}")
    else: FAIL += 1; print(f"  ❌ {label}")

torch.manual_seed(P.seed); np.random.seed(P.seed)
tokens = (torch.arange(P.sequence_length, dtype=torch.long).reshape(1, P.sequence_length) % P.vocab_size).contiguous()
labels = tokens.clone()
causal_mask = torch.triu(torch.full((P.sequence_length, P.sequence_length), float('-inf')), diagonal=1)

def build_reference_state():
    torch.manual_seed(P.seed)
    m = MonolithicNumericalModel()
    # Deterministic init: zero LoRA B, kaiming_uniform A (already done in LoRALinear init)
    ref = {name: p.detach().clone() for name, p in m.named_parameters()}
    return ref, m

STATE_MAP = {
    "token_embedding.weight": "server.token_embedding.weight",
    "pos_embedding.weight": "server.pos_embedding.weight",
    "base_layers.0.*": "server.base_layers.0.*",
    "base_layers.1.*": "server.base_layers.1.*",
    "local_layers.0.*": "worker.local_layers.0.*",
    "local_layers.1.*": "worker.local_layers.1.*",
    "top_layers.0.*": "server.top_layers.0.*",
    "top_layers.1.*": "server.top_layers.1.*",
    "lm_head.weight": "server.lm_head.weight",
    "lm_head.bias": "server.lm_head.bias",
    "lora_wrapper.lora_A.weight": "worker.lora_wrapper.lora_A.weight",
    "lora_wrapper.lora_B.weight": "worker.lora_wrapper.lora_B.weight",
}

SERVER_KEYS = {"token_embedding", "pos_embedding", "base_layers", "top_layers", "lm_head"}
WORKER_KEYS = {"local_layers", "lora_wrapper"}

def load_split_models(ref_state):
    server = SplitServerNumericalModel()
    worker = WorkerNumericalModel()
    server_loaded = {}
    worker_loaded = {}
    for name, param in server.named_parameters():
        if name in ref_state:
            param.data.copy_(ref_state[name].clone())
            server_loaded[name] = True
    for name, param in worker.named_parameters():
        if name in ref_state:
            param.data.copy_(ref_state[name].clone())
            worker_loaded[name] = True
    _verify = {
        "server_missing": [n for n in ref_state if any(s in n for s in SERVER_KEYS) and n not in server_loaded],
        "worker_missing": [n for n in ref_state if any(w in n for w in WORKER_KEYS) and n not in worker_loaded],
        "server_unexpected": [n for n in server_loaded if n not in ref_state],
        "worker_unexpected": [n for n in worker_loaded if n not in ref_state],
    }
    return server, worker, _verify

def run_split_full_flow(ref_state=None):
    if ref_state is None: ref, _ = build_reference_state()
    else: ref = ref_state
    server, worker, v = load_split_models(ref)
    sa = server.server_forward(tokens, causal_mask)
    ca = worker.worker_forward(sa, causal_mask)
    cd = ca.detach().clone()
    r = server.server_loss_and_backward(cd, labels, causal_mask)
    wr = worker.worker_backward(ca, r["cut_gradient"])
    r.update(wr); r["server_activation"] = sa; r["cut_activation"] = cd
    r["_verify"] = v
    return r

def run_mono_full_flow(ref_state=None, step_count=1):
    if ref_state is not None:
        m = MonolithicNumericalModel()
        for name, p in m.named_parameters():
            if name in ref_state: p.data.copy_(ref_state[name])
    else:
        m = MonolithicNumericalModel()
    opt = torch.optim.SGD([m.lora_wrapper.lora_A.weight, m.lora_wrapper.lora_B.weight], lr=0.01, momentum=0.0, weight_decay=0.0)
    result = {}
    for step in range(step_count):
        opt.zero_grad(set_to_none=True)
        r = m.forward(tokens, labels, causal_mask, return_all=True)
        if step == 0:
            result.update(r)
        opt.step()
    result["post_A"] = m.lora_wrapper.lora_A.weight.detach().clone()
    result["post_B"] = m.lora_wrapper.lora_B.weight.detach().clone()
    result["_opt_step_count"] = step_count
    return result

def test_p1_n01():
    h1 = _profile.numerical_profile_hash()
    h2 = _profile.numerical_profile_hash(_profile.NumericalProfileV1())
    check("P1-N01: hash stable", h1 == h2 and len(h1) == 64)

def test_p1_n02():
    """Exact key set comparison: no missing, no unexpected, all equal."""
    ref, _ = build_reference_state()
    server = SplitServerNumericalModel()
    worker = WorkerNumericalModel()
    s_params = dict(server.named_parameters())
    w_params = dict(worker.named_parameters())
    s_keys = set(s_params.keys())
    w_keys = set(w_params.keys())
    r_keys = set(ref.keys())
    server_expected = {k for k in r_keys if any(s in k for s in ["token_embedding","pos_embedding","base_layers","top_layers","lm_head"])}
    worker_expected = {k for k in r_keys if any(w in k for w in ["local_layers","lora_wrapper"])}
    # Load state
    s_state = {}; w_state = {}
    for n,p in server.named_parameters():
        if n in ref: p.data.copy_(ref[n].clone()); s_state[n]=True
    for n,p in worker.named_parameters():
        if n in ref: p.data.copy_(ref[n].clone()); w_state[n]=True
    s_missing = server_expected - set(s_state.keys())
    s_unexpected = set(s_state.keys()) - server_expected
    w_missing = worker_expected - set(w_state.keys())
    w_unexpected = set(w_state.keys()) - worker_expected
    # Verify exact equality
    ok = True
    for n in server_expected:
        if n not in s_state: ok=False
        elif not torch.equal(ref[n], dict(server.named_parameters()).get(n, torch.zeros(1))): ok=False
    for n in worker_expected:
        if n not in w_state: ok=False
        elif not torch.equal(ref[n], dict(worker.named_parameters()).get(n, torch.zeros(1))): ok=False
    ok = ok and len(s_missing)==0 and len(s_unexpected)==0 and len(w_missing)==0 and len(w_unexpected)==0
    check("P1-N02: all state identical, 0 missing, 0 unexpected", ok)

def test_p1_n03():
    ref, _ = build_reference_state()
    server, worker, _ = load_split_models(ref)
    x = MonolithicNumericalModel().token_embedding(tokens) + MonolithicNumericalModel().pos_embedding(torch.arange(P.sequence_length).unsqueeze(0))
    # Rebuild for proper state
    m = MonolithicNumericalModel()
    for n, p in m.named_parameters():
        if n in ref: p.data.copy_(ref[n])
    x = m.token_embedding(tokens) + m.pos_embedding(torch.arange(P.sequence_length).unsqueeze(0))
    for layer in m.base_layers: x = layer(x, src_mask=causal_mask)
    mono_act = x.detach().clone()
    split_act = server.server_forward(tokens, causal_mask)
    check("P1-N03: server activation", torch.allclose(mono_act, split_act, rtol=1e-5, atol=1e-6))

def test_p1_n04():
    ref, _ = build_reference_state()
    sr = run_split_full_flow(ref)
    mr = run_mono_full_flow(ref)
    check("P1-N04: cut activation", torch.allclose(sr["cut_activation"], mr["cut_activation"], rtol=1e-5, atol=1e-6))

def test_p1_n05():
    ref, _ = build_reference_state()
    sr = run_split_full_flow(ref)
    mr = run_mono_full_flow(ref)
    check("P1-N05: logits", torch.allclose(sr["logits"], mr["logits"], rtol=1e-5, atol=1e-6))

def test_p1_n06():
    ref, _ = build_reference_state()
    sr = run_split_full_flow(ref)
    mr = run_mono_full_flow(ref)
    check("P1-N06: loss", abs(sr["loss"] - mr["loss"]) < 1e-6)

def test_p1_n07():
    ref, _ = build_reference_state()
    sr = run_split_full_flow(ref)
    mr = run_mono_full_flow(ref)
    if mr["cut_gradient"] is None: check("P1-N07: cut gradient N/A", False); return
    check("P1-N07: cut gradient", torch.allclose(sr["cut_gradient"], mr["cut_gradient"], rtol=1e-5, atol=1e-6))

def test_p1_n08():
    ref, _ = build_reference_state()
    sr = run_split_full_flow(ref)
    mr = run_mono_full_flow(ref)
    if mr["grad_A"] is None: check("P1-N08: grad A N/A", False); return
    check("P1-N08: LoRA-A gradient", torch.allclose(sr["grad_A"], mr["grad_A"], rtol=1e-5, atol=1e-6))

def test_p1_n09():
    ref, _ = build_reference_state()
    sr = run_split_full_flow(ref)
    mr = run_mono_full_flow(ref)
    if mr["grad_B"] is None: check("P1-N09: grad B N/A", False); return
    check("P1-N09: LoRA-B gradient", torch.allclose(sr["grad_B"], mr["grad_B"], rtol=1e-5, atol=1e-6))

def test_p1_n10():
    _, w, _ = load_split_models(build_reference_state()[0])
    opt_ids = {id(p) for g in w.optimizer.param_groups for p in g["params"]}
    lora_ids = {id(w.lora_wrapper.lora_A.weight), id(w.lora_wrapper.lora_B.weight)}
    check("P1-N10: optimizer == LoRA", opt_ids == lora_ids)

def test_p1_n11():
    _, w, _ = load_split_models(build_reference_state()[0])
    base = [p for n, p in w.named_parameters() if "lora" not in n]
    ok = all(not p.requires_grad for p in base)
    check("P1-N11: base requires_grad False", ok)

def test_p1_n12():
    ref, _ = build_reference_state()
    s, w, _ = load_split_models(ref)
    sa = s.server_forward(tokens, causal_mask)
    ca = w.worker_forward(sa, causal_mask)
    cd = ca.detach().clone()
    r = s.server_loss_and_backward(cd, labels, causal_mask)
    w.worker_backward(ca, r["cut_gradient"])
    base = [p for n, p in w.named_parameters() if "lora" not in n]
    ok = all(p.grad is None for p in base)
    check("P1-N12: base grad None after backward", ok)

def test_p1_n13():
    ref, _ = build_reference_state()
    s, w, _ = load_split_models(ref)
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

def test_p1_n14():
    ref, _ = build_reference_state()
    sr = run_split_full_flow(ref); mr = run_mono_full_flow(ref)
    check("P1-N14: LoRA-A post-step", torch.allclose(sr["post_A"], mr["post_A"], rtol=1e-5, atol=1e-6))

def test_p1_n15():
    ref, _ = build_reference_state()
    sr = run_split_full_flow(ref); mr = run_mono_full_flow(ref)
    check("P1-N15: LoRA-B post-step", torch.allclose(sr["post_B"], mr["post_B"], rtol=1e-5, atol=1e-6))

def test_p1_n16():
    ref, _ = build_reference_state()
    sr = run_split_full_flow(ref); mr = run_mono_full_flow(ref)
    da = (sr["post_A"] - sr["pre_A"]) - (mr["post_A"] - mr["pre_A"])
    check("P1-N16: delta-A", da.abs().max().item() < 1e-6)

def test_p1_n17():
    ref, _ = build_reference_state()
    sr = run_split_full_flow(ref); mr = run_mono_full_flow(ref)
    db = (sr["post_B"] - sr["pre_B"]) - (mr["post_B"] - mr["pre_B"])
    check("P1-N17: delta-B", db.abs().max().item() < 1e-6)

def test_p1_n18():
    ref, _ = build_reference_state()
    sr = run_split_full_flow(ref)
    ok = (sr["post_A"] - sr["pre_A"]).abs().sum().item() > 0 or (sr["post_B"] - sr["pre_B"]).abs().sum().item() > 0
    check("P1-N18: delta non-zero", ok)

def test_p1_n19():
    ref, _ = build_reference_state()
    r1 = run_split_full_flow(ref)
    ref2, _ = build_reference_state()
    r2 = run_split_full_flow(ref2)
    check("P1-N19: deterministic", torch.equal(r1["post_A"], r2["post_A"]) and torch.equal(r1["post_B"], r2["post_B"]))

def test_p1_n20():
    """Two symmetric steps: both paths execute zero_grad → forward → backward → step, twice."""
    ref, _ = build_reference_state()

    # Split: two steps
    s, w, _ = load_split_models(ref)
    for step_i in range(2):
        sa = s.server_forward(tokens, causal_mask)
        ca = w.worker_forward(sa, causal_mask)
        r = s.server_loss_and_backward(ca.detach().clone(), labels, causal_mask)
        wr = w.worker_backward(ca, r["cut_gradient"])
    split_post_A = wr["post_A"]; split_post_B = wr["post_B"]

    # Monolithic: two steps with zero_grad each time
    m = MonolithicNumericalModel()
    for n, p in m.named_parameters():
        if n in ref: p.data.copy_(ref[n])
    opt = torch.optim.SGD([m.lora_wrapper.lora_A.weight, m.lora_wrapper.lora_B.weight], lr=0.01, momentum=0.0, weight_decay=0.0)
    for step_i in range(2):
        opt.zero_grad(set_to_none=True)
        _ = m.forward(tokens, labels, causal_mask, return_all=True)
        opt.step()

    check("P1-N20: step2 LoRA-A", torch.allclose(split_post_A, m.lora_wrapper.lora_A.weight, rtol=1e-5, atol=1e-6))
    check("P1-N20: step2 LoRA-B", torch.allclose(split_post_B, m.lora_wrapper.lora_B.weight, rtol=1e-5, atol=1e-6))

def test_p1_n21():
    """Oracle independent cross-entropy loss."""
    ref, _ = build_reference_state()
    sr = run_split_full_flow(ref)

    # Compute oracle loss from logits and labels
    oracle_shift = sr["logits"][:, :-1, :].contiguous()
    oracle_labels = labels[:, 1:].contiguous()
    oracle_loss = torch.nn.CrossEntropyLoss(ignore_index=-100)(
        oracle_shift.view(-1, P.vocab_size), oracle_labels.view(-1)
    ).item()

    # Also verify that altering labels changes the loss
    bad_labels = labels.clone()
    bad_labels[0, :50] = -100  # mask first 50 tokens
    bad_shift = bad_labels[:, 1:].contiguous()
    bad_loss = torch.nn.CrossEntropyLoss(ignore_index=-100)(
        oracle_shift.view(-1, P.vocab_size), bad_shift.view(-1)
    ).item()

    check("P1-N21: loss matches oracle", abs(sr["loss"] - oracle_loss) < 1e-6)
    check("P1-N21: label change alters loss", abs(bad_loss - oracle_loss) > 1e-6)

def test_p1_n22():
    shift_labels = labels[:, 1:].contiguous()
    check("P1-N22: ETT=127", (shift_labels != -100).sum().item() == 127)

def test_p1_n23():
    """Functional causal mask: future tokens don't affect early logits."""
    ref, _ = build_reference_state()
    s, w, _ = load_split_models(ref)
    k = P.sequence_length // 2  # 64
    # Two sequences: identical up to k, different after k
    tokens_a = (torch.arange(P.sequence_length, dtype=torch.long).reshape(1, P.sequence_length) % P.vocab_size).contiguous()
    tokens_b = tokens_a.clone()
    tokens_b[0, k:] = (tokens_b[0, k:] + 50) % P.vocab_size  # diff after k

    sa_a = s.server_forward(tokens_a, causal_mask)
    ca_a = w.worker_forward(sa_a, causal_mask)
    logits_a = s.server_loss_and_backward(ca_a.detach().clone(), labels, causal_mask)["logits"]

    sa_b = s.server_forward(tokens_b, causal_mask)
    ca_b = w.worker_forward(sa_b, causal_mask)
    logits_b = s.server_loss_and_backward(ca_b.detach().clone(), labels, causal_mask)["logits"]

    # Positions before k must be identical
    early_diff = (logits_a[0, :k] - logits_b[0, :k]).abs().max().item()
    # After k there must be SOME difference (otherwise trivial)
    late_diff = (logits_a[0, k:] - logits_b[0, k:]).abs().max().item()

    check("P1-N23: early positions unaffected by future", early_diff < 1e-6)
    check("P1-N23: late positions differ (non-trivial)", late_diff > 1e-6)
    check("P1-N23: causal_mask[0,1]==-inf", causal_mask[0, 1] == float('-inf'))

def test_p1_n24():
    """Monolithic: all base params frozen (only LoRA trainable)."""
    m = MonolithicNumericalModel()
    ok = all(not p.requires_grad for n, p in m.named_parameters() if "lora" not in n)
    ok = ok and all(p.requires_grad for n, p in m.named_parameters() if "lora" in n)
    check("P1-N24: monolithic base frozen", ok)

def test_p1_n25():
    """Monolithic: base gradients None after backward."""
    m = MonolithicNumericalModel()
    ref = {n: p.detach().clone() for n, p in m.named_parameters()}
    _ = m.forward(tokens, labels, causal_mask, return_all=True)
    base = [p for n, p in m.named_parameters() if "lora" not in n]
    ok = all(p.grad is None for p in base)
    check("P1-N25: monolithic base grad None", ok)

def test_p1_n26():
    """Monolithic: base weights unchanged after backward + step."""
    m = MonolithicNumericalModel()
    ref = {n: p.detach().clone() for n, p in m.named_parameters()}
    _ = m.forward(tokens, labels, causal_mask, return_all=True)
    opt = torch.optim.SGD([m.lora_wrapper.lora_A.weight, m.lora_wrapper.lora_B.weight], lr=0.01)
    opt.step()
    ok = all(torch.equal(ref[n], p) for n, p in m.named_parameters() if "lora" not in n)
    check("P1-N26: monolithic base unchanged", ok)

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
    ("P1-N23", test_p1_n23), ("P1-N24", test_p1_n24), ("P1-N25", test_p1_n25), ("P1-N26", test_p1_n26),
]

if __name__ == "__main__":
    print("=" * 60)
    print("  RC5.2 Phase 1 R3 — Numerical Tests")
    print("=" * 60)
    for name, fn in TESTS:
        print(f"\n--- {name} ---")
        try:
            fn()
        except Exception as e:
            import traceback
            FAIL += 1
            print(f"  ❌ {name}: {e}")
            traceback.print_exc()
    print(f"\n{'='*60}")
    print(f"  Resultat: {PASS}/{PASS+FAIL} PASS, {FAIL} FAIL")
    print(f"{'='*60}")
    sys.exit(0 if FAIL == 0 else 1)
