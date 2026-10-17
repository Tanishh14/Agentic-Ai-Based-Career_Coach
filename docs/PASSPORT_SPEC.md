# Cryptographic Skill Passport Specification (Ed25519)

This document describes the cryptographic proof model, canonical serialization, key management, and standalone verification protocol for the **AI Career Coach Skill Passport**.

---

## 🔐 Overview

A Skill Passport is an Ed25519-signed JSON document that binds candidate technical competencies to verifiable GitHub commit SHAs, line numbers, critic verdicts, and interview evaluation timestamps.

Third parties (employers, evaluators) can verify the passport completely offline without contacting the server or having an account.

---

## 📜 Canonical Data Structure

The signed payload is serialized using Canonical JSON (RFC 8785 / JCS) to guarantee deterministic byte representation:

```json
{
  "passport_id": "sp_9f8c12a4b87e4d2a",
  "candidate": {
    "username": "Nishadwaw",
    "issued_at": "2026-10-17T03:00:00Z",
    "expires_at": "2027-10-17T03:00:00Z"
  },
  "verifications": [
    {
      "skill": "API Development",
      "verdict": "DEMONSTRATED",
      "repo": "Nishadwaw/demo-repo",
      "commit_sha": "3d9faa264906dd3ae65bf92be4c008959e72569f",
      "file": "app/main.py",
      "lines": "20-45",
      "depth": "production"
    }
  ],
  "public_key_id": "key_2026_ed25519_primary"
}
```

---

## 🔑 Key Management & Signing

- **Algorithm**: `Ed25519` (RFC 8032) using pure Curve25519 Edwards-curve Digital Signature Algorithm.
- **Key Storage**: The active private key is stored in PEM format at `data/keys/passport_ed25519_active.pem` under restricted file permissions.
- **Public Key Registry**: Public keys are cataloged at `data/keys/passport_public_keys.json` with activation and revocation timestamps.

---

## 🔍 Independent Verification

Verification can be executed via the bundled CLI:
```bash
python scripts/verify_passport.py --passport passport.json --public-key data/keys/passport_public_keys.json
```
Or via standard Python `cryptography`:
```python
from cryptography.hazmat.primitives.asymmetric import ed25519
import base64

public_key = ed25519.Ed25519PublicKey.from_public_bytes(bytes.fromhex(pub_hex))
public_key.verify(base64.b64decode(signature_b64), canonical_bytes)
```
