# ◈ Consuming the AthanorOS binhost

## What you're pointing at

`PORTAGE_BINHOST` is set to a URL that serves a `Packages` file — Portage's
binhost index format. That's the only thing that lives at the binhost URL
itself. Each package entry in the index carries its own absolute `URI`
pointing at a GitHub Release asset, so Portage fetches the index from one
place and the actual `.gpkg.tar` files from wherever they were uploaded.

This means the binhost URL (a GitHub Pages `gh-pages` branch) can stay tiny
and fast even as the number of packages grows — it's never storing the
blobs itself.

## Setup

1. Copy `templates/make.conf.binhost.template` into `/etc/portage/make.conf`
   (or append its contents if you already have entries there).
2. Copy `templates/package.use.template` to
   `/etc/portage/package.use/athanor-binpkgs`, matching flags against
   whatever's currently in upstream `packages.list`.
3. If any packages track `~arch`, do the same with
   `templates/package.accept_keywords.template`.
4. Import the signing key if you want the `binpkg-signing` FEATURES check to
   actually verify anything:

   ```
   curl -fsSL https://choccynix.github.io/athanor-binpkgs/athanor-binpkgs.pub.asc \
     -o /etc/portage/gpg/athanor-binpkgs.pub.asc
   ```

5. Sync and try installing something known to be in `packages.list`:

   ```
   emerge --sync
   emerge --getbinpkg=y --usepkg=y app-editors/neovim
   ```

   Portage logs will say `[binary]` next to the package name if it pulled
   the prebuilt version instead of compiling.

## Why it might fall back to source anyway

- Your local USE flags don't match the build-time USE for that package —
  see `package.use.template`.
- Your `~arch` keyword acceptance doesn't match — see
  `package.accept_keywords.template`.
- The package simply isn't in `packages.list` yet — open a package request
  issue using the template in this repo.
- The rolling build is older than your currently-synced portage tree and
  Portage considers the binary stale — this resolves itself on the next
  scheduled build (Sundays, ahead of the ISO's own weekly build).
