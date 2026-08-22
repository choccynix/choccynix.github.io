import os
import json
import tarfile
import urllib.request
import subprocess
import shutil
import io
from concurrent.futures import ThreadPoolExecutor

# --- Configuration ---
REPO_OWNER = "choccynix"
BINPKG_REPO = f"{REPO_OWNER}/athanor-binpkgs"
API_URL = f"https://api.github.com/repos/{BINPKG_REPO}/releases?per_page=100"

BINHOST_DIR = "binhost"
PROFILES_DIR = "profiles"
METADATA_DIR = "metadata"
TEMP_DIR = "temp_downloads"

def setup_directories():
    """Create basic Gentoo repository structure."""
    os.makedirs(BINHOST_DIR, exist_ok=True)
    os.makedirs(PROFILES_DIR, exist_ok=True)
    os.makedirs(METADATA_DIR, exist_ok=True)

    if not os.path.exists(os.path.join(PROFILES_DIR, "repo_name")):
        with open(os.path.join(PROFILES_DIR, "repo_name"), "w") as f:
            f.write(f"{REPO_OWNER}\n")
    
    if not os.path.exists(os.path.join(METADATA_DIR, "layout.conf")):
        with open(os.path.join(METADATA_DIR, "layout.conf"), "w") as f:
            f.write("masters = gentoo\nauto-sync = false\n")

def get_pkg_metadata(filepath):
    """Extract CATEGORY and PN from modern Gentoo GPKG or legacy TBZ2 packages."""
    category, pn = None, None
    filename = os.path.basename(filepath)

    if filepath.endswith('.gpkg.tar'):
        # 1. Try Portage's native gpkg parser (available in gentoo/stage3)
        try:
            import portage
            from portage.gpkg import gpkg
            pkg = gpkg(portage.settings, filename, filepath)
            cat_data = pkg.get_metadata("CATEGORY")
            pn_data = pkg.get_metadata("PN")
            if cat_data:
                category = cat_data.decode('utf-8', errors='ignore').strip() if isinstance(cat_data, bytes) else str(cat_data).strip()
            if pn_data:
                pn = pn_data.decode('utf-8', errors='ignore').strip() if isinstance(pn_data, bytes) else str(pn_data).strip()
            if category and pn:
                return category, pn
        except Exception:
            pass

        # 2. Fallback: Decompress the nested metadata.tar.zst directly
        try:
            with tarfile.open(filepath, "r") as outer_tar:
                for member in outer_tar.getmembers():
                    if "metadata.tar" in member.name:
                        f = outer_tar.extractfile(member)
                        if f:
                            meta_bytes = f.read()
                            if member.name.endswith(".zst"):
                                proc = subprocess.Popen(["zstd", "-d"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                                meta_bytes, _ = proc.communicate(input=meta_bytes)
                            
                            with tarfile.open(fileobj=io.BytesIO(meta_bytes), mode="r:*") as inner_tar:
                                for inner_member in inner_tar.getmembers():
                                    if inner_member.name.endswith("CATEGORY"):
                                        category = inner_tar.extractfile(inner_member).read().decode('utf-8', errors='ignore').strip()
                                    elif inner_member.name.endswith("PN"):
                                        pn = inner_tar.extractfile(inner_member).read().decode('utf-8', errors='ignore').strip()
                                if category and pn:
                                    return category, pn
        except Exception:
            pass

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
            if category and pn:
                return category, pn
        except Exception:
            pass

    return category, pn

def reorganize_loose_files():
    """Fix any packages that were dumped in the binhost root by previous runs."""
    if not os.path.exists(BINHOST_DIR):
        return
    for item in os.listdir(BINHOST_DIR):
        item_path = os.path.join(BINHOST_DIR, item)
        if os.path.isfile(item_path) and item.endswith(('.gpkg.tar', '.tbz2', '.xpak')):
            cat, pn = get_pkg_metadata(item_path)
            if cat and pn:
                target_dir = os.path.join(BINHOST_DIR, cat, pn)
                os.makedirs(target_dir, exist_ok=True)
                shutil.move(item_path, os.path.join(target_dir, item))
                print(f"Re-organized existing loose package: {cat}/{pn}/{item}")

def get_existing_packages():
    """Index all properly organized packages."""
    existing = {}
    for root, dirs, files in os.walk(BINHOST_DIR):
        for f in files:
            if f.endswith(('.gpkg.tar', '.tbz2', '.xpak')):
                parts = root.split(os.sep)
                if len(parts) >= 3:
                    cat, pn = parts[-2], parts[-1]
                    existing[f] = (cat, pn, f)
    return existing

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
    reorganize_loose_files()
    existing_pkgs = get_existing_packages()
    all_pkgs = list(existing_pkgs.values())

    print(f"Fetching releases from {API_URL} ...")
    req = urllib.request.Request(API_URL, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        with urllib.request.urlopen(req) as response:
            releases = json.loads(response.read().decode())
    except Exception as e:
        print(f"Error fetching API releases: {e}")
        return all_pkgs

    to_download = []
    for release in releases:
        for asset in release.get('assets', []):
            name = asset['name']
            url = asset['browser_download_url']
            
            if name.endswith(('.gpkg.tar', '.tbz2', '.xpak')):
                if name in existing_pkgs:
                    continue
                to_download.append((name, url))

    if to_download:
        print(f"Found {len(to_download)} new package(s). Downloading in parallel (8 threads)...")
        os.makedirs(TEMP_DIR, exist_ok=True)
        
        with ThreadPoolExecutor(max_workers=8) as executor:
            downloaded = list(executor.map(download_asset, to_download))

        for name, temp_path in downloaded:
            if not temp_path or not os.path.exists(temp_path):
                continue
                
            cat, pn = get_pkg_metadata(temp_path)
            
            if cat and pn:
                target_dir = os.path.join(BINHOST_DIR, cat, pn)
                os.makedirs(target_dir, exist_ok=True)
                target_path = os.path.join(target_dir, name)
                shutil.move(temp_path, target_path)
                all_pkgs.append((cat, pn, name))
                print(f"Organized: {cat}/{pn}/{name}")
            else:
                print(f"Warn: Still unable to determine metadata for {name}")
                shutil.move(temp_path, os.path.join(BINHOST_DIR, name))

        shutil.rmtree(TEMP_DIR, ignore_errors=True)
    else:
        print("All packages are already up-to-date!")

    return all_pkgs

def generate_packages_index():
    print("Generating Portage 'Packages' index using emaint...")
    try:
        env = os.environ.copy()
        env['PKGDIR'] = os.path.abspath(BINHOST_DIR)
        subprocess.run(["emaint", "binhost", "--fix"], env=env, check=True)
        print("Packages index generated successfully.")
    except Exception as e:
        print(f"emaint binhost generation failed: {e}")

def generate_website(pkgs):
    print("Generating HTML website...")
    html = f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{REPO_OWNER} Gentoo Repository & Binhost</title>
    <style>
        body {{ font-family: system-ui, -apple-system, sans-serif; margin: 2rem auto; max-width: 800px; background: #1e1e1e; color: #e0e0e0; }}
        a {{ color: #66b3ff; text-decoration: none; }}
        a:hover {{ text-decoration: underline; }}
        h1, h2 {{ border-bottom: 1px solid #444; padding-bottom: 0.5rem; }}
        ul {{ list-style: none; padding: 0; }}
        li {{ margin: 0.5rem 0; background: #2a2a2a; padding: 1rem; border-radius: 6px; }}
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
    for cat, pn, name in pkgs:
        path = f"binhost/{name}" if cat == "unknown" else f"binhost/{cat}/{pn}/{name}"
        html += f'''
        <li>
            <div class="package">
                <span class="category">{cat}/{pn}</span>
                <a href="{path}">{name}</a>
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
    pkgs = fetch_and_organize_binpkgs()
    generate_packages_index()
    generate_website(pkgs)
    print("Build complete!")
