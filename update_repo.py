import os
import json
import tarfile
import urllib.request
import subprocess
import shutil
import gzip
import tempfile
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

    with open(os.path.join(PROFILES_DIR, "repo_name"), "w") as f:
        f.write(f"{REPO_OWNER}\n")
    
    with open(os.path.join(METADATA_DIR, "layout.conf"), "w") as f:
        f.write("masters = gentoo\nauto-sync = false\n")

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
    Extract CATEGORY and PN from modern Gentoo GPKG or legacy TBZ2 packages
    by extracting metadata to a temporary sandbox directory.
    """
    category, pn = None, None
    filename = os.path.basename(filepath)

    # 1. Handle modern GPKG archives (.gpkg.tar)
    if filepath.endswith('.gpkg.tar'):
        with tempfile.TemporaryDirectory() as extract_dir:
            try:
                # Extract the outer archive
                subprocess.run(
                    ["tar", "-xf", filepath, "-C", extract_dir],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=True
                )
                
                # Locate metadata.tar.* inside the extracted tree
                meta_archive = None
                for root, dirs, files in os.walk(extract_dir):
                    for f in files:
                        if "metadata.tar" in f:
                            meta_archive = os.path.join(root, f)
                            break
                    if meta_archive:
                        break

                if meta_archive:
                    meta_extract_dir = os.path.join(extract_dir, "meta_extracted")
                    os.makedirs(meta_extract_dir, exist_ok=True)
                    
                    # Unpack metadata.tar.* (supports .zst, .gz, .xz, etc.)
                    subprocess.run(
                        ["tar", "-xf", meta_archive, "-C", meta_extract_dir],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        check=True
                    )

                    # Read CATEGORY and PN
                    cat_file = os.path.join(meta_extract_dir, "CATEGORY")
                    pn_file = os.path.join(meta_extract_dir, "PN")

                    if os.path.exists(cat_file):
                        with open(cat_file, "r", encoding="utf-8", errors="ignore") as f:
                            category = f.read().strip()

                    if os.path.exists(pn_file):
                        with open(pn_file, "r", encoding="utf-8", errors="ignore") as f:
                            pn = f.read().strip()

            except Exception as e:
                print(f"Error parsing GPKG {filename}: {e}")

    # 2. Handle legacy TBZ2 / XPAK packages
    elif filepath.endswith(('.tbz2', '.xpak')):
        try:
            import portage.xpak
            xpak = portage.xpak.tbz2(filepath)
            cat_data = xpak.get_data(b"CATEGORY")
            pn_data = xpak.get_data(b"PN")
            if cat_data:
                category = cat_data.decode('utf-8', errors='ignore').strip()
            if pn_data:
                pn = pn_data.decode('utf-8', errors='ignore').strip()
        except Exception:
            pass

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
            
            # Skip invalid filenames (e.g. gtk.-3...)
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
            print(f"Successfully sorted: {cat}/{pn}/{name}")
        else:
            print(f"Warning: Could not identify metadata for {name}")

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

            # Inject remote URL into each package header
            entries = content.split("\n\n")
            new_entries = []
            for entry in entries:
                if not entry.strip():
                    continue
                lines = entry.splitlines()
                pkg_filename = None
                for line in lines:
                    if line.startswith("PATH:"):
                        pkg_filename = os.path.basename(line.split(":", 1)[1].strip())
                        break
                if pkg_filename and pkg_filename in asset_url_map:
                    lines.append(f"URI: {asset_url_map[pkg_filename]}")
                new_entries.append("\n".join(lines))

            final_packages = "\n\n".join(new_entries) + "\n"

            with open(os.path.join(BINHOST_DIR, "Packages"), "w", encoding="utf-8") as f:
                f.write(final_packages)

            with gzip.open(os.path.join(BINHOST_DIR, "Packages.gz"), "wb") as f:
                f.write(final_packages.encode('utf-8'))

            print("Packages and Packages.gz created successfully with proper categories!")
    except Exception as e:
        print(f"emaint binhost generation error: {e}")
    finally:
        # Delete temporary binpkgs so git stays clean (< 2 MB)
        shutil.rmtree(TEMP_BINHOST, ignore_errors=True)

def generate_website(pkgs):
    print("Generating HTML website...")
    html = f'''<!DOCTYPE html>
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
        html += f'''
        <li>
            <div class="package">
                <span class="category">{cat}/{pn}</span>
                <a href="{download_url}">{name}</a>
            </div>
        </li>'''

    html += '''
    </ul>
</body>
</html>
'''
    with open("index.html", "w") as f:
        f.write(html)

if __name__ == "__main__":
    setup_directories()
    pkgs, asset_url_map = fetch_and_organize_binpkgs()
    generate_packages_index(asset_url_map)
    generate_website(pkgs)
    print("Build complete!")
