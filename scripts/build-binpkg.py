#!/usr/bin/env python3
"""
◈ build-binpkg.py

Reads packages.list, runs ONE combined `emerge --buildpkg` covering every
atom in the list, and leaves finished .gpkg.tar (or legacy .tbz2, depending
on Portage's binpkg-format setting) files under PKGDIR for a later step to
collect and upload.

CORRECTED from an earlier revision: the emerge invocation no longer passes
--nodeps. That flag told Portage to build *only* the named atom and refuse
to pull in anything it depends on — fine for packages whose deps happened
to already exist in the stage3 image, but a real (and previously
unflagged) design mistake for anything with actual dependencies not yet
present (mesa, wlroots, sway, firefox, the Qt/KDE stack, etc. all failed
because of this, not because of naming).

CORRECTED AGAIN from an earlier revision: this used to call `emerge` once
per atom in a loop. That broke down for interdependent sets like the
Calamares/KDE-Frameworks stack — e.g. dev-libs/boost got merged early with
default USE flags (its own line in the list), then later, resolving
calamares itself, autounmask correctly determined boost also needed
+python +python_targets_python3_13, but boost was already installed with
different flags from its own separate emerge call, and Portage won't
retroactively change an already-merged package's USE mid-run. Building
every atom in ONE combined `emerge` call lets Portage's solver pick
mutually consistent USE flags for shared dependencies across the whole set
from the start, which is what per-atom REQUIRED_USE/USE conflicts like
this actually need.

Per-atom USE overrides (the `use:` column in packages*.list) are written to
/etc/portage/package.use/ci-overrides *before* the combined emerge runs,
since a single combined invocation only accepts one global USE environment
value — per-package overrides have to come from package.use to apply
individually.

Also passes --autounmask-write=y --autounmask-continue=y: Portage's own
solver frequently determines a shared dependency (libxkbcommon, libglvnd,
freetype, boost, etc.) needs a specific USE flag enabled to satisfy
something deeper in the tree, and without these flags it just reports the
needed change and stops rather than applying it. These flags let it write
the change to /etc/portage/package.use and continue automatically —
standard practice for a disposable CI container building a real
dependency tree, rather than chasing every transitive USE flag by hand.

Stdlib only — no third-party deps, consistent with the rest of the toolchain.

Usage:
    python3 build-binpkg.py packages.list /var/cache/binpkgs

Exit codes:
    0  the combined emerge run succeeded (every atom in the list got built
       or was already satisfied)
    1  the combined emerge run failed — build-report.json's "log_tail"
       has the real reason; it applies to the whole list, since a single
       combined invocation doesn't produce clean per-atom pass/fail
"""
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
            print(f"[warn] could not parse line, skipping: {raw_line!r}", file=sys.stderr)
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
    print(f"[info] wrote {len(lines)} per-atom USE override(s) to package.use/ci-overrides")


def main():
    if len(sys.argv) != 3:
        print(f"usage: {sys.argv[0]} packages.list PKGDIR", file=sys.stderr)
        sys.exit(2)

    list_path = Path(sys.argv[1])
    pkgdir = Path(sys.argv[2])
    pkgdir.mkdir(parents=True, exist_ok=True)

    atoms = parse_packages_list(list_path)
    print(f"[info] {len(atoms)} atoms to build (one combined emerge invocation)")

    write_package_use_overrides(atoms)

    env = os.environ.copy()
    env["PKGDIR"] = str(pkgdir)
    # Without this, CONFIG_PROTECT treats everything under /etc/portage as
    # protected, so --autounmask-write doesn't actually modify our own
    # package.use/package.accept_keywords/package.mask files in place — it
    # writes a separate pending ._cfg0000_ shadow file that never takes
    # effect within the same run, and the "successfully written" message is
    # misleading: nothing was actually applied. There's nothing in this
    # disposable container worth protecting across upgrades, so these
    # paths are unprotected entirely.
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

    # CORRECTED from an earlier revision: --autounmask-continue=y is
    # documented to write the change AND continue the same invocation when
    # dependency calculation is otherwise fully resolved. In practice, on
    # this container, it's been observed to write the change, print
    # "Autounmask changes successfully written.", and then the invocation
    # still exits non-zero with nothing further — reproduced identically
    # across separate runs, so not a fluke. Rather than trust the flag to
    # do what its docs say, if a run fails right after writing a change,
    # just re-invoke emerge fresh once — a new process reliably starts by
    # reading the config that was just written to disk, which is exactly
    # the standard manual workaround Gentoo users already reach for when
    # NOT using --autounmask-continue at all.
    # CORRECTED AGAIN: the check below only looked at result.stdout, but
    # Portage prints "Autounmask changes successfully written." to STDERR
    # (it's an EOutput warning-class message, not an info-class one) —
    # confirmed by two real runs (kde-qt, xlibre) both showing this exact
    # text at the end of their captured log_tail while "retried" stayed
    # false, meaning the retry never actually fired. Checking both streams.
    if not ok and "Autounmask changes successfully written" in (result.stdout + result.stderr):
        print("[info] autounmask changes were written but the run still failed — "
              "retrying once with a fresh emerge invocation")
        retried = True
        result = subprocess.run(cmd, env=env, capture_output=True, text=True)
        ok = result.returncode == 0

    if ok:
        print(f"[ok] combined build succeeded{' (after retry)' if retried else ''}")
    else:
        print(f"[fail] combined build failed{' (even after retry)' if retried else ''}\n{result.stderr[-20000:]}", file=sys.stderr)

    report = {
        "atoms": [atom for atom, _ in atoms],
        "ok": ok,
        "retried": retried,
        "log_tail": "" if ok else (result.stdout[-20000:] + "\n" + result.stderr[-20000:]),
    }
    report_path = pkgdir / "build-report.json"
    report_path.write_text(json.dumps(report, indent=2))
    print(f"[info] report written to {report_path}")

    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
