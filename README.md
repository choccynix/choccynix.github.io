# 🍫 choccynix Gentoo Binhost & Repository

Welcome to the automated Gentoo binary package host (binhost) and overlay for **choccynix**.

This repository automatically indexes pre-compiled binary packages (`.gpkg.tar`) from [choccynix/athanor-binpkgs](https://github.com/choccynix/athanor-binpkgs) releases, builds a standard Portage `Packages` index, and hosts a static web catalog on GitHub Pages.

---

## 🚀 Quick Start / How to Use

### 1. Configure the Binhost
Add the following lines to your `/etc/portage/make.conf`:

```bash
# Point Portage to this binhost
PORTAGE_BINHOST="https://choccynix.github.io/binhost"

# Automatically pull binary packages when available
EMERGE_DEFAULT_OPTS="${EMERGE_DEFAULT_OPTS} --getbinpkg"

# add the repo

[choccynix]
location = /var/db/repos/choccynix
sync-type = git
sync-uri = https://github.com/choccynix/choccynix.github.io.git
auto-sync = yes

# sync it
emaint sync -r choccynix
