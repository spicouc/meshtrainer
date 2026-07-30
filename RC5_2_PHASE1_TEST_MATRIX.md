# RC5.2 Phase 1 R3 — Test Matrix (29 assertions)

## Numerical Equivalence — 17 tests
N01: profile hash stable
N02: monolithic/split state identical, 0 missing/unexpected
N03: server activation equivalent
N04: cut activation equivalent
N05: logits equivalent
N06: loss equivalent
N07: cut gradient equivalent
N08: LoRA-A gradient equivalent
N09: LoRA-B gradient equivalent
N10: optimizer == LoRA only
N14: LoRA-A post-step equivalent
N15: LoRA-B post-step equivalent
N16: delta-A equivalent
N17: delta-B equivalent
N18: delta non-zero
N19: deterministic repeat
N20: two symmetric consecutive steps

## Numerical Equivalence (continued) — 3 tests
N21: loss matches oracle + labels alter loss
N22: ETT == 127
N23: causal mask functional (future unaffected)

## Worker Base Freeze — 3 tests
N11: worker base requires_grad False
N12: worker base grad None after backward
N13: worker base weights unchanged

## Monolithic Base Freeze — 3 tests
N24: monolithic base requires_grad False
N25: monolithic base grad None after backward
N26: monolithic base weights unchanged

## Total: 29 tests, all at strict rtol=1e-5, atol=1e-6
