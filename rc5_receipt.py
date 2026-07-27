"""RC5.1 — Receipt generation with HMAC authentication."""
import hashlib
import hmac
import json
import time
import os

RECEIPT_KEY = os.urandom(32)  # Per PoC, en produccio rotacio de claus
RECEIPT_KEY_ID = "k1"

def canonical_json(obj):
    """Serialitzacio JSON canonica (keys sorted, no whitespace)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))

def generate_receipt(
    run_id,
    round_id,
    assignment_id,
    micro_unit_id,
    worker_id,
    session_id,
    base_adapter_hash,
    model_hash,
    adapter_schema_hash,
    loss_definition_hash,
    precision_profile,
    data_shard_hash,
    effective_trainable_tokens,
    optimizer_steps,
    first_step_id,
    last_step_id,
):
    receipt = {
        "protocol_version": "1.1.0-rc5",
        "run_id": run_id,
        "round_id": round_id,
        "assignment_id": assignment_id,
        "micro_unit_id": micro_unit_id,
        "worker_id": worker_id,
        "session_id": session_id,
        "base_adapter_hash": base_adapter_hash,
        "model_hash": model_hash,
        "adapter_schema_hash": adapter_schema_hash,
        "loss_definition_hash": loss_definition_hash,
        "precision_profile": precision_profile,
        "data_shard_hash": data_shard_hash,
        "effective_trainable_tokens": effective_trainable_tokens,
        "optimizer_steps": optimizer_steps,
        "first_step_id": first_step_id,
        "last_step_id": last_step_id,
        "issued_at": time.time(),
        "receipt_nonce": hashlib.sha256(os.urandom(16)).hexdigest()[:16],
        "receipt_key_id": RECEIPT_KEY_ID,
    }
    payload = canonical_json(receipt)
    receipt["signature"] = hmac.new(RECEIPT_KEY, payload.encode(), hashlib.sha256).hexdigest()
    return receipt

def verify_receipt(receipt):
    """Verify a receipt's HMAC signature."""
    sig = receipt.pop("signature", None)
    if sig is None:
        return False
    payload = canonical_json(receipt)
    expected = hmac.new(RECEIPT_KEY, payload.encode(), hashlib.sha256).hexdigest()
    receipt["signature"] = sig  # restore
    return hmac.compare_digest(sig, expected)
