#!/usr/bin/env bash
# Generate a Fernet key for OPENEDX_WEBHOOK_RELAY_ENCRYPTION_KEY.
#
# Usage:
#   ./scripts/generate_encryption_key.sh
#
# Pipe the output straight into your secrets manager rather than a file —
# e.g. for a Tutor deployment backed by AWS Secrets Manager:
#   ./scripts/generate_encryption_key.sh | \
#     aws secretsmanager create-secret --name openedx/webhook-relay/encryption-key \
#       --secret-string file:///dev/stdin
#
# See docs/decisions/0003-secret-storage.rst and
# docs/decisions/0009-key-rotation-and-secret-backends.rst.
set -euo pipefail

python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
