#!/usr/bin/env python3
"""
◈ upload-single-asset.py

Uploads one file to a release under an explicit asset name, deleting any
existing asset with that name first (GitHub Releases won't let you
overwrite an asset in place). Used for things that aren't package blobs —
Packages-index fragments, ccache tarballs — where we want a stable,
overwritable name rather than the natural filename.

Uploads now retry a few times with backoff on transient network errors
(large ccache tarballs can hit the same dropped-connection issue package
uploads did — see upload-release.py for where this was first found).

Usage:
    python3 upload-single-asset.py FILE_PATH TAG ASSET_NAME
"""
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


def api_request(method, url, token, data=None, content_type="application/json"):
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    if data is not None:
        req.add_header("Content-Type", content_type)
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode()) if resp.length != 0 else {}


def get_or_create_release(repo, tag, token):
    try:
        return api_request("GET", f"{API}/repos/{repo}/releases/tags/{tag}", token)
    except urllib.error.HTTPError:
        payload = json.dumps({"tag_name": tag, "name": tag, "prerelease": "rolling" not in tag}).encode()
        return api_request("POST", f"{API}/repos/{repo}/releases", token, data=payload)


def delete_existing_asset(release, name, repo, token):
    for asset in release.get("assets", []):
        if asset["name"] == name:
            req = urllib.request.Request(
                f"{API}/repos/{repo}/releases/assets/{asset['id']}",
                method="DELETE",
            )
            req.add_header("Authorization", f"Bearer {token}")
            urllib.request.urlopen(req)
            return


def upload_asset(release, file_path: Path, asset_name: str, token):
    upload_url = release["upload_url"].split("{", 1)[0]
    mime, _ = mimetypes.guess_type(asset_name)
    mime = mime or "application/octet-stream"
    data = file_path.read_bytes()
    url = f"{upload_url}?name={asset_name}"
    return api_request("POST", url, token, data=data, content_type=mime)


def upload_asset_with_retry(release, file_path: Path, asset_name: str, token):
    for attempt in range(1, MAX_UPLOAD_ATTEMPTS + 1):
        try:
            return upload_asset(release, file_path, asset_name, token)
        except urllib.error.HTTPError:
            raise  # a real HTTP response — not a network drop, don't retry here
        except (urllib.error.URLError, ConnectionError, OSError) as e:
            if attempt == MAX_UPLOAD_ATTEMPTS:
                raise
            wait = 2 ** attempt
            print(f"[retry] {asset_name} upload failed ({e}), attempt {attempt}/{MAX_UPLOAD_ATTEMPTS}, retrying in {wait}s")
            time.sleep(wait)


def main():
    if len(sys.argv) != 4:
        print(f"usage: {sys.argv[0]} FILE_PATH TAG ASSET_NAME", file=sys.stderr)
        sys.exit(2)

    file_path = Path(sys.argv[1])
    tag = sys.argv[2]
    asset_name = sys.argv[3]

    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not token or not repo:
        print("[error] GITHUB_TOKEN and GITHUB_REPOSITORY must be set", file=sys.stderr)
        sys.exit(1)

    release = get_or_create_release(repo, tag, token)
    delete_existing_asset(release, asset_name, repo, token)
    # re-fetch so the assets list (and upload_url) reflect the deletion
    release = api_request("GET", f"{API}/repos/{repo}/releases/tags/{tag}", token)
    asset = upload_asset_with_retry(release, file_path, asset_name, token)
    print(f"[ok] uploaded {asset_name} -> {asset['browser_download_url']}")


if __name__ == "__main__":
    main()
