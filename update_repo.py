#!/usr/bin/env python3

import gzip
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

REPO_OWNER = "choccynix"
BINPKG_REPO = f"{REPO_OWNER}/athanor-binpkgs"

API_URL = (
    f"https://api.github.com/repos/{BINPKG_REPO}"
    "/releases?per_page=100"
)

RELEASE_DOWNLOAD_BASE = (
    f"https://github.com/{BINPKG_REPO}/releases/download/"
)

BINHOST_DIR = Path("binhost")
PROFILES_DIR = Path("profiles")
METADATA_DIR = Path("metadata")
TEMP_DIR = Path("temp_downloads")


PACKAGE_EXTENSIONS = (
    ".gpkg.tar",
    ".tbz2",
    ".xpak",
)


# ---------------------------------------------------------------------------
# Repository setup
# ---------------------------------------------------------------------------

def setup_directories():
    """Create the static Gentoo repository/binhost structure."""

    BINHOST_DIR.mkdir(parents=True, exist_ok=True)
    PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    METADATA_DIR.mkdir(parents=True, exist_ok=True)

    # This isn't actually needed by the GitHub Pages binhost, but retaining
    # the directory makes the generated repository structure conventional.
    Path("/var/db/repos/gentoo").mkdir(
        parents=True,
        exist_ok=True,
    )

    (PROFILES_DIR / "repo_name").write_text(
        f"{REPO_OWNER}\n",
        encoding="utf-8",
    )

    (METADATA_DIR / "layout.conf").write_text(
        "masters = gentoo\n"
        "auto-sync = false\n",
        encoding="utf-8",
    )

    Path(".nojekyll").write_text("", encoding="utf-8")

    Path(".gitignore").write_text(
        "*.gpkg.tar\n"
        "*.tbz2\n"
        "*.xpak\n"
        "temp_*\n",
        encoding="utf-8",
    )

    # Remove old generated Packages indexes.
    for name in (
        "Packages",
        "Packages.gz",
    ):
        path = BINHOST_DIR / name
        if path.exists():
            path.unlink()


# ---------------------------------------------------------------------------
# GitHub API
# ---------------------------------------------------------------------------

def github_get_json(url):
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "AthanorOS-Binhost/1.0",
            "Accept": "application/vnd.github+json",
        },
    )

    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_releases():
    print(f"Fetching releases from {API_URL} ...")

    try:
        return github_get_json(API_URL)
    except Exception as exc:
        raise RuntimeError(
            f"Could not fetch GitHub releases: {exc}"
        ) from exc


# ---------------------------------------------------------------------------
# Package metadata
# ---------------------------------------------------------------------------

def read_xpak_metadata(filepath):
    """
    Read CATEGORY/PN/PF from an old XPAK/TBZ2 package.
    """

    try:
        import portage.xpak

        xpak = portage.xpak.tbz2(str(filepath))

        category = xpak.get_data(b"CATEGORY")
        pn = xpak.get_data(b"PN")
        pf = xpak.get_data(b"PF")

        category = (
            category.decode("utf-8", errors="replace").strip()
            if category else None
        )

        pn = (
            pn.decode("utf-8", errors="replace").strip()
            if pn else None
        )

        pf = (
            pf.decode("utf-8", errors="replace").strip()
            if pf else None
        )

        return category, pn, pf

    except Exception:
        return None, None, None


def read_gpkg_metadata(filepath):
    """
    Read CATEGORY/PN/PF from a modern GPKG archive.

    GPKG is itself a tar archive containing metadata.
    """

    category = None
    pn = None
    pf = None

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)

        try:
            result = subprocess.run(
                [
                    "tar",
                    "-xf",
                    str(filepath),
                    "-C",
                    str(tmp),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

            if result.returncode != 0:
                return None, None, None

            metadata_archives = list(
                tmp.rglob("*metadata*.tar*")
            )

            for archive in metadata_archives:
                metadata_dir = tmp / "metadata"
                metadata_dir.mkdir(exist_ok=True)

                result = subprocess.run(
                    [
                        "tar",
                        "-xf",
                        str(archive),
                        "-C",
                        str(metadata_dir),
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )

                if result.returncode != 0:
                    continue

                for path in metadata_dir.rglob("*"):
                    if not path.is_file():
                        continue

                    if path.name == "CATEGORY" and category is None:
                        category = path.read_text(
                            encoding="utf-8",
                            errors="replace",
                        ).strip()

                    elif path.name == "PN" and pn is None:
                        pn = path.read_text(
                            encoding="utf-8",
                            errors="replace",
                        ).strip()

                    elif path.name == "PF" and pf is None:
                        pf = path.read_text(
                            encoding="utf-8",
                            errors="replace",
                        ).strip()

                if category and pn:
                    break

        except Exception:
            return None, None, None

    return category, pn, pf


def get_package_metadata(filepath):
    """
    Return CATEGORY, PN and PF.
    """

    name = filepath.name

    if name.endswith(".gpkg.tar"):
        category, pn, pf = read_gpkg_metadata(filepath)

    elif name.endswith((".tbz2", ".xpak")):
        category, pn, pf = read_xpak_metadata(filepath)

    else:
        return None, None, None

    # Some package formats don't expose PN cleanly.
    if not pn and pf:
        try:
            import portage.versions

            split = portage.versions.pkgsplit(pf)

            if split:
                pn = split[0]

        except Exception:
            pass

    if not pn and pf:
        match = re.match(
            r"^(.+?)-(\d+(?:\.\d+)*.*)$",
            pf,
        )

        if match:
            pn = match.group(1)

    return category, pn, pf


# ---------------------------------------------------------------------------
# Asset downloading
# ---------------------------------------------------------------------------

def download_asset(asset):
    """
    Download one package temporarily so its metadata can be inspected.
    """

    name = asset["name"]
    url = asset["browser_download_url"]

    TEMP_DIR.mkdir(parents=True, exist_ok=True)

    path = TEMP_DIR / name

    try:
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": "AthanorOS-Binhost/1.0",
            },
        )

        with urllib.request.urlopen(
            request,
            timeout=300,
        ) as response, open(path, "wb") as output:

            shutil.copyfileobj(response, output)

        return asset, path

    except Exception as exc:
        print(
            f"[warn] failed downloading {name}: {exc}"
        )
        return asset, None


# ---------------------------------------------------------------------------
# Release processing
# ---------------------------------------------------------------------------

def collect_packages(releases):
    """
    Collect package assets from GitHub releases.

    Newest release wins if the same filename exists in multiple releases.
    """

    packages = {}

    # GitHub normally returns newest releases first, but sort explicitly
    # so this doesn't depend on API ordering.
    releases = sorted(
        releases,
        key=lambda release: release.get(
            "published_at",
            release.get("created_at", ""),
        ),
        reverse=True,
    )

    for release in releases:
        tag = release.get("tag_name")

        if not tag:
            continue

        for asset in release.get("assets", []):
            name = asset.get("name", "")

            if not name.endswith(PACKAGE_EXTENSIONS):
                continue

            # Ignore signatures and weird generated artifacts.
            if name.endswith(".sig"):
                continue

            if name not in packages:
                packages[name] = {
                    "release": tag,
                    "asset": asset,
                }

    print(
        f"Found {len(packages)} unique binary package assets."
    )

    return list(packages.values())


# ---------------------------------------------------------------------------
# Packages index
# ---------------------------------------------------------------------------

def sha256_file(path):
    digest = hashlib.sha256()

    with open(path, "rb") as f:
        for chunk in iter(
            lambda: f.read(1024 * 1024),
            b"",
        ):
            digest.update(chunk)

    return digest.hexdigest()


def generate_packages_index(package_entries):
    """
    Generate a Portage Packages index.

    The important bit is that PATH contains:

        <release-tag>/<filename>

    while URI contains:

        https://github.com/.../releases/download/

    Therefore Portage constructs the real GitHub release URL directly.
    """

    print("Generating Portage Packages index...")

    entries = []

    for entry in package_entries:
        category = entry["category"]
        pn = entry["pn"]
        pf = entry["pf"]
        filename = entry["filename"]
        release = entry["release"]
        local_path = entry["local_path"]

        # Relative path from the GitHub release download endpoint.
        path = f"{release}/{filename}"

        stat = local_path.stat()

        # Portage Packages format.
        block = [
            f"CPV: {category}/{pf}",
            f"BUILD_TIME: {int(stat.st_mtime)}",
            f"SIZE: {stat.st_size}",
            f"MD5: {hashlib.md5(local_path.read_bytes()).hexdigest()}",
            f"SHA256: {sha256_file(local_path)}",
            f"CATEGORY: {category}",
            f"PN: {pn}",
            f"PF: {pf}",
            f"PATH: {path}",
        ]

        entries.append("\n".join(block))

    header = "\n".join(
        [
            "PACKAGES: 1",
            "TIMESTAMP: 0",
            f"URI: {RELEASE_DOWNLOAD_BASE}",
        ]
    )

    final = (
        header
        + "\n\n"
        + "\n\n".join(entries)
        + "\n"
    )

    packages_path = BINHOST_DIR / "Packages"
    packages_gz_path = BINHOST_DIR / "Packages.gz"

    packages_path.write_text(
        final,
        encoding="utf-8",
    )

    with gzip.open(
        packages_gz_path,
        "wb",
    ) as f:
        f.write(final.encode("utf-8"))

    print(
        f"Wrote {len(entries)} package entries."
    )


# ---------------------------------------------------------------------------
# Main metadata processing
# ---------------------------------------------------------------------------

def process_packages(package_assets):
    """
    Download packages concurrently, extract metadata, then remove them.

    Only metadata is retained in the final GitHub Pages tree.
    """

    if not package_assets:
        print("No packages found.")
        return []

    print(
        f"Inspecting metadata for "
        f"{len(package_assets)} packages..."
    )

    results = []

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [
            executor.submit(
                download_asset,
                entry["asset"],
            )
            for entry in package_assets
        ]

        for future in futures:
            asset, path = future.result()

            if path is None:
                continue

            category, pn, pf = get_package_metadata(path)

            if not category or not pn:
                print(
                    f"[warn] could not determine metadata "
                    f"for {asset['name']}"
                )
                path.unlink(missing_ok=True)
                continue

            if not pf:
                # PF should normally be available, but derive it from
                # the filename as a fallback.
                filename = asset["name"]

                pf = filename

                for suffix in (
                    ".gpkg.tar",
                    ".tbz2",
                    ".xpak",
                ):
                    if pf.endswith(suffix):
                        pf = pf[:-len(suffix)]
                        break

            results.append(
                {
                    "category": category,
                    "pn": pn,
                    "pf": pf,
                    "filename": asset["name"],
                    "release": package_assets[
                        next(
                            i
                            for i, item in enumerate(package_assets)
                            if item["asset"]["name"]
                            == asset["name"]
                        )
                    ]["release"],
                    "local_path": path,
                    "download_url": asset[
                        "browser_download_url"
                    ],
                }
            )

    return results


# ---------------------------------------------------------------------------
# Website
# ---------------------------------------------------------------------------

def generate_website(packages):
    print("Generating website...")

    packages = sorted(
        packages,
        key=lambda p: (
            p["category"],
            p["pn"],
            p["pf"],
        ),
    )

    rows = []

    for package in packages:
        url = (
            RELEASE_DOWNLOAD_BASE
            + package["release"]
            + "/"
            + package["filename"]
        )

        rows.append(
            f"""
        <li>
            <div class="package">
                <span class="category">
                    {package["category"]}/{package["pn"]}
                </span>
                <a href="{url}">
                    {package["filename"]}
                </a>
            </div>
        </li>
"""
        )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport"
      content="width=device-width, initial-scale=1.0">
<title>{REPO_OWNER} Gentoo Binhost</title>

<style>
body {{
    font-family: system-ui, sans-serif;
    margin: 2rem auto;
    max-width: 900px;
    background: #1e1e1e;
    color: #e0e0e0;
    padding: 0 1rem;
}}

a {{
    color: #66b3ff;
    text-decoration: none;
}}

a:hover {{
    text-decoration: underline;
}}

h1, h2 {{
    border-bottom: 1px solid #444;
    padding-bottom: .5rem;
}}

ul {{
    list-style: none;
    padding: 0;
}}

li {{
    margin: .5rem 0;
    background: #2a2a2a;
    padding: .8rem 1rem;
    border-radius: 6px;
}}

.package {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 1rem;
    flex-wrap: wrap;
}}

.category {{
    font-weight: bold;
    color: #a0c4ff;
}}

code, pre {{
    background: #000;
    border-radius: 4px;
}}

code {{
    padding: .2rem .4rem;
}}

pre {{
    padding: 1rem;
    overflow-x: auto;
}}
</style>
</head>

<body>

<h1>{REPO_OWNER} Gentoo Binhost</h1>

<p>
Automated Gentoo binary package host for AthanorOS.
</p>

<h2>Configuration</h2>

<p>
Add this to
<code>/etc/portage/binrepos.conf/athanor.conf</code>:
</p>

<pre>[athanor]
priority = 9999
sync-uri = https://{REPO_OWNER}.github.io/binhost
</pre>

<p>
Or, for older Portage installations:
</p>

<pre>PORTAGE_BINHOST="https://{REPO_OWNER}.github.io/binhost"</pre>

<h2>Available Packages ({len(packages)})</h2>

<ul>
{"".join(rows)}
</ul>

</body>
</html>
"""

    Path("index.html").write_text(
        html,
        encoding="utf-8",
    )


def generate_binhost_page():
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>{REPO_OWNER} Binhost</title>
</head>

<body>

<h1>{REPO_OWNER} Gentoo Binary Package Host</h1>

<p>
Portage binary package index.
</p>

<ul>
<li>
<a href="Packages">Packages</a>
</li>

<li>
<a href="Packages.gz">Packages.gz</a>
</li>
</ul>

</body>
</html>
"""

    (BINHOST_DIR / "index.html").write_text(
        html,
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    setup_directories()

    releases = fetch_releases()

    package_assets = collect_packages(
        releases
    )

    packages = process_packages(
        package_assets
    )

    if not packages:
        raise RuntimeError(
            "No packages with readable metadata were found."
        )

    # The release URL is deterministic, so don't rely on browser_download_url
    # for the Packages index.
    generate_packages_index(
        packages
    )

    generate_website(
        packages
    )

    generate_binhost_page()

    # We don't need the downloaded packages anymore.
    shutil.rmtree(
        TEMP_DIR,
        ignore_errors=True,
    )

    print()
    print("========================================")
    print("AthanorOS binhost generation complete")
    print("========================================")
    print(
        f"Packages: {len(packages)}"
    )
    print(
        f"Binhost: https://{REPO_OWNER}.github.io/binhost"
    )
    print(
        f"Release base: {RELEASE_DOWNLOAD_BASE}"
    )


if __name__ == "__main__":
    main()
