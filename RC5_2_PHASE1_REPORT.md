# RC5.2 Phase 1 R3 — Final Report

**Commit:** pending mutation gate
**Branch:** rc5.2-numerical-phase1
**Base:** meshtrainer-v1.1-rc5.1

## Corrections R3

| Bloquejador | Correccio |
|---|---|
| Monolithic base params not frozen | `for name, param in self.named_parameters(): if "lora" not in name: param.requires_grad = False` |
| Missing monolithic freeze tests | P1-N24 (frozen), P1-N25 (grad None), P1-N26 (weights unchanged) |
| P1-N02: soft key comparison | Exact set comparison: `server_expected`, `worker_expected`, 0 missing, 0 unexpected |
| P1-N23: not testing future independence | Two sequences (tokens_a, tokens_b), verify early logits identical, late logits differ |
| MUT-01: randn grad | Implemented (detected by N07/N14/N15) |
| MUT-02: server ignores labels | SplitServer only, range-limited sed |
| MUT-03: bypass with crash | Differentiable: `server_activation + 0.0 * (lora_A.sum() + lora_B.sum())` — no traceback |
| MUT-04: extra param in opt | Implemented (detected by N10) |
| MUT-05: alter 1 element of gradient | Implemented (detected by N07/N14/N15) |
| MUT-06: ReLU worker-only | Lambda replacement of `linear1 = lora_wrapper` with ReLU path |
| Runtime-invalid classification | Any traceback → RUNTIME-INVALID (regardless of assertion count) |
| Application count | `grep -q '^1$'` for exact one match |

## Numerical tests: 29/29 PASS (strict rtol=1e-5, atol=1e-6)
