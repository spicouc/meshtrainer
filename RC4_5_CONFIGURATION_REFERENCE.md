# meshtrainer — Configuration Reference v1.0

## Fitxer de configuracio: `config.yaml`

```yaml
# === OBLIGATORIS ===

model_path: /path/to/qwen3_0_6b   # Path al model Qwen3-0.6B
workers: 2                         # Nombre de workers per ronda (1-8)
rounds: 2                          # Nombre de rondes federades (1-10)

# === OPCIONALS ===

output_dir: /tmp/meshtrainer-release  # Directori de sortida
seed: 42                               # Seed per reproducibilitat
tgfs: true                             # Pujar resultats a TGFS (si token disponible)
checkpoint_path: /path/to/adapter      # Checkpoint LoRA pre-entrenat (opcional)

# === PARAMETRES D'ENTRENAMENT ===

batch_size: 4                          # Mida del batch per worker
learning_rate: 0.0003                  # Learning rate
lora_r: 8                              # Rang LoRA
lora_alpha: 16                          # Alpha LoRA
samples_per_batch: 4                   # Mostres per batch
```

## Valors per defecte

| Parametre | Defecte | Rang valid |
|---|---|---|
| model_path | (obligatori) | Path valid |
| workers | 2 | 1-8 |
| rounds | 2 | 1-10 |
| output_dir | /tmp/meshtrainer-release | Directori amb escriptura |
| seed | 42 | Qualsevol enter |
| tgfs | true | true/false |
| checkpoint_path | null | Path o null |
| batch_size | 4 | 1-64 |
| learning_rate | 0.0003 | 1e-6 a 1e-2 |
| lora_r | 8 | 2-64 |
| lora_alpha | 16 | 2-128 |
| samples_per_batch | 4 | 1-32 |

## Exemples

### CPU (minim)
```yaml
model_path: /models/qwen3_0_6b
workers: 2
rounds: 2
seed: 42
```

### Multi-maquina
```yaml
model_path: /models/qwen3_0_6b
workers: 4
rounds: 3
coordinator_host: 192.168.1.100
coordinator_port: 8791
```
