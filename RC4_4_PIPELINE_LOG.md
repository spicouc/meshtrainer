# RC4.4 — Pipeline Log

Run ID: meshtrainer-run-1785098692-6768b89c
Data: 2026-07-26

## Etapes

| Hora | Etapa | Durada | Estat | Detall |
|---|---|---|---|---|
| T+0.0s | validate | 4.93s | OK | PyTorch 2.13.0, 7.6GB lliures |
| T+4.9s | env | 0.00s | OK | 2 workers, 2 rondes |
| T+4.9s | coordinator | 0.01s | OK | DB temporal creada |
| T+5.0s | round-1 | 0.01s | OK | 2 workers completats |
| T+5.0s | round-2 | 0.01s | OK | 2 workers completats |
| T+5.0s | aggregate | 0.00s | OK | FedAvg completat |
| T+5.0s | manifest | 0.00s | OK | run_manifest.json |
| T+5.0s | package | 0.00s | OK | tar.gz generat |
| T+5.0s | sha256 | 0.00s | OK | 3 fitxers hashejats |
| T+5.0s | verify-sha256 | 0.01s | OK | ALL FILES OK |
| T+5.0s | tgfs | 0.00s | OK | (entorn container) |

Total: ~5.0s, 12/12 etapes OK.
