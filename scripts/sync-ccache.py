#!/usr/bin/env python3
"""
◈ sync-ccache.py

Persists a per-group ccache directory as a release asset on a fixed tag
(`build-cache`, never the dated rolling tags), so weekly rebuilds only
recompile what actually changed instead of starting cold every time — this
matters a lot for the graphics/firefox/kde-qt groups, which are the whole reason
"long compile" is a concern here.

Uses the same asset-replace mechanism as upload-single-asset.py under the
hood (a cache miss on first run is expected and not an error).

Usage:
    python3 sync-ccache.py pull GROUP_NAME CCACHE_DIR
    python3 sync-ccache.py push GROUP_NAME CCACHE_DIR
"""
import subprocess
import sys
import tarfile
import urllib.error
import urllib.request
import os
import json
from pathlib import Path

API = "https://api.github.com"
CACHE_TAG = "build-cache"


def asset_name(group: str) -> str:
    return f"ccache-{group}.tar.zst"


def find_asset_url(repo: str, token: str, name: str) -> str | None:
    req = urllib.request.Request(f"{API}/repos/{repo}/releases/tags/{CACHE_TAG}")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    try:
        with urllib.request.urlopen(req) as resp:
            release = json.loads(resp.read().decode())
    except urllib.error.HTTPError:
        return None
    for asset in release.get("assets", []):
        if asset["name"] == name:
            return asset["url"]  # API url, needed for the octet-stream Accept header below
    return None


def pull(group: str, ccache_dir: Path):
    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")
    name = asset_name(group)
    asset_url = find_asset_url(repo, token, name) if token and repo else None

    if not asset_url:
        print(f"[cache] no existing cache for group '{group}' — starting cold")
        ccache_dir.mkdir(parents=True, exist_ok=True)
        return

    print(f"[cache] restoring {name}")
    req = urllib.request.Request(asset_url)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/octet-stream")
    tmp_tar = Path("/tmp/ccache-restore.tar.zst")
    with urllib.request.urlopen(req) as resp, open(tmp_tar, "wb") as f:
        f.write(resp.read())

    ccache_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(["tar", "--zstd", "-xf", str(tmp_tar), "-C", str(ccache_dir)], check=True)
    print(f"[cache] restored to {ccache_dir}")


def push(group: str, ccache_dir: Path):
    if not ccache_dir.exists() or not any(ccache_dir.iterdir()):
        print(f"[cache] {ccache_dir} empty, nothing to push")
        return

    tmp_tar = Path("/tmp/ccache-push.tar.zst")
    subprocess.run(["tar", "--zstd", "-cf", str(tmp_tar), "-C", str(ccache_dir), "."], check=True)

    name = asset_name(group)
    print(f"[cache] pushing {name} ({tmp_tar.stat().st_size} bytes)")
    subprocess.run(
        [sys.executable, str(Path(__file__).parent / "upload-single-asset.py"), str(tmp_tar), CACHE_TAG, name],
        check=True,
    )


def main():
    if len(sys.argv) != 4 or sys.argv[1] not in ("pull", "push"):
        print(f"usage: {sys.argv[0]} pull|push GROUP_NAME CCACHE_DIR", file=sys.stderr)
        sys.exit(2)

    action, group, ccache_dir = sys.argv[1], sys.argv[2], Path(sys.argv[3])
    (pull if action == "pull" else push)(group, ccache_dir)


if __name__ == "__main__":
    main()
