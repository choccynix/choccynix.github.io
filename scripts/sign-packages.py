#!/usr/bin/env python3
"""
◈ sign-packages.py

Detached-signs the published Packages index with GPG so consumers can verify
it hasn't been tampered with in transit (relevant here since the index will
be served over GitHub Pages / raw.githubusercontent.com rather than
Gentoo's own signed infrastructure).

Shells out to `gpg` directly rather than pulling in a Python GPG binding —
keeps this stdlib-only like the rest of the toolchain, and matches whatever
gpg version is already installed in the CI container.

Requires:
    - a GPG secret key imported into the CI runner's keyring
      (GPG_SIGNING_KEY_ID env var identifies which key to use)
    - GPG_PASSPHRASE env var if the key is passphrase-protected (passed via
      --batch --passphrase-fd rather than interactively)

Usage:
    python3 sign-packages.py Packages Packages.sig
"""
import os
import subprocess
import sys
from pathlib import Path


def main():
    if len(sys.argv) != 3:
        print(f"usage: {sys.argv[0]} INPUT_FILE OUTPUT_SIG", file=sys.stderr)
        sys.exit(2)

    input_path = Path(sys.argv[1])
    output_path = Path(sys.argv[2])

    key_id = os.environ.get("GPG_SIGNING_KEY_ID")
    passphrase = os.environ.get("GPG_PASSPHRASE")

    if not key_id:
        print("[error] GPG_SIGNING_KEY_ID must be set", file=sys.stderr)
        sys.exit(1)

    cmd = [
        "gpg", "--batch", "--yes",
        "--local-user", key_id,
        "--detach-sign",
        "--armor",
        "--output", str(output_path),
    ]

    if passphrase:
        cmd = cmd[:1] + ["--pinentry-mode", "loopback", "--passphrase-fd", "0"] + cmd[1:]

    print(f"[sign] {input_path} -> {output_path} (key {key_id})")
    result = subprocess.run(
        cmd + [str(input_path)],
        input=(passphrase.encode() if passphrase else None),
        capture_output=True,
    )

    if result.returncode != 0:
        print(result.stderr.decode(errors="replace"), file=sys.stderr)
        sys.exit(1)

    print(f"[ok] wrote {output_path}")


if __name__ == "__main__":
    main()
