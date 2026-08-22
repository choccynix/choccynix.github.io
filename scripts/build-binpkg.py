#!/usr/bin/env python3
import os
import re
import subprocess
import sys
import json
from pathlib import Path

ATOM_LINE = re.compile(r"^(?P<atom>\S+)(?:\s+use:(?P<use>\S+))?\s*$")

def parse_packages_list(path: Path):
    atoms = []
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        m = ATOM_LINE.match(line)
        if not m:
            continue
        atoms.append((m.group("atom"), m.group("use")))
    return atoms

def write_package_use_overrides(atoms) -> None:
    lines = []
    for atom, use_override in atoms:
        if not use_override:
            continue
        flags = " ".join(flag.strip() for flag in use_override.split(","))
        lines.append(f"{atom} {flags}")
    if not lines:
        return
    override_dir = Path("/etc/portage/package.use")
    override_dir.mkdir(parents=True, exist_ok=True)
    (override_dir / "ci-overrides").write_text("\n".join(lines) + "\n")
    print(f"[info] wrote {len(lines)} per-atom USE override(s)")

def main():
    if len(sys.argv) != 3:
        print(f"usage: {sys.argv[0]} packages.list PKGDIR", file=sys.stderr)
        sys.exit(2)

    list_path = Path(sys.argv[1])
    pkgdir = Path(sys.argv[2])
    pkgdir.mkdir(parents=True, exist_ok=True)

    atoms = parse_packages_list(list_path)
    print(f"[info] {len(atoms)} atoms to build")
    write_package_use_overrides(atoms)

    env = os.environ.copy()
    env["PKGDIR"] = str(pkgdir)
    env["CONFIG_PROTECT_MASK"] = (
        "/etc/portage/package.use /etc/portage/package.accept_keywords "
        "/etc/portage/package.mask /etc/portage/package.unmask"
    )

    cmd = [
        "emerge", "--buildpkg", "--usepkg=y", "--quiet-build=y",
        "--autounmask-write=y", "--autounmask-continue=y",
    ] + [atom for atom, _ in atoms]

    print(f"[build] {' '.join(a for a, _ in atoms)}")
    result = subprocess.run(cmd, env=env, capture_output=True, text=True)
    ok = result.returncode == 0
    retried = False

    if not ok and "Autounmask changes successfully written" in (result.stdout + result.stderr):
        print("[info] autounmask changes written — retrying emerge")
        retried = True
        result = subprocess.run(cmd, env=env, capture_output=True, text=True)
        ok = result.returncode == 0

    report = {
        "atoms": [atom for atom, _ in atoms],
        "ok": ok,
        "retried": retried,
        "log_tail": "" if ok else (result.stdout[-20000:] + "\n" + result.stderr[-20000:]),
    }
    (pkgdir / "build-report.json").write_text(json.dumps(report, indent=2))
    sys.exit(0 if ok else 1)

if __name__ == "__main__":
    main()
