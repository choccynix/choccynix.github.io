import os
import json
import tarfile
import urllib.request
import subprocess
import shutil

# --- Configuration ---
REPO_OWNER = "choccynix"
BINPKG_REPO = f"{REPO_OWNER}/athanor-binpkgs"
API_URL = f"https://api.github.com/repos/{BINPKG_REPO}/releases?per_page=100"

BINHOST_DIR = "binhost"
PROFILES_DIR = "profiles"
METADATA_DIR = "metadata"

def setup_directories():
    """Create basic Gentoo ebuild repository overlay skeleton."""
    os.makedirs(BINHOST_DIR, exist_ok=True)
    os.makedirs(PROFILES_DIR, exist_ok=True)
    os.makedirs(METADATA_DIR, exist_ok=True)

    if not os.path.exists(os.path.join(PROFILES_DIR, "repo_name")):
        with open(os.path.join(PROFILES_DIR, "repo_name"), "w") as f:
            f.write(f"{REPO_OWNER}\n")
    
    if not os.path.exists(os.path.join(METADATA_DIR, "layout.conf")):
        with open(os.path.join(METADATA_DIR, "layout.conf"), "w") as f:
            f.write("masters = gentoo\nauto-sync = false\n")

def get_existing_packages():
    """Scan the binhost directory to find packages we've already downloaded in previous runs."""
    existing = {}
    for root, dirs, files in os.walk(BINHOST_DIR):
        for f in files:
            if f.endswith(('.gpkg.tar', '.tbz2', '.xpak')):
                # Try to extract category/pn from the folder structure
                parts = root.split(os.sep)
                if len(parts) >= 3:
                    cat, pn = parts[-2], parts[-1]
                else:
                    cat, pn = "unknown", "unknown"
                existing[f] = (cat, pn, f)
    return existing

def get_pkg_metadata(filepath):
    """Safely extract the CATEGORY and PN (Package Name) from a binary package."""
    category, pn = None, None
    if filepath.endswith('.gpkg.tar'):
        try:
            with tarfile.open(filepath, "r") as tar:
                for member in tar.getmembers():
                    if member.name.endswith("/CATEGORY"):
                        category = tar.extractfile(member).read().decode('utf-8').strip()
                    elif member.name.endswith("/PN"):
                        pn = tar.extractfile(member).read().decode('utf-8').strip()
                    if category and pn:
                        break
        except Exception as e:
            print(f"Failed to read gpkg {filepath}: {e}")
    else:
        try:
            import portage.xpak
            xpak = portage.xpak.tbz2(filepath)
            category = xpak.get_data(b"CATEGORY").decode('utf-8').strip()
            pn = xpak.get_data(b"PN").decode('utf-8').strip()
        except Exception as e:
            print(f"Fallback parsing failed for {filepath}: {e}")
            
    return category, pn

def fetch_and_organize_binpkgs():
    print(f"Fetching releases from {API_URL} ...")
    req = urllib.request.Request(API_URL, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        with urllib.request.urlopen(req) as response:
            releases = json.loads(response.read().decode())
    except Exception as e:
        print(f"Error fetching API releases: {e}")
        return []

    existing_pkgs = get_existing_packages()
    all_pkgs = list(existing_pkgs.values())
    
    temp_dir = "temp_downloads"
    os.makedirs(temp_dir, exist_ok=True)

    for release in releases:
        for asset in release.get('assets', []):
            name = asset['name']
            url = asset['browser_download_url']
            
            if name.endswith(('.gpkg.tar', '.tbz2', '.xpak')):
                if name in existing_pkgs:
                    # Skip downloading if we already have it in our repo
                    continue
                    
                temp_path = os.path.join(temp_dir, name)
                print(f"Downloading new package: {name} ...")
                urllib.request.urlretrieve(url, temp_path)
                
                cat, pn = get_pkg_metadata(temp_path)
                
                if cat and pn:
                    target_dir = os.path.join(BINHOST_DIR, cat, pn)
                    os.makedirs(target_dir, exist_ok=True)
                    target_path = os.path.join(target_dir, name)
                    shutil.move(temp_path, target_path)
                    all_pkgs.append((cat, pn, name))
                    print(f"Organized: {cat}/{pn}/{name}")
                else:
                    print(f"Warn: Metadata missing for {name}, leaving in root binhost dir.")
                    shutil.move(temp_path, os.path.join(BINHOST_DIR, name))
                    all_pkgs.append(("unknown", "unknown", name))
                    
    shutil.rmtree(temp_dir, ignore_errors=True)
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
    print("Generating HTML web-front...")
    
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
    <p>Add the following lines to your <code>/etc/portage/make.conf</code> to fetch pre-compiled binaries from this repository:</p>
    <pre>
PORTAGE_BINHOST="https://{REPO_OWNER}.github.io/binhost"
EMERGE_DEFAULT_OPTS="${{EMERGE_DEFAULT_OPTS}} --getbinpkg"
    </pre>

    <h2>Available Binpkgs ({len(pkgs)})</h2>
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
    
    <footer style="margin-top: 2rem; font-size: 0.8em; color: #777;">
        <p>Automatically generated via GitHub Actions.</p>
    </footer>
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
