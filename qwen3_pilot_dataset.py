"""Qwen3PilotDataset — loader JSONL del dataset pilot.

Format mínim: {"instruction": "...", "response": "..."}
Format opcional: {"messages": [{"role": "user", "content": "..."},
                               {"role": "assistant", "content": "..."}]}

Separació obligatòria: train / validation / test.
"""
import json
import os


class Qwen3PilotDataset:
    def __init__(self, data_dir: str):
        self.data_dir = data_dir

    def _load(self, name: str) -> list[dict]:
        path = os.path.join(self.data_dir, f"{name}.jsonl")
        if not os.path.exists(path):
            raise FileNotFoundError(f"Split no trobat: {path}")
        out = []
        with open(path, encoding="utf-8") as f:
            for i, line in enumerate(f):
                line = line.strip()
                if not line:
                    continue
                try:
                    ex = json.loads(line)
                except json.JSONDecodeError as e:
                    raise ValueError(f"JSONL malformat a {name}.jsonl línia {i+1}: {e}")
                out.append(self._normalize(ex, name, i))
        if not out:
            raise ValueError(f"Split buit: {name}")
        return out

    @staticmethod
    def _normalize(ex: dict, name: str, i: int) -> dict:
        if "instruction" in ex and "response" in ex:
            return {"instruction": ex["instruction"], "response": ex["response"],
                    "source": f"{name}:{i}"}
        if "messages" in ex:
            msgs = ex["messages"]
            user = next((m["content"] for m in msgs if m.get("role") == "user"), None)
            asst = next((m["content"] for m in msgs if m.get("role") == "assistant"), None)
            if user is None or asst is None:
                raise ValueError(f"messages incomplet a {name}:{i}")
            return {"instruction": user, "response": asst, "source": f"{name}:{i}"}
        raise ValueError(f"Format desconegut a {name}:{i} (cal instruction/response o messages)")

    def train(self) -> list[dict]:
        return self._load("train")

    def validation(self) -> list[dict]:
        return self._load("validation")

    def test(self) -> list[dict]:
        return self._load("test")

    def summary(self) -> dict:
        return {s: len(self._load(s)) for s in ("train", "validation", "test")}
