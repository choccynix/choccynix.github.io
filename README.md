# choccynix.github.io

This repo does three things at once, deliberately:

1. **GitHub Pages site.** Because it's named `<username>.github.io`,
   GitHub serves it automatically from this repo's root — no gh-pages
   branch, no settings toggle. `index.html` is intentionally minimal.

2. **Gentoo binhost index host.** `Packages` and `Packages.sig` in this
   repo's root are auto-updated by
   [athanor-binpkgs](https://github.com/choccynix/athanor-binpkgs)' CI
   after every build. The actual `.gpkg.tar` binary blobs still live on
   that repo's GitHub Releases — this repo only ever holds the lightweight
   index pointing at them. Don't hand-edit `Packages`/`Packages.sig`
   directly; they get overwritten on the next build.

3. **A Gentoo ebuild overlay.** `metadata/layout.conf` +
   `profiles/repo_name` + `profiles/categories` make this a valid
   `repos.conf` overlay target, separate from the binhost concern above.
   No ebuilds in here yet — add real category/package directories as
   needed, same as any other overlay.

## For consumers

See `templates/binrepos.conf.template` (binary packages) and
`templates/repos.conf.template` (ebuild overlay) — copy either or both
into `/etc/portage/`. Full walkthrough in athanor-binpkgs'
[docs/binhost-setup.md](https://github.com/choccynix/athanor-binpkgs/blob/main/docs/binhost-setup.md).

## Auto-update mechanism

athanor-binpkgs' `merge-and-publish` job pushes here using a fine-grained
Personal Access Token stored as a secret in *that* repo (GitHub's default
`GITHUB_TOKEN` can't write to a different repo than the one a workflow
runs in). If the `Packages` file here ever looks stale, check that token
hasn't expired — see athanor-binpkgs' workflow comments for the exact
secret name.
