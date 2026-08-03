# RC5.2 R12 Closeout Report

Branca: rc5.3-stageb (closeout RC5.2 R12 dins el paquet RC5.3 Stage B R5)
Base: a7b7cf87c9c05139b64cc956104063216d8a6ee6
Estat: CERTIFIED (dins el gate combinat Overall: PASS)

## Objectiu

Tancar formalment RC5.2 Phase 2 R12 substituint els runners antics pel
FINAL GATE real que encadena tots els subgates RC5.2/RC5.1 sense recomptes
hardcoded.

## Runners executats (RUN_RC5_2_PHASE2_FINAL_GATE.sh)

1. bash RUN_RC5_2_PHASE2_TESTS.sh — suite completa ×2 (49/49 cadascuna)
2. bash RUN_RC5_2_PHASE2_ADVERSARIAL_GATE.sh — adversarial 21/21
3. bash RUN_RC5_2_PHASE2_MUTATION_GATE.sh — mutation 12/12 (traceback → RUNTIME-INVALID)
4. bash RUN_RC5_2_PHASE1_TESTS.sh — Phase 1 regressions 35/35
5. bash RUN_RC5_2_PHASE1_MUTATION_GATE.sh — Phase 1 mutation
6. bash RUN_RC5_1_TESTS.sh — RC5.1 complet 76/76 ×2 (prohibit substituir per python3 rc5_tests.py)
7. bash RUN_RC5_1_MUTATION_GATE.sh — RC5.1 mutation 5/5
8. controlled-failure RC5.2 — còpia temporal amb 1 assertion trencada → FAIL detectat

## Proves noves afegides a rc5_2_phase2_tests.py (49/49)

- U10 expired receipt: ara usa el unit real COMMITTED u_uc, clona el receipt
  vàlid, només canvia expires_at al passat i el re-signa. El rebuig ha de ser
  PER l'expiració ("expired" al missatge), no per unit inexistent.
- U11 nonce reuse: mateix nonce, receipt_id diferent → rejected.
- U12 SHA mismatch: tot vàlid excepte delta_bundle_sha256 → rejected.
- S1 wrong profile hash: ara exigeix el missatge exacte
  "Numerical profile hash mismatch" (abans passava per "No registered
  assignment", motiu equivocat).

## Mutants corregits

- MUT-P2-07: patró antic (True or sha != receipt) feia sempre cert el check i
  trencava el flux amb traceback → ara elimina la validació SHA i es detecta
  netament amb U12.
- MUT-P2-10: detectat amb U11 (nonce reuse).
- MUT-P2-11: detectat amb U10 (expired amb unit real).
- MUT-P2-12: detectat amb S1 específic del missatge.

## Resultat mutation RC5.2

Score 12/12, Invalid 0, Runtime-invalid 0, Not applied 0, Timeouts 0.

## Tags

- meshtrainer-v1.1-rc5.2 (closeout no certificat, publicat): WITHDRAWN,
  documentat; no es mou silenciosament.
- Creada meshtrainer-v1.1-rc5.2.1 sobre RC5.2 R12 (commit final verificat).
