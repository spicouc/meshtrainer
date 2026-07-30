"""RC5.2 Phase 2 W6: Receipt v1.2."""
import json, hashlib, hmac, time, uuid
from rc5_2_canonical import canonical_json_v1

def generate_receipt(**kw) -> dict:
    """Generate receipt v1.2 with HMAC signature."""
    key = kw.get("signing_key", b"\x01" * 32)
    receipt = {
        "protocol_version": "1.2.0-rc5.2",
        "receipt_version": "1.2",
        "receipt_id": kw.get("receipt_id", uuid.uuid4().hex),
        "receipt_nonce": kw.get("receipt_nonce", uuid.uuid4().hex),
        "issued_at": int(time.time()),
        "expires_at": int(time.time()) + 3600,
        "run_id": kw.get("run_id", ""),
        "round_id": kw.get("round_id", ""),
        "assignment_id": kw.get("assignment_id", ""),
        "micro_unit_id": kw.get("micro_unit_id", ""),
        "unit_id": kw.get("unit_id", ""),
        "worker_id": kw.get("worker_id", ""),
        "session_id": kw.get("session_id", ""),
        "worker_model_hash": kw.get("worker_model_hash", ""),
        "partition_schema_hash": kw.get("partition_schema_hash", ""),
        "base_adapter_hash": kw.get("base_adapter_hash", ""),
        "adapter_schema_hash": kw.get("adapter_schema_hash", ""),
        "numerical_profile_hash": kw.get("numerical_profile_hash", ""),
        "update_id": kw.get("update_id", ""),
        "delta_id": kw.get("delta_id", ""),
        "delta_format": kw.get("delta_format", "tensor_bundle_v1"),
        "delta_bundle_sha256": kw.get("delta_bundle_sha256", ""),
        "delta_bundle_byte_length": kw.get("delta_bundle_byte_length", 0),
        "effective_trainable_tokens": kw.get("ett", 0),
        "loss": kw.get("loss", 0.0),
        "key_id": kw.get("key_id", "k1"),
    }
    payload = canonical_json_v1(receipt)
    receipt["signature"] = hmac.new(key, payload, hashlib.sha256).hexdigest()
    return receipt

def verify_receipt(receipt: dict, key: bytes = b"\x01" * 32) -> bool:
    sig = receipt.pop("signature", "")
    payload = canonical_json_v1(receipt)
    expected = hmac.new(key, payload, hashlib.sha256).hexdigest()
    receipt["signature"] = sig
    return hmac.compare_digest(sig, expected)

COORDINATOR_CONTRIBUTIONS = {}
COORDINATOR_ADAPTER = None

def checkpoint_upload(contrib: dict, key: bytes = b"\x01" * 32) -> str:
    """RC5.2 checkpoint.upload — validate receipt, store contribution."""
    receipt = contrib.get("receipt", {})
    if not verify_receipt(receipt, key):
        raise ValueError("Invalid receipt HMAC")
    cid = receipt["receipt_id"]
    if cid in COORDINATOR_CONTRIBUTIONS:
        raise ValueError("Duplicate contribution")
    COORDINATOR_CONTRIBUTIONS[cid] = {
        "status": "RECEIVED",
        "delta_bundle_b64": contrib.get("delta_bundle_b64", ""),
        "delta_bundle_sha256": receipt.get("delta_bundle_sha256", ""),
        "loss": receipt.get("loss", 0.0),
        "ett": receipt.get("effective_trainable_tokens", 0),
        "worker_id": receipt.get("worker_id", ""),
    }
    return cid

def validate_contribution(cid: str) -> bool:
    if cid in COORDINATOR_CONTRIBUTIONS:
        COORDINATOR_CONTRIBUTIONS[cid]["status"] = "VALIDATED"
        return True
    return False

def activate_contribution(cid: str) -> bool:
    if cid in COORDINATOR_CONTRIBUTIONS and COORDINATOR_CONTRIBUTIONS[cid]["status"] == "VALIDATED":
        COORDINATOR_CONTRIBUTIONS[cid]["status"] = "ACTIVE"
        return True
    return False
