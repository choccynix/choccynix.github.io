#!/usr/bin/env python3
import json
import mimetypes
import os
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

API = "https://api.github.com"
MAX_UPLOAD_ATTEMPTS = 4

def api_request(method: str, url: str, token: str, data: bytes | None = None, content_type: str = "application/json"):
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    if data is not None:
        req.add_header("Content-Type", content_type)
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode())

def get_or_create_release(repo: str, tag: str, token: str) -> dict:
    try:
        return api_request("GET", f"{API}/repos/{repo}/releases/tags/{tag}", token)
    except urllib.error.HTTPError:
        pass
    payload = json.dumps({
        "tag_name": tag,
        "name": tag,
        "body": f"Automated binary package build: {tag}",
        "prerelease": "rolling" not in tag,
    }).encode()
    return api_request("POST", f"{API}/repos/{repo}/releases", token, data=payload)

def find_existing_asset_url(repo: str, tag: str, name: str, token: str) -> str | None:
    release = api_request("GET", f"{API}/repos/{repo}/releases/tags/{tag}", token)
    for asset in release.get("assets", []):
        if asset["name"] == name:
            return asset["browser_download_url"]
    return None

def upload_asset(release: dict, file_path: Path, token: str) -> dict:
    upload_url = release["upload_url"].split("{", 1)[0]
    mime, _ = mimetypes.guess_type(str(file_path))
    mime = mime or "application/octet-stream"
    data = file_path.read_bytes()
    url = f"{upload_url}?name={file_path.name}"
    return api_request("POST", url, token, data=data, content_type=mime)

def upload_asset_with_retry(release: dict, file_path: Path, token: str) -> dict:
    for attempt in range(1, MAX_UPLOAD_ATTEMPTS + 1):
        try:
            return upload_asset(release, file_path, token)
        except urllib.error.HTTPError:
            raise
        except (urllib.error.URLError, ConnectionError, OSError) as e:
            if attempt == MAX_UPLOAD_ATTEMPTS:
                raise
            wait = 2 ** attempt
            print(f"[retry] {file_path.name} error ({e}), retrying in {wait}s")
            time.sleep(wait)

def main():
    if len(sys.argv) != 4:
        print(f"usage: {sys.argv[0]} PKGDIR TAG OUTPUT_MAP_JSON", file=sys.stderr)
        sys.exit(2)

    pkgdir = Path(sys.argv[1])
    tag = sys.argv[2]
    out_map_path = Path(sys.argv[3])

    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not token or not repo:
        print("[error] GITHUB_TOKEN and GITHUB_REPOSITORY must be set", file=sys.stderr)
        sys.exit(1)

    release = get_or_create_release(repo, tag, token)
    upload_map = {}
    files = sorted(p for p in pkgdir.rglob("*") if p.is_file() and p.name not in {"Packages", "build-report.json"})

    for f in files:
        rel = str(f.relative_to(pkgdir))
        try:
            asset = upload_asset_with_retry(release, f, token)
            upload_map[rel] = asset["browser_download_url"]
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")
            if e.code == 422 and "already_exists" in body:
                existing_url = find_existing_asset_url(repo, tag, f.name, token)
                if existing_url:
                    upload_map[rel] = existing_url
            else:
                raise

    out_map_path.write_text(json.dumps(upload_map, indent=2))

if __name__ == "__main__":
    main()
