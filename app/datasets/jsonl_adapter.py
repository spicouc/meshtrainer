"""Capa de datasets (Fase 1): DatasetAdapter JSONL + validació de 9 checks.

Format certificat del core: JSONL amb {instruction, response} o
{messages:[{role,content},...]}. Interfície pensada per afegir CSV/HF/Parquet
en el futur sense tocar els serveis.
"""
from __future__ import annotations

import json
import os
from typing import Any, Iterator, Optional


class DatasetValidationError(Exception):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


class DatasetAdapter:
    """Adapter de dataset JSONL."""

    format = "jsonl"

    def __init__(self, source_path: str):
        self.source_path = source_path
        if not os.path.exists(source_path):
            raise DatasetValidationError([f"fitxer no existeix: {source_path}"])

    # ── lectura ──────────────────────────────────────────────────────────
    def _iter_raw(self) -> Iterator[tuple[dict, int]]:
        with open(self.source_path, encoding="utf-8") as f:
            for i, line in enumerate(f):
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line), i
                except json.JSONDecodeError as e:
                    raise DatasetValidationError(
                        [f"JSONL no parseja línia {i}: {e}"])

    def iter_examples(self, split: str = "train") -> Iterator[dict]:
        """Exemples normalitzats a {instruction, response, source}."""
        for ex, i in self._iter_raw():
            if "instruction" in ex and "response" in ex:
                yield {"instruction": ex["instruction"], "response": ex["response"],
                       "source": f"{split}:{i}"}
            elif "messages" in ex:
                msgs = ex["messages"]
                user = next((m["content"] for m in msgs
                             if m.get("role") == "user"), None)
                asst = next((m["content"] for m in msgs
                             if m.get("role") == "assistant"), None)
                if user is None or asst is None:
                    raise DatasetValidationError(
                        [f"messages incomplet a {split}:{i}"])
                yield {"instruction": user, "response": asst,
                       "source": f"{split}:{i}"}

    def count_examples(self) -> int:
        n = 0
        for _ in self.iter_examples():
            n += 1
        return n

    # ── validació (9 checks del disseny) ─────────────────────────────────
    def validate(self, tokenize_fn=None, max_seq_len: Optional[int] = None) -> dict:
        """Executa els 9 checks. tokenize_fn opcional (backend carregat) per
        als checks 7-9; si no es passa, els checks 7-9 queden marcats com a
        'pendents de backend' (no són errors estructurals)."""
        errors: list[str] = []
        info: dict[str, Any] = {}

        # 1. fitxer existeix i és llegible
        try:
            size = os.path.getsize(self.source_path)
            with open(self.source_path, encoding="utf-8") as f:
                f.read(1024)
        except Exception as e:
            errors.append(f"check1 fitxer il·legible: {e}")
            raise DatasetValidationError(errors)
        info["size"] = size

        # 2. JSONL parseja (totes les línies)
        n_examples = 0
        schemas: set[str] = set()
        try:
            for ex, i in self._iter_raw():
                n_examples += 1
                schemas.add("instruction+response" if "instruction" in ex
                            else ("messages" if "messages" in ex else "unknown"))
        except DatasetValidationError as e:
            errors.append(f"check2 {e.errors[0]}")
            raise DatasetValidationError(errors)
        info["examples"] = n_examples
        info["schema"] = sorted(schemas)

        # 3. schema compatible
        if schemas and "unknown" in schemas:
            errors.append("check3 schema no compatible (ni instruction+response "
                          "ni messages)")

        # 4. exemples no buits
        empty = 0
        for ex in self.iter_examples():
            if not ex.get("instruction", "").strip() or \
               not ex.get("response", "").strip():
                empty += 1
        if empty:
            errors.append(f"check4 {empty} exemples amb instrucció/resposta buida")
        info["empty_examples"] = empty

        # 5. splits correctes (JSONL pla: tot train)
        info["train_count"] = n_examples
        info["validation_count"] = 0
        info["test_count"] = 0

        # 6. format chat/messages correcte — cobert pel check 3 + iter_examples
        try:
            list(self.iter_examples())[:0]
        except DatasetValidationError as e:
            errors.append(f"check6 {e.errors[0]}")

        # 7-9. backend-aware (només si es passa tokenize_fn)
        if tokenize_fn is not None:
            try:
                ex0 = next(iter(self.iter_examples()))
                tok = tokenize_fn(ex0["instruction"], ex0["response"])
                labels = tok[2] if isinstance(tok, tuple) else tok
                ett = int(sum(1 for t in labels if t != -100))
                if ett <= 0:
                    errors.append("check8 ETT <= 0 amb el backend seleccionat")
                else:
                    info["ett_sample"] = ett
                if max_seq_len:
                    seq = len(tok[0]) if isinstance(tok, tuple) else len(tok)
                    if seq > max_seq_len:
                        errors.append(f"check9 seq_len {seq} > max_seq_len "
                                      f"{max_seq_len}")
            except Exception as e:
                errors.append(f"check7 tokenització impossible: {e}")
        else:
            info["backend_checks"] = "pending"

        return {"errors": errors, "info": info}
