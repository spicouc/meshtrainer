"""RC5.2 Phase 2 R4: Receipt v1.2 — no default key, no mutation."""
import json, hmac, hashlib, time, uuid
from rc5_2_canonical import canonical_json_v1

def generate_receipt(**kw) -> dict:
    receipt = {
        "protocol_version": "1.2.0-rc5.2",
        "receipt_version": "1.2",
        "receipt_id": kw.get("receipt_id", uuid.uuid4().hex),
        "receipt_nonce": kw.get("receipt_nonce", uuid.uuid4().hex),
        "issued_at": kw.get("issued_at", int(time.time())),
        "expires_at": kw.get("expires_at", int(time.time()) + 3600),
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
    signing_key = kw.get("signing_key")
    if signing_key is None:
        raise ValueError("signing_key is required")
    payload = canonical_json_v1(receipt)
    receipt["signature"] = hmac.new(signing_key, payload, hashlib.sha256).hexdigest()
    return receipt

def verify_receipt(receipt: dict, signing_key: bytes) -> bool:
    sig = receipt.get("signature", "")
    payload = canonical_json_v1({k: v for k, v in receipt.items() if k != "signature"})
    expected = hmac.new(signing_key, payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, expected)
