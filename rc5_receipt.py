"""RC5.1 — Receipt generation with HMAC authentication. Persistent keyring."""
import hashlib
import hmac
import json
import time
import os
import copy

RECEIPT_KEYRING_FILE = os.environ.get("RC5_KEYRING", "/tmp/rc5_keyring.json")

def _load_keyring():
    if os.path.exists(RECEIPT_KEYRING_FILE):
        with open(RECEIPT_KEYRING_FILE) as f:
            return json.load(f)
    return {"active": {}, "retired": {}}

def _save_keyring(kr):
    with open(RECEIPT_KEYRING_FILE, "w") as f:
        json.dump(kr, f, sort_keys=True)

def init_keyring(active_key_id="k1", active_key_hex=None):
    """Initialize keyring with one active key. Safe to call multiple times."""
    kr = _load_keyring()
    if active_key_id not in kr["active"]:
        key = active_key_hex or hashlib.sha256(os.urandom(32)).hexdigest()
        kr["active"][active_key_id] = {"key": key, "created_at": time.time()}
        _save_keyring(kr)
    return kr

def rotate_key(new_key_id, new_key_hex=None):
    """Rotate: retire current active, add new active key."""
    kr = _load_keyring()
    for kid in list(kr["active"]):
        kr["retired"][kid] = kr["active"].pop(kid)
        kr["retired"][kid]["retired_at"] = time.time()
    key = new_key_hex or hashlib.sha256(os.urandom(32)).hexdigest()
    kr["active"][new_key_id] = {"key": key, "created_at": time.time()}
    _save_keyring(kr)
    return kr

def get_active_key(key_id):
    """Get key bytes for a key_id. Returns None if unknown or retired."""
    kr = _load_keyring()
    entry = kr["active"].get(key_id)
    if entry is None:
        entry = kr["retired"].get(key_id)
        if entry is None:
            return None
        return bytes.fromhex(entry["key"])
    return bytes.fromhex(entry["key"])

def canonical_json(obj):
    """Serialitzacio JSON canonica (keys sorted, no whitespace)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))

def generate_receipt(
    run_id, round_id, assignment_id, micro_unit_id,
    worker_id, session_id,
    base_adapter_hash, model_hash, adapter_schema_hash,
    loss_definition_hash, precision_profile, data_shard_hash,
    effective_trainable_tokens, optimizer_steps,
    first_step_id, last_step_id,
    receipt_key_id="k1",
    expiry_s=3600,
):
    kr = _load_keyring()
    if receipt_key_id not in kr["active"]:
        raise ValueError(f"Key {receipt_key_id} not active")
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
        "expires_at": time.time() + expiry_s,
        "receipt_nonce": hashlib.sha256(os.urandom(16)).hexdigest()[:16],
        "receipt_key_id": receipt_key_id,
    }
    payload = canonical_json(receipt)
    key = get_active_key(receipt_key_id)
    if key is None:
        raise ValueError(f"Key {receipt_key_id} not found")
    receipt["signature"] = hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()
    return receipt

def verify_receipt(receipt):
    """Verify a receipt's HMAC signature. Does NOT mutate the input."""
    r = copy.deepcopy(receipt)
    sig = r.pop("signature", None)
    if sig is None:
        return False, "missing signature"
    key = get_active_key(r.get("receipt_key_id", ""))
    if key is None:
        return False, "unknown or retired key_id"
    payload = canonical_json(r)
    expected = hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return False, "HMAC mismatch"
    return True, "ok"
