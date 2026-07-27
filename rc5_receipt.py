"""RC5.1 — Receipt generation with HMAC. Signing/verification key separation."""
import hashlib
import hmac
import json
import time
import os
import copy
import tempfile

KEYRING_FILE = os.environ.get("RC5_KEYRING", "/tmp/rc5_keyring.json")
_KEYRING_LOCK = __import__("threading").Lock()

def _load_keyring():
    if os.path.exists(KEYRING_FILE):
        with open(KEYRING_FILE) as f:
            return json.load(f)
    return {"signing": {}, "verification": {}}

def _save_keyring(kr):
    tmp = KEYRING_FILE + ".tmp." + os.urandom(4).hex()
    with open(tmp, "w") as f:
        json.dump(kr, f, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, KEYRING_FILE)
    os.chmod(KEYRING_FILE, 0o600)

def init_keyring(active_key_id="k1", active_key_hex=None, retention_days=90):
    with _KEYRING_LOCK:
        kr = _load_keyring()
        if active_key_id not in kr["signing"]:
            key = active_key_hex or hashlib.sha256(os.urandom(32)).hexdigest()
            kr["signing"][active_key_id] = {
                "key": key, "created_at": time.time(), "type": "signing"
            }
            kr["verification"][active_key_id] = {
                "key": key, "created_at": time.time(), "retired_at": None, "type": "verification"
            }
            _save_keyring(kr)
    return kr

def rotate_key(new_key_id, new_key_hex=None):
    with _KEYRING_LOCK:
        kr = _load_keyring()
        # Retire all current signing keys
        for kid in list(kr["signing"]):
            entry = kr["signing"].pop(kid)
            if kid in kr["verification"]:
                kr["verification"][kid]["retired_at"] = time.time()
        # Add new signing key
        key = new_key_hex or hashlib.sha256(os.urandom(32)).hexdigest()
        kr["signing"][new_key_id] = {
            "key": key, "created_at": time.time(), "type": "signing"
        }
        kr["verification"][new_key_id] = {
            "key": key, "created_at": time.time(), "retired_at": None, "type": "verification"
        }
        _save_keyring(kr)
    return kr

def get_signing_key(key_id):
    """Get signing key bytes. Returns None if not an active signing key."""
    with _KEYRING_LOCK:
        kr = _load_keyring()
        entry = kr["signing"].get(key_id)
        if entry is None:
            return None
        return bytes.fromhex(entry["key"])

def get_verification_key(key_id):
    """Get verification key bytes. Works for both active and retired keys."""
    with _KEYRING_LOCK:
        kr = _load_keyring()
        entry = kr["verification"].get(key_id)
        if entry is None:
            return None
        return bytes.fromhex(entry["key"])

def canonical_json(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))

def generate_receipt(
    run_id, round_id, assignment_id, micro_unit_id,
    worker_id, session_id,
    base_adapter_hash, model_hash, adapter_schema_hash,
    loss_definition_hash, precision_profile, data_shard_hash,
    effective_trainable_tokens, optimizer_steps,
    first_step_id, last_step_id,
    delta_sha256,
    receipt_key_id="k1",
    expiry_s=3600,
):
    kr = _load_keyring()
    if receipt_key_id not in kr.get("signing", {}):
        raise ValueError(f"Signing key {receipt_key_id} not active")
    receipt = {
        "protocol_version": "1.1.0-rc5",
        "run_id": run_id, "round_id": round_id,
        "assignment_id": assignment_id, "micro_unit_id": micro_unit_id,
        "worker_id": worker_id, "session_id": session_id,
        "base_adapter_hash": base_adapter_hash, "model_hash": model_hash,
        "adapter_schema_hash": adapter_schema_hash,
        "loss_definition_hash": loss_definition_hash,
        "precision_profile": precision_profile, "data_shard_hash": data_shard_hash,
        "effective_trainable_tokens": effective_trainable_tokens,
        "optimizer_steps": optimizer_steps,
        "first_step_id": first_step_id, "last_step_id": last_step_id,
        "delta_sha256": delta_sha256,
        "issued_at": time.time(), "expires_at": time.time() + expiry_s,
        "receipt_nonce": hashlib.sha256(os.urandom(16)).hexdigest()[:16],
        "receipt_key_id": receipt_key_id,
    }
    payload = canonical_json(receipt)
    key = get_signing_key(receipt_key_id)
    if key is None:
        raise ValueError(f"Signing key {receipt_key_id} not found")
    receipt["signature"] = hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()
    return receipt

def verify_receipt(receipt):
    """Verify a receipt's HMAC. Uses verification keys (active + retired)."""
    r = copy.deepcopy(receipt)
    sig = r.pop("signature", None)
    if sig is None:
        return False, "missing signature"
    kid = r.get("receipt_key_id", "")
    key = get_verification_key(kid)
    if key is None:
        return False, f"unknown key_id {kid}"
    payload = canonical_json(r)
    expected = hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return False, "HMAC mismatch"
    # Check expiry
    if time.time() > r.get("expires_at", 0):
        return False, "receipt expired"
    return True, "ok"
