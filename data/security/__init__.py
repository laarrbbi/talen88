"""Security primitives for the data layer.

Encryption-at-rest and the key-management seam live here, isolated from query and
analytics logic. Production deployments swap the key provider (BYOK / KMS / HSM)
without touching anything else.
"""
