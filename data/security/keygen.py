"""Generate a data-encryption key.

Prints a fresh key to stdout AND writes it to the gitignored local keyfile so a
developer can get running with one command. For production, generate the key in
your KMS/secret manager instead and inject it via PULSESCORE_DATA_KEY.

    python -m data.security.keygen          # write local keyfile + print key
    export PULSESCORE_DATA_KEY=$(python -m data.security.keygen --print-only)
"""
from __future__ import annotations

import argparse

from cryptography.fernet import Fernet

from .crypto import _keyfile_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a PulseScore data key.")
    parser.add_argument("--print-only", action="store_true",
                        help="print a key without writing the local keyfile")
    args = parser.parse_args()

    key = Fernet.generate_key()
    if args.print_only:
        print(key.decode())
        return

    path = _keyfile_path()
    path.write_bytes(key)
    try:
        path.chmod(0o600)
    except OSError:
        pass
    print(key.decode())


if __name__ == "__main__":
    main()
