# ◈ Contributing a package

## Adding to the curated set

1. Add a line to `packages.list`:

   ```
   category/package[-version]  [use:flag1,-flag2]
   ```

2. Open a PR. The `build-packages.yml` workflow triggers on any PR touching
   `packages.list` and attempts the build inside the real musl+llvm
   container — this is the actual test of whether the package works on
   AthanorOS, not just whether the atom is spelled correctly.
3. On a PR, packages are uploaded to a throwaway release tagged
   `binpkgs-artifacts-pr-<number>` rather than the rolling release, and the
   `Packages` index on `gh-pages` is left untouched — merging to `main` is
   what actually publishes a package to the live binhost.
4. Check `build-report.json` (attached to the PR run) if a build fails —
   it keeps the tail of the emerge log per failing atom.

## Things that commonly break under musl+llvm

Worth checking before opening a PR, based on issues already hit building the
AthanorOS ISO itself:

- Packages with hard `sys-apps/dracut` dependencies need `sys-kernel/dracut`
  instead (the package moved).
- Anything pulling in `linux-firmware` needs its license accepted in the
  build environment or the build will stall waiting for interactive
  acceptance.
- glibc-only build systems (autotools scripts that shell out assuming GNU
  coreutils behavior, or anything that hardcodes glibc-specific syscalls)
  are the most common source of musl build failures — if in doubt, open the
  PR anyway and let CI tell you.

## Requesting without building it yourself

Use the "◈ Package request" issue template instead of a PR if you don't want
to write the `packages.list` line yourself — same CI validation happens once
someone else (or a maintainer) adds it.
