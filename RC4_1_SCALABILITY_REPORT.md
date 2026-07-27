# RC4.1 — Escalabilitat

**Data:** 2026-07-26
**Projecte:** meshtrainer
**Host:** CT112 (qwen-train, 4 CPU, 8GB RAM)

---

## 1. Configuracions provades

| Workers | Repeticions | Tipus |
|---|---|---|
| 2 | 3 | Referencia (baseline) |
| 4 | 3 | Escala mitjana |
| 8 | 3 | Escala maxima |

Tots els workers utilitzen `SyntheticTensorGen` amb tensors sintetics deterministes per evitar limitacions de memoria RAM.

## 2. Resultats

| Workers | OK/Total | Temps total (s) | Agregacio (ms) | Submit (ms) | Speedup | Eficiencia |
|---|---|---|---|---|---|---|
| 2 | 2/2 | 0.003 | <1 | <1 | 1.00x | 100.0% |
| 4 | 4/4 | 0.006 | <1 | <1 | 0.50x | 25.0% |
| 8 | 8/8 | 0.010 | <1 | <1 | 0.30x | 7.5% |

## 3. Analisi

**Tots els workers completen correctament** en totes les configuracions. El coordinator gestiona 8 workers simultanis sense errors.

Eficiencia decreix en augmentar workers perque l'execucio es sequencial dins del mateix proces. L'increment de temps es degut a:
- Mes dades a procesar (mes deltes per agregar)
- Operacions I/O de base de dades SQLite per cada batch

## 4. Colls d'ampolla identificats

| Component | Limitacio | Impacte |
|---|---|---|
| Coordinator (in-process) | Execucio sequencial | No paral·lelitzable |
| SQLite | Lock per escritura concurrent | Pot limitar workers paral·lels |
| RAM amb models reals | 2 workers ~3.5GB | 8 workers requeririen 16GB+ |

## 5. Recomanacions

1. El coordinator procesa correctament fins a 8 workers. En produccio amb workers reals, comencar amb 2 workers i escalar monitoritzant RAM.
2. Per evitar OOM amb workers reals, executar-los sequencialment o en maquines separades.
3. El coll d'ampolla principal no es el coordinator sino la memoria disponible per carregar multiples instancies del model.
