#!/usr/bin/env python3

import gzip
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


# ============================================================================
# Configuration
# ============================================================================

REPO_OWNER = "choccynix"
BINPKG_REPO = f"{REPO_OWNER}/athanor-binpkgs"

API_URL = (
    f"https://api.github.com/repos/{BINPKG_REPO}"
    "/releases?per_page=100"
)

# GitHub Releases download endpoint.
#
# Final package URL:
#
# https://github.com/choccynix/athanor-binpkgs/releases/download/
#     <release-tag>/<filename>
#
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

MAX_DOWNLOAD_THREADS = 8


# ============================================================================
# Repository setup
# ============================================================================

def setup_directories():
    """Create the generated repository structure."""

    BINHOST_DIR.mkdir(parents=True, exist_ok=True)
    PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    METADATA_DIR.mkdir(parents=True, exist_ok=True)

    # Compatibility with the existing build environment.
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

    Path(".nojekyll").write_text(
        "",
        encoding="utf-8",
    )

    Path(".gitignore").write_text(
        "*.gpkg.tar\n"
        "*.tbz2\n"
        "*.xpak\n"
        "temp_*\n",
        encoding="utf-8",
    )

    # Remove stale generated indexes.
    for filename in (
        "Packages",
        "Packages.gz",
    ):
        path = BINHOST_DIR / filename

        if path.exists():
            path.unlink()


# ============================================================================
# GitHub API
# ============================================================================

def github_get_json(url):
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "AthanorOS-Binhost/1.0",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )

    with urllib.request.urlopen(
        request,
        timeout=60,
    ) as response:
        return json.loads(
            response.read().decode("utf-8")
        )


def fetch_releases():
    print(
        f"Fetching releases from {API_URL} ..."
    )

    try:
        releases = github_get_json(API_URL)
    except Exception as exc:
        raise RuntimeError(
            f"Failed to fetch GitHub releases: {exc}"
        ) from exc

    if not isinstance(releases, list):
        raise RuntimeError(
            "GitHub API did not return a release list."
        )

    print(
        f"Found {len(releases)} releases."
    )

    return releases


# ============================================================================
# Package metadata
# ============================================================================

def read_xpak_metadata(filepath):
    """Read CATEGORY/PN/PF from TBZ2/XPAK."""

    try:
        import portage.xpak

        xpak = portage.xpak.tbz2(
            str(filepath)
        )

        category = xpak.get_data(
            b"CATEGORY"
        )

        pn = xpak.get_data(
            b"PN"
        )

        pf = xpak.get_data(
            b"PF"
        )

        category = (
            category.decode(
                "utf-8",
                errors="replace",
            ).strip()
            if category
            else None
        )

        pn = (
            pn.decode(
                "utf-8",
                errors="replace",
            ).strip()
            if pn
            else None
        )

        pf = (
            pf.decode(
                "utf-8",
                errors="replace",
            ).strip()
            if pf
            else None
        )

        return category, pn, pf

    except Exception:
        return None, None, None


def read_gpkg_metadata(filepath):
    """
    Extract CATEGORY/PN/PF from a modern GPKG package.
    """

    category = None
    pn = None
    pf = None

    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)

        try:
            result = subprocess.run(
                [
                    "tar",
                    "-xf",
                    str(filepath),
                    "-C",
                    str(tmpdir),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

            if result.returncode != 0:
                return None, None, None

            metadata_archives = []

            for path in tmpdir.rglob("*"):
                if not path.is_file():
                    continue

                if "metadata.tar" in path.name:
                    metadata_archives.append(path)

            for archive in metadata_archives:
                metadata_dir = (
                    tmpdir / "metadata_extracted"
                )

                metadata_dir.mkdir(
                    parents=True,
                    exist_ok=True,
                )

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

                    if path.name == "CATEGORY":
                        if category is None:
                            category = path.read_text(
                                encoding="utf-8",
                                errors="replace",
                            ).strip()

                    elif path.name == "PN":
                        if pn is None:
                            pn = path.read_text(
                                encoding="utf-8",
                                errors="replace",
                            ).strip()

                    elif path.name == "PF":
                        if pf is None:
                            pf = path.read_text(
                                encoding="utf-8",
                                errors="replace",
                            ).strip()

                if category and pn:
                    break

        except Exception:
            return None, None, None

    return category, pn, pf


def derive_pn_from_pf(pf):
    """Fallback PN extraction."""

    if not pf:
        return None

    try:
        import portage.versions

        split = portage.versions.pkgsplit(
            pf
        )

        if split:
            return split[0]

    except Exception:
        pass

    match = re.match(
        r"^(.+?)-"
        r"(\d+(?:\.\d+)*"
        r"(?:[a-z]+)?"
        r"(?:[-_].*)?)$",
        pf,
    )

    if match:
        return match.group(1)

    return None


def get_package_metadata(filepath):
    """Return CATEGORY, PN and PF."""

    filename = filepath.name

    if filename.endswith(".gpkg.tar"):
        category, pn, pf = (
            read_gpkg_metadata(filepath)
        )

    elif filename.endswith(
        (".tbz2", ".xpak")
    ):
        category, pn, pf = (
            read_xpak_metadata(filepath)
        )

    else:
        return None, None, None

    if not pn and pf:
        pn = derive_pn_from_pf(pf)

    return category, pn, pf


# ============================================================================
# Downloading
# ============================================================================

def download_asset(job):
    """
    Download a package temporarily so its metadata can be inspected.
    """

    asset = job["asset"]

    name = asset["name"]
    url = asset["browser_download_url"]

    TEMP_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

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
        ) as response:

            with open(path, "wb") as output:
                shutil.copyfileobj(
                    response,
                    output,
                )

        return job, path

    except Exception as exc:
        print(
            f"[WARN] failed to download "
            f"{name}: {exc}"
        )

        try:
            path.unlink()
        except FileNotFoundError:
            pass

        return job, None


# ============================================================================
# Release/package collection
# ============================================================================

def collect_packages(releases):
    """
    Find all binary package assets.

    Releases are processed newest first. If the same filename exists
    in multiple releases, the newest release wins.
    """

    releases = sorted(
        releases,
        key=lambda release: (
            release.get(
                "published_at",
                release.get(
                    "created_at",
                    "",
                ),
            )
        ),
        reverse=True,
    )

    packages = {}

    for release in releases:
        tag = release.get(
            "tag_name"
        )

        if not tag:
            continue

        for asset in release.get(
            "assets",
            [],
        ):
            name = asset.get(
                "name",
                "",
            )

            if not name.endswith(
                PACKAGE_EXTENSIONS
            ):
                continue

            if name in packages:
                continue

            packages[name] = {
                "release": tag,
                "asset": asset,
            }

    result = list(
        packages.values()
    )

    print(
        f"Found {len(result)} unique "
        f"binary package assets."
    )

    return result


# ============================================================================
# Hashing
# ============================================================================

def hash_file(path, algorithm):
    digest = hashlib.new(
        algorithm
    )

    with open(path, "rb") as f:
        while True:
            chunk = f.read(
                1024 * 1024
            )

            if not chunk:
                break

            digest.update(chunk)

    return digest.hexdigest()


# ============================================================================
# Process package metadata
# ============================================================================

def process_packages(package_jobs):
    """
    Download packages concurrently, inspect metadata,
    and return the metadata needed by Packages.
    """

    if not package_jobs:
        return []

    print(
        f"Inspecting metadata for "
        f"{len(package_jobs)} packages "
        f"using {MAX_DOWNLOAD_THREADS} threads..."
    )

    processed = []

    with ThreadPoolExecutor(
        max_workers=MAX_DOWNLOAD_THREADS
    ) as executor:

        futures = [
            executor.submit(
                download_asset,
                job,
            )
            for job in package_jobs
        ]

        for future in futures:
            job, path = future.result()

            if path is None:
                continue

            asset = job["asset"]
            release = job["release"]

            category, pn, pf = (
                get_package_metadata(path)
            )

            if not category or not pn:
                print(
                    f"[WARN] unable to determine "
                    f"metadata for {asset['name']}"
                )

                try:
                    path.unlink()
                except FileNotFoundError:
                    pass

                continue

            if not pf:
                pf = asset["name"]

                for suffix in (
                    ".gpkg.tar",
                    ".tbz2",
                    ".xpak",
                ):
                    if pf.endswith(suffix):
                        pf = pf[
                            :-len(suffix)
                        ]
                        break

            processed.append(
                {
                    "category": category,
                    "pn": pn,
                    "pf": pf,
                    "filename": asset["name"],
                    "release": release,
                    "local_path": path,
                    "download_url": asset[
                        "browser_download_url"
                    ],
                }
            )

    return processed


# ============================================================================
# Packages index
# ============================================================================

def generate_packages_index(packages):
    """
    Generate a Portage Packages index.

    IMPORTANT:

    Each package has its own release URI:

        URI: https://github.com/.../releases/download/<TAG>/

    and its PATH is only the release asset name:

        PATH: package-version.gpkg.tar

    This produces:

        https://github.com/.../releases/download/<TAG>/package-version.gpkg.tar

    Do NOT run `emaint binhost --fix` against this afterward.
    """

    print(
        "Generating Portage Packages index..."
    )

    packages = sorted(
        packages,
        key=lambda package: (
            package["category"],
            package["pn"],
            package["pf"],
        ),
    )

    blocks = []

    for package in packages:
        category = package["category"]
        pn = package["pn"]
        pf = package["pf"]
        filename = package["filename"]
        release = package["release"]
        local_path = package["local_path"]

        stat = local_path.stat()

        md5 = hash_file(
            local_path,
            "md5",
        )

        sha256 = hash_file(
            local_path,
            "sha256",
        )

        # ================================================================
        # THE IMPORTANT URL FIX
        # ================================================================

        package_uri = (
            RELEASE_DOWNLOAD_BASE
            + release
            + "/"
        )

        package_path = filename

        block = [
            f"CPV: {category}/{pf}",
            f"BUILD_TIME: {int(stat.st_mtime)}",
            f"SIZE: {stat.st_size}",
            f"MD5: {md5}",
            f"SHA256: {sha256}",
            f"CATEGORY: {category}",
            f"PN: {pn}",
            f"PF: {pf}",
            f"URI: {package_uri}",
            f"PATH: {package_path}",
        ]

        blocks.append(
            "\n".join(block)
        )

    # ========================================================================
    # IMPORTANT PACKAGE INDEX HEADER
    #
    # VERSION is required by Portage.
    # TIMESTAMP must be a real timestamp.
    #
    # There is intentionally NO global URI here because packages may
    # reside in different GitHub releases.
    # ========================================================================

    timestamp = int(
        time.time()
    )

    header = "\n".join(
        [
            "PACKAGES: 1",
            "VERSION: 1",
            f"TIMESTAMP: {timestamp}",
        ]
    )

    final = (
        header
        + "\n\n"
        + "\n\n".join(blocks)
        + "\n"
    )

    packages_path = (
        BINHOST_DIR / "Packages"
    )

    packages_gz_path = (
        BINHOST_DIR / "Packages.gz"
    )

    packages_path.write_text(
        final,
        encoding="utf-8",
    )

    with gzip.open(
        packages_gz_path,
        "wb",
    ) as output:
        output.write(
            final.encode("utf-8")
        )

    print(
        f"Wrote {len(blocks)} package entries."
    )

    # Print one URL so the Actions log makes debugging easy.
    if packages:
        first = packages[0]

        test_url = (
            RELEASE_DOWNLOAD_BASE
            + first["release"]
            + "/"
            + first["filename"]
        )

        print(
            f"Example package URL: {test_url}"
        )


# ============================================================================
# Website
# ============================================================================

def generate_website(packages):
    """Generate the main package catalog."""

    packages = sorted(
        packages,
        key=lambda package: (
            package["category"],
            package["pn"],
            package["pf"],
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
    font-family:
        system-ui,
        -apple-system,
        sans-serif;

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

<h2>Portage configuration</h2>

<p>
For modern Portage, create:
</p>

<pre>/etc/portage/binrepos.conf/athanor.conf</pre>

<pre>[athanor]
priority = 9999
sync-uri = https://{REPO_OWNER}.github.io/binhost
</pre>

<p>
For older Portage configurations:
</p>

<pre>PORTAGE_BINHOST="https://{REPO_OWNER}.github.io/binhost"</pre>

<h2>Available Packages ({len(packages)})</h2>

<ul>
{"".join(rows)}
</ul>

</body>
</html>
"""

    Path(
        "index.html"
    ).write_text(
        html,
        encoding="utf-8",
    )


def generate_binhost_page():
    """Generate the binhost directory index."""

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
<a href="Packages">
Packages
</a>
</li>

<li>
<a href="Packages.gz">
Packages.gz
</a>
</li>

</ul>

</body>

</html>
"""

    (
        BINHOST_DIR / "index.html"
    ).write_text(
        html,
        encoding="utf-8",
    )


# ============================================================================
# Main
# ============================================================================

def main():
    setup_directories()

    releases = fetch_releases()

    package_jobs = collect_packages(
        releases
    )

    packages = process_packages(
        package_jobs
    )

    if not packages:
        raise RuntimeError(
            "No packages with readable metadata "
            "were found."
        )

    generate_packages_index(
        packages
    )

    generate_website(
        packages
    )

    generate_binhost_page()

    # Delete downloaded packages.
    shutil.rmtree(
        TEMP_DIR,
        ignore_errors=True,
    )

    print()
    print(
        "========================================"
    )
    print(
        "AthanorOS binhost generation complete"
    )
    print(
        "========================================"
    )

    print(
        f"Packages: {len(packages)}"
    )

    print(
        "Binhost:"
        f" https://{REPO_OWNER}.github.io/binhost"
    )

    print(
        "Release base:"
        f" {RELEASE_DOWNLOAD_BASE}"
    )


if __name__ == "__main__":
    main()
