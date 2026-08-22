#!/usr/bin/env python3
"""
◈ merge-packages-index.py

Each matrix group (core / graphics / desktop / firefox / kde-qt) produces its own complete
Packages-format file via generate-packages-index.py. This just concatenates
their stanzas under a single shared header, so the published binhost index
looks like one coherent set to Portage rather than three.

Usage:
    python3 merge-packages-index.py OUTPUT fragment1 [fragment2 ...]
"""
import sys
from pathlib import Path


def split_header_and_stanzas(text: str):
    blocks = text.split("\n\n")
    header = blocks[0]
    stanzas = [b.strip("\n") for b in blocks[1:] if b.strip("\n")]
    return header, stanzas


def main():
    if len(sys.argv) < 3:
        print(f"usage: {sys.argv[0]} OUTPUT fragment1 [fragment2 ...]", file=sys.stderr)
        sys.exit(2)

    output_path = Path(sys.argv[1])
    fragment_paths = [Path(p) for p in sys.argv[2:]]

    header = None
    all_stanzas = []
    for path in fragment_paths:
        if not path.exists():
            print(f"[warn] fragment missing, skipping: {path}", file=sys.stderr)
            continue
        h, stanzas = split_header_and_stanzas(path.read_text())
        if header is None:
            header = h
        all_stanzas.extend(stanzas)

    if header is None:
        print("[error] no fragments found", file=sys.stderr)
        sys.exit(1)

    content = header.rstrip("\n") + "\n\n" + "\n\n".join(all_stanzas) + "\n"
    output_path.write_text(content)
    print(f"[ok] merged {len(all_stanzas)} stanzas from {len(fragment_paths)} fragments -> {output_path}")


if __name__ == "__main__":
    main()
