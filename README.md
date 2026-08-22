# athanor-binpkgs

◈ Prebuilt binary packages for **AthanorOS** — a musl+llvm+openrc Gentoo derivative.

This repo is the *retort* — it holds no binaries itself. It holds the scripts,
templates, and CI wiring that build packages inside the same
`gentoo/stage3:musl-llvm` container used for the ISO, then publish them as
GitHub Release assets. A `Packages` index (Portage's binhost metadata format)
is regenerated on every publish and served via GitHub Pages, so end users
point `PORTAGE_BINHOST` at one stable URL while the actual `.gpkg.tar` blobs
live in GitHub Releases. The canonical consumer URL is
`https://choccynix.github.io/athanor-binpkgs/`.

## Why not just commit binaries to git?

Git repos degrade badly once binary blobs accumulate — every clone re-downloads
the full history, and there's no garbage collection for old package versions.
GitHub Releases already solves this for the AthanorOS ISOs; this repo reuses
that exact mechanism instead of reinventing it.

## Layout

```
athanor-binpkgs/
├── packages.list               core set: small, fast-building tools
├── packages.graphics.list      Wayland-minimal graphics stack (mesa, wlroots, seatd)
├── packages.desktop.list       usable live desktop (sway, foot)
├── packages.browser.list       webkit-gtk + surf, isolated in its own group (replaces firefox, which hit LLVM's 6h compile ceiling)
├── packages.kde-qt.list        Qt5/KF5/KPMcore + partitioning tools (Calamares itself dropped)
├── packages.xorg.list          mainline Xorg + suckless (dwm, st, dmenu)
├── packages.xlibre.list        XLibre fork alternative to xorg.list (needs overlay, see file header)
├── .github/workflows/
│   ├── build-packages.yml      matrix build across all seven lists, then merges
│   │                           + signs + publishes one combined Packages index
│   └── publish-index.yml       manual re-sign/re-publish without a full rebuild
├── scripts/
│   ├── build-binpkg.py         one combined `emerge --buildpkg` per list (CI + local use)
│   ├── generate-packages-index.py  builds the Portage-compatible Packages file
│   ├── merge-packages-index.py     combines each group's fragment into one index
│   ├── sync-ccache.py          persists a per-group ccache between CI runs
│   ├── sign-packages.py        GPG-signs the index
│   ├── upload-release.py       stdlib-only GitHub Releases uploader (package blobs)
│   └── upload-single-asset.py  uploads/replaces one named asset (fragments, ccache)
├── templates/                  copy-paste configs for end users and contributors
└── docs/                       setup guide, contributing guide, signing notes
```

## Why seven lists instead of one

GitHub-hosted runners hard-cap every job at 6 hours, no matter the plan. A
serial build of Mesa + Sway + WebKitGTK + Qt5 + KDE Frameworks + KPMcore +
Xorg + XLibre could easily exceed that (Firefox, previously in this slot,
hit the ceiling on its own — replaced with WebKitGTK/surf after LLVM
itself turned out to fail compiling for it, a genuine toolchain bug).
`build-packages.yml` builds `core`, `graphics`, `desktop`, `browser`,
`kde-qt`, `xorg`, and `xlibre` as seven parallel matrix jobs, each with
its own ccache (persisted as a release asset on a fixed `build-cache`
tag), so the first run is slow but every rebuild after that only
recompiles what changed — and a slow group like `browser` can't hold
up or get cancelled alongside fast ones like `desktop`. A final job merges
all seven groups' index fragments into one combined `Packages` file
before publishing.

`xorg` and `xlibre` are **alternatives to each other, not additions** —
XLibre (a 2025 Xorg fork) file-collides with Xorg itself, so only one can
actually be installed on a real system at a time. Both still get built and
published; the choice of which is left to whoever consumes the binhost.
`xlibre` also needs a third-party overlay (not in Gentoo's main tree),
added only for that one matrix job.

Each group's `emerge` invocation covers its *entire* list in one call
rather than one atom at a time — that's what lets Portage's solver pick
mutually consistent USE flags for shared dependencies (e.g. `boost` needing
`python` for one consumer but not for anything else in `core`) instead of
locking in a USE combination too early and hitting a conflict later in the
same run. This is exactly what happened with Calamares before it was
dropped — its dependency on `boost[python]` conflicted with `boost`
already having been merged plain elsewhere in the same list.

## GitHub Pages endpoint

The canonical Portage endpoint is:

```text
https://choccynix.github.io/athanor-binpkgs/
```

CI publishes `Packages` and `Packages.sig` to the `gh-pages` branch. In the
repository settings, enable **Pages → Deploy from a branch → `gh-pages` /
root**. The workflow also writes `.nojekyll` and a small `index.html`, so the
site is easy to sanity-check in a browser.

The important separation is: **Pages serves metadata; Releases serve the
large package blobs.** The ISO builder only consumes the Pages endpoint and
does not modify the binhost.

## Quick start (consuming packages)

See `docs/binhost-setup.md`. Short version: copy
`templates/make.conf.binhost.template` into `/etc/portage/make.conf`.

## Quick start (contributing a package)

See `docs/contributing-packages.md`. Short version: add a line to
`packages.list`, open a PR — CI builds it and reports back on the PR whether
it compiled cleanly under musl+llvm.

## Pillars

Same three as AthanorOS proper: **Divergence, Minimalism, Transmutation.**
This repo exists so `Transmutation` — turning source into something usable —
doesn't have to happen on every single machine, every single time.
