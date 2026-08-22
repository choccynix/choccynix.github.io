#!/usr/bin/env python3
import sys
from pathlib import Path
import json

def parse_packages_file(text: str):
    blocks = text.split("\n\n")
    header = blocks[0]
    stanzas = []
    for block in blocks[1:]:
        block = block.strip("\n")
        if not block:
            continue
        fields = {}
        order = []
        for line in block.splitlines():
            if ":" not in line:
                continue
            key, _, value = line.partition(":")
            key = key.strip()
            fields[key] = value.strip()
            order.append(key)
        stanzas.append((order, fields))
    return header, stanzas

def render_packages_file(header: str, stanzas) -> str:
    out = [header.rstrip("\n"), ""]
    for order, fields in stanzas:
        for key in order:
            out.append(f"{key}: {fields[key]}")
        out.append("")
    return "\n".join(out).rstrip("\n") + "\n"

def main():
    if len(sys.argv) != 4:
        print(f"usage: {sys.argv[0]} PKGDIR/Packages upload-map.json OUTPUT", file=sys.stderr)
        sys.exit(2)

    packages_path = Path(sys.argv[1])
    upload_map_path = Path(sys.argv[2])
    output_path = Path(sys.argv[3])

    if not packages_path.exists():
        print(f"[warn] {packages_path} does not exist", file=sys.stderr)
        sys.exit(0)

    header, stanzas = parse_packages_file(packages_path.read_text())
    upload_map = json.loads(upload_map_path.read_text())

    rewritten = 0
    for order, fields in stanzas:
        rel_path = fields.get("PATH")
        if rel_path in upload_map:
            fields["URI"] = upload_map[rel_path]
            if "URI" not in order:
                order.append("URI")
            rewritten += 1

    output_path.write_text(render_packages_file(header, stanzas))
    print(f"[info] wrote {rewritten} absolute URIs to {output_path}")

if __name__ == "__main__":
    main()
