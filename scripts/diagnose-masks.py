#!/usr/bin/env python3
"""
◈ diagnose-masks.py

Runs `emerge -pv` per atom and prints Portage's own explanation for any
atom it can't resolve — keyword mask, package.mask, REQUIRED_USE, etc. —
rather than us guessing at the reason from a prior failure's tail output.

Never fails the job; this is purely informational; it runs before the real
build so the log makes clear *why* anything is unbuildable on this profile,
distinct from things that are simply wrong in packages*.list.

Usage:
    python3 diagnose-masks.py packages.list
"""
import re
import subprocess
import sys
from pathlib import Path

ATOM_LINE = re.compile(r"^(?P<atom>\S+)(?:\s+use:(?P<use>\S+))?\s*$")


def main():
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} packages.list", file=sys.stderr)
        sys.exit(2)

    list_path = Path(sys.argv[1])
    atoms = []
    for line in list_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = ATOM_LINE.match(line)
        if m:
            atoms.append(m.group("atom"))

    print(f"[diagnose] checking {len(atoms)} atoms from {list_path}")
    for atom in atoms:
        result = subprocess.run(
            ["emerge", "-pv", "--color=n", atom],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            print(f"[diagnose] OK   {atom}")
            continue
        print(f"[diagnose] FAIL {atom}")
        # print only the reason-bearing lines, not the whole noisy output
        for line in result.stdout.splitlines() + result.stderr.splitlines():
            if any(kw in line for kw in ("mask", "REQUIRED_USE", "keyword", "satisfy", "unsatisfied")):
                print(f"    {line.strip()}")


if __name__ == "__main__":
    main()
