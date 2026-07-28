# RC5.1 — Tiny Split PoC — Informe Final P0

**Commit:** 8cc66e31440b29d646b630e35712ef158df50792
**Branca:** rc5.1-p0-corrective
**Data:** 2026-07-28

## Estat del gate

Protocol primitives: PARTIAL PASS
Receipt/delta integrity: PASS
Ledger revision semantics: PASS
Numerical split learning: NOT VALIDATED
Tensor FedAvg: NOT IMPLEMENTED
RC5.1 global gate: OPEN

## Resultats

Run 1: 33/33 PASS
Run 2: 33/33 PASS
Timeouts: 0
Flaky: 0
Mandatory skips: 0

## Correccions aplicades (T-00 a T-08)

T-00: Dependencies RC3 incloses al paquet. Funciona des de checkout net.
T-01: Delta LoRA capturat abans de optimizer.step. `lora_delta.abs().sum() > 0`.
T-02: delta_sha256 al payload HMAC. Coordinator verifica presencia, format, coincidencia amb parametre i amb bytes reals.
T-03: SUPERSEDED nomes a handle_activate_contribution, no a checkpoint.upload.
T-04: or True eliminat. Hash 64 chars comparat amb hashlib.sha256(delta_bytes).
T-05: Tests de seguretat complets (assignment incorrecte, expiracio, transicio illegal, propietat aliena).
T-06: COUNTS dict persistent, validacio PASS == sum(COUNTS.values()).
T-07: Runner amb timeout 180s, set +e, distingeix PASS/TIMEOUT/FAIL.
T-08: Aquest informe.

## Evidencia hash coordinator

El coordinator:
1. Rep delta_b64
2. Decodifica a bytes
3. Calcula SHA-256 dels bytes reals
4. Compara amb receipt.delta_sha256 i params.delta_sha256
5. Desa el fitxer NPZ
6. El SHA del fitxer desat coincideix amb el hash calculat original

Test R4-hash-evidence: `file_sha == our_hash` ✅

## Dades numeriques

FedAvg esperat: (1.0×10 + 3.0×30) / (10+30) = 2.5
FedAvg real: 2.5 (FP64 acumulacio, FP32 resultat)
Max error absolut: < 1e-7

Base adapter: [10.0, 20.0]
Global delta: [2.5, 2.5]
Global adapter esperat: [12.5, 22.5]

## Elements fora d'abast

- split learning numeric real (CrossEntropyLoss)
- TinyClientModel real
- equivalencia numerica model complet vs split
- FedAvg tensorial sobre pesos reals
- calibratge
- persistencia completa a SQLite del ledger (en memoria via patch_coordinator)
- LoRA estandard sense ReLU
- Browser/WebGPU worker (Python reference worker unic)

## Riscos residuals

- El session_id per work.request depen de `_rc5_workers` en memoria
- base_adapter_path real no testejat amb restart
- El PoC utilitza SyntheticTensorGen, no tensors de model real
