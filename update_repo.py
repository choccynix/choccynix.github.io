import os
import json
import urllib.request
import subprocess
import shutil
import gzip
import tempfile
import re
from concurrent.futures import ThreadPoolExecutor

# --- Configuration ---
REPO_OWNER = "choccynix"
BINPKG_REPO = f"{REPO_OWNER}/athanor-binpkgs"
API_URL = f"https://api.github.com/repos/{BINPKG_REPO}/releases?per_page=100"

BINHOST_DIR = "binhost"
PROFILES_DIR = "profiles"
METADATA_DIR = "metadata"
TEMP_DIR = "temp_downloads"
TEMP_BINHOST = "temp_binhost"

def setup_directories():
    """Create repository structure and clean up previous artifacts."""
    os.makedirs(BINHOST_DIR, exist_ok=True)
    os.makedirs(PROFILES_DIR, exist_ok=True)
    os.makedirs(METADATA_DIR, exist_ok=True)
    os.makedirs("/var/db/repos/gentoo", exist_ok=True)  # Silence Portage warning in stage3

    with open(os.path.join(PROFILES_DIR, "repo_name"), "w") as f:
        f.write(f"{REPO_OWNER}\n")
    
    with open(os.path.join(METADATA_DIR, "layout.conf"), "w") as f:
        f.write("masters = gentoo\nauto-sync = false\n")

    # Tell GitHub Pages not to use Jekyll
    with open(".nojekyll", "w") as f:
        f.write("")

    with open(".gitignore", "w") as f:
        f.write("*.gpkg.tar\n*.tbz2\n*.xpak\ntemp_*\n")

    # Remove loose package files from previous runs
    for root, dirs, files in os.walk(BINHOST_DIR):
        for f in files:
            if f.endswith(('.gpkg.tar', '.tbz2', '.xpak')):
                try:
                    os.remove(os.path.join(root, f))
                except Exception:
                    pass

def get_pkg_metadata(filepath):
    """
    Extract CATEGORY and PN from modern Gentoo GPKG packages.
    If PN is missing, calculates it dynamically from PF using Portage.
    """
    category, pn, pf = None, None, None
    filename = os.path.basename(filepath)

    if filepath.endswith('.gpkg.tar'):
        with tempfile.TemporaryDirectory() as tmpdir:
            try:
                # 1. Unpack outer tar
                res = subprocess.run(["tar", "-xf", filepath, "-C", tmpdir], capture_output=True, text=True)
                if res.returncode != 0:
                    print(f"Outer tar extraction failed for {filename}: {res.stderr.strip()}")
                    return None, None

                # 2. Locate metadata.tar*
                meta_archive = None
                for root, dirs, files in os.walk(tmpdir):
                    for f in files:
                        if "metadata.tar" in f:
                            meta_archive = os.path.join(root, f)
                            break
                    if meta_archive: break

                if meta_archive:
                    meta_dir = os.path.join(tmpdir, "meta_extracted")
                    os.makedirs(meta_dir, exist_ok=True)

                    # 3. Unpack inner tar (GNU tar automatically handles .zst, .gz, .xz, etc.)
                    res2 = subprocess.run(["tar", "-xf", meta_archive, "-C", meta_dir], capture_output=True, text=True)
                    if res2.returncode != 0:
                        print(f"Inner tar extraction failed for {filename}: {res2.stderr.strip()}")
                        return None, None

                    # 4. Find CATEGORY, PN, and PF
                    for root, dirs, files in os.walk(meta_dir):
                        if "CATEGORY" in files and not category:
                            with open(os.path.join(root, "CATEGORY"), "r", encoding="utf-8", errors="ignore") as f:
                                category = f.read().strip()
                        if "PN" in files and not pn:
                            with open(os.path.join(root, "PN"), "r", encoding="utf-8", errors="ignore") as f:
                                pn = f.read().strip()
                        if "PF" in files and not pf:
                            with open(os.path.join(root, "PF"), "r", encoding="utf-8", errors="ignore") as f:
                                pf = f.read().strip()

            except Exception as e:
                print(f"Exception extracting {filename}: {e}")

    elif filepath.endswith(('.tbz2', '.xpak')):
        try:
            import portage.xpak
            xpak = portage.xpak.tbz2(filepath)
            cat_data = xpak.get_data(b"CATEGORY")
            pn_data = xpak.get_data(b"PN")
            pf_data = xpak.get_data(b"PF")
            if cat_data:
                category = cat_data.decode('utf-8', errors='ignore').strip()
            if pn_data:
                pn = pn_data.decode('utf-8', errors='ignore').strip()
            if pf_data:
                pf = pf_data.decode('utf-8', errors='ignore').strip()
        except Exception:
            pass

    # 5. Calculate PN from PF if PN was not saved by Portage
    if category and not pn and pf:
        try:
            import portage.versions
            splitted = portage.versions.pkgsplit(pf)
            if splitted:
                pn = splitted[0]
        except Exception:
            pass
            
        if not pn:
            match = re.match(r'^(.+?)-(\d+.*)$', pf)
            if match:
                pn = match.group(1)

    if not category or not pn:
        print(f"Missing crucial metadata in {filename} (Category: {category}, PN: {pn}, PF: {pf})")

    return category, pn

def download_asset(asset_info):
    name, url = asset_info
    temp_path = os.path.join(TEMP_DIR, name)
    try:
        urllib.request.urlretrieve(url, temp_path)
        return name, temp_path
    except Exception as e:
        print(f"Failed to download {name}: {e}")
        return name, None

def fetch_and_organize_binpkgs():
    print(f"Fetching releases from {API_URL} ...")
    req = urllib.request.Request(API_URL, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        with urllib.request.urlopen(req) as response:
            releases = json.loads(response.read().decode())
    except Exception as e:
        print(f"Error fetching API releases: {e}")
        return [], {}

    asset_url_map = {}
    to_download = []
    
    for release in releases:
        for asset in release.get('assets', []):
            name = asset['name']
            url = asset['browser_download_url']
            
            if name.endswith(('.gpkg.tar', '.tbz2', '.xpak')) and ".-" not in name:
                asset_url_map[name] = url
                to_download.append((name, url))

    os.makedirs(TEMP_DIR, exist_ok=True)
    os.makedirs(TEMP_BINHOST, exist_ok=True)

    print(f"Downloading and extracting metadata for {len(to_download)} packages (8 threads)...")
    with ThreadPoolExecutor(max_workers=8) as executor:
        downloaded = list(executor.map(download_asset, to_download))

    all_pkgs = []
    for name, temp_path in downloaded:
        if not temp_path or not os.path.exists(temp_path):
            continue
            
        cat, pn = get_pkg_metadata(temp_path)
        download_url = asset_url_map.get(name, "")

        if cat and pn:
            target_dir = os.path.join(TEMP_BINHOST, cat, pn)
            os.makedirs(target_dir, exist_ok=True)
            target_path = os.path.join(target_dir, name)
            shutil.move(temp_path, target_path)
            all_pkgs.append((cat, pn, name, download_url))
            print(f"Sorted: {cat}/{pn}/{name}")
        else:
            print(f"Could not identify metadata for {name}, skipping.")

    shutil.rmtree(TEMP_DIR, ignore_errors=True)
    return all_pkgs, asset_url_map

def generate_packages_index(asset_url_map):
    print("Generating Portage 'Packages' index via emaint...")
    try:
        env = os.environ.copy()
        env['PKGDIR'] = os.path.abspath(TEMP_BINHOST)
        subprocess.run(["emaint", "binhost", "--fix"], env=env, check=True)

        packages_src = os.path.join(TEMP_BINHOST, "Packages")
        if os.path.exists(packages_src):
            with open(packages_src, "r", encoding="utf-8") as f:
                content = f.read()

            entries = content.split("\n\n")
            new_entries = []
            for entry in entries:
                if not entry.strip():
                    continue
                lines = entry.splitlines()

                # Preserve the top-level PACKAGES header
                if lines[0].strip() == "PACKAGES":
                    new_entries.append("\n".join(lines))
                    continue

                new_lines = []
                for line in lines:
                    if line.startswith("PATH:"):
                        pkg_filename = os.path.basename(line.split(":", 1)[1].strip())
                        if pkg_filename in asset_url_map:
                            # Point PATH directly to the GitHub Releases URL
                            new_lines.append(f"PATH: {asset_url_map[pkg_filename]}")
                            new_lines.append(f"URI: {asset_url_map[pkg_filename]}")
                        else:
                            new_lines.append(line)
                    else:
                        new_lines.append(line)
                new_entries.append("\n".join(new_lines))

            final_packages = "\n\n".join(new_entries) + "\n"

            with open(os.path.join(BINHOST_DIR, "Packages"), "w", encoding="utf-8") as f:
                f.write(final_packages)

            with gzip.open(os.path.join(BINHOST_DIR, "Packages.gz"), "wb") as f:
                f.write(final_packages.encode('utf-8'))

            print("Packages index generated successfully with remote GitHub URLs.")
    except Exception as e:
        print(f"emaint binhost generation error: {e}")
    finally:
        shutil.rmtree(TEMP_BINHOST, ignore_errors=True)

def generate_website(pkgs):
    print("Generating HTML websites...")
    
    # 1. Main Home Page (/)
    main_html = f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{REPO_OWNER} Gentoo Repository & Binhost</title>
    <style>
        body {{ font-family: system-ui, -apple-system, sans-serif; margin: 2rem auto; max-width: 850px; background: #1e1e1e; color: #e0e0e0; padding: 0 1rem; }}
        a {{ color: #66b3ff; text-decoration: none; }}
        a:hover {{ text-decoration: underline; }}
        h1, h2 {{ border-bottom: 1px solid #444; padding-bottom: 0.5rem; }}
        ul {{ list-style: none; padding: 0; }}
        li {{ margin: 0.5rem 0; background: #2a2a2a; padding: 0.8rem 1rem; border-radius: 6px; }}
        .package {{ display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px; }}
        .category {{ font-weight: bold; color: #a0c4ff; }}
        code {{ background: #000; padding: 0.2rem 0.4rem; border-radius: 4px; font-size: 0.9em; }}
        pre {{ background: #000; padding: 1rem; border-radius: 6px; overflow-x: auto; border: 1px solid #333; }}
    </style>
</head>
<body>
    <h1>{REPO_OWNER} Gentoo Repository & Binhost</h1>
    <p>This is an automated Portage repository and binary package host.</p>
    
    <h2>How to Use the Binhost</h2>
    <p>Add the following lines to your <code>/etc/portage/make.conf</code>:</p>
    <pre>
PORTAGE_BINHOST="https://{REPO_OWNER}.github.io/binhost"
EMERGE_DEFAULT_OPTS="${{EMERGE_DEFAULT_OPTS}} --getbinpkg"
    </pre>

    <h2>Available Packages ({len(pkgs)})</h2>
    <ul>
'''
    pkgs.sort(key=lambda x: (x[0], x[1], x[2]))
    for cat, pn, name, download_url in pkgs:
        main_html += f'''
        <li>
            <div class="package">
                <span class="category">{cat}/{pn}</span>
                <a href="{download_url}">{name}</a>
            </div>
        </li>'''

    main_html += '''
    </ul>
</body>
</html>
'''
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(main_html)

    # 2. Dedicated Binhost Directory Page (/binhost/) to prevent 404s
    binhost_html = f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Gentoo Binhost Index - {REPO_OWNER}</title>
    <style>
        body {{ font-family: system-ui, -apple-system, sans-serif; margin: 2rem auto; max-width: 850px; background: #1e1e1e; color: #e0e0e0; padding: 0 1rem; }}
        a {{ color: #66b3ff; text-decoration: none; }}
        a:hover {{ text-decoration: underline; }}
        h1, h2 {{ border-bottom: 1px solid #444; padding-bottom: 0.5rem; }}
        pre {{ background: #000; padding: 1rem; border-radius: 6px; overflow-x: auto; border: 1px solid #333; }}
        ul {{ list-style: none; padding: 0; }}
        li {{ margin: 0.5rem 0; background: #2a2a2a; padding: 0.8rem 1rem; border-radius: 6px; }}
        code {{ background: #000; padding: 0.2rem 0.4rem; border-radius: 4px; font-size: 0.9em; }}
    </style>
</head>
<body>
    <h1>Gentoo Binhost Endpoint</h1>
    <p>This directory serves the Portage binary package index for <code>{REPO_OWNER}</code>.</p>
    
    <h2>Configuration</h2>
    <p>Add the following to your <code>/etc/portage/make.conf</code>:</p>
    <pre>
PORTAGE_BINHOST="https://{REPO_OWNER}.github.io/binhost"
EMERGE_DEFAULT_OPTS="${{EMERGE_DEFAULT_OPTS}} --getbinpkg"
    </pre>

    <h2>Index Files</h2>
    <ul>
        <li>📄 <a href="Packages">Packages (Plain Text Index)</a></li>
        <li>📦 <a href="Packages.gz">Packages.gz (Compressed Index)</a></li>
    </ul>

    <p><a href="../">&larr; Return to main package catalog</a></p>
</body>
</html>
'''
    with open(os.path.join(BINHOST_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(binhost_html)

if __name__ == "__main__":
    setup_directories()
    pkgs, asset_url_map = fetch_and_organize_binpkgs()
    generate_packages_index(asset_url_map)
    generate_website(pkgs)
    print("Build complete!")
