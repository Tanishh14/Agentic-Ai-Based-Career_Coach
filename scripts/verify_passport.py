#!/usr/bin/env python3
"""Standalone CLI utility to verify Ed25519-signed Skill Passports."""

from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path


def canonical_json_bytes(data: dict) -> bytes:
    """Produce deterministic UTF-8 bytes for signing / verification."""
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def verify_passport(passport_file: Path, public_key_file: Path | None = None) -> bool:
    try:
        from cryptography.hazmat.primitives.asymmetric import ed25519
    except ImportError:
        print("Error: 'cryptography' package is required. Install via 'pip install cryptography'.")
        return False

    if not passport_file.exists():
        print(f"Error: Passport file '{passport_file}' not found.")
        return False

    with open(passport_file, "r", encoding="utf-8") as f:
        doc = json.load(f)

    signature_b64 = doc.get("signature")
    payload = doc.get("payload")

    if not signature_b64 or not payload:
        print("Error: Invalid passport format (missing signature or payload).")
        return False

    key_id = payload.get("public_key_id")
    pub_hex = None

    if public_key_file and public_key_file.exists():
        with open(public_key_file, "r", encoding="utf-8") as f:
            registry = json.load(f)
            keys = registry.get("keys", {})
            pub_hex = keys.get(key_id, {}).get("public_key_hex")

    if not pub_hex:
        pub_hex = doc.get("public_key_hex")

    if not pub_hex:
        print("Error: Public key hex could not be located in registry or passport.")
        return False

    try:
        public_key = ed25519.Ed25519PublicKey.from_public_bytes(bytes.fromhex(pub_hex))
        signature_bytes = base64.b64decode(signature_b64)
        canonical_bytes = canonical_json_bytes(payload)

        public_key.verify(signature_bytes, canonical_bytes)
        print("✅ Cryptographic Signature: VALID (Ed25519 Verified)")
        print(f"   Candidate: {payload.get('candidate', {}).get('username')}")
        print(f"   Passport ID: {payload.get('passport_id')}")
        print(f"   Verifications: {len(payload.get('verifications', []))} verified competencies")
        return True
    except Exception as e:
        print(f"❌ Cryptographic Signature: INVALID ({e})")
        return False


def main():
    parser = argparse.ArgumentParser(description="Verify an Ed25519 Skill Passport")
    parser.add_argument("--passport", required=True, type=Path, help="Path to passport JSON file")
    parser.add_argument("--public-key", type=Path, default=None, help="Path to public key registry JSON")
    args = parser.parse_args()

    success = verify_passport(args.passport, args.public_key)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
