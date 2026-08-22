# ◈ Package index signing

The `Packages` index is served over GitHub Pages rather than Gentoo's own
signed mirror infrastructure, so a detached GPG signature is how consumers
verify it wasn't tampered with between CI and their machine.

## One-time maintainer setup

1. Generate (or reuse) a dedicated signing key — don't reuse a personal key
   that has other trust attached to it:

   ```
   gpg --batch --gen-key <<'EOF'
   %no-protection
   Key-Type: eddsa
   Key-Curve: ed25519
   Name-Real: AthanorOS binpkgs
   Name-Email: ci@athanoros.invalid
   Expire-Date: 2y
   EOF
   ```

2. Export the public key and publish it in this repo so consumers can fetch
   it (referenced by `docs/binhost-setup.md`):

   ```
   gpg --armor --export ci@athanoros.invalid > athanor-binpkgs.pub.asc
   ```

3. Export the private key and store it as a repo secret
   (`GPG_SIGNING_KEY_ID` for the key ID, plus import the secret key material
   itself as a secret the workflow imports at runtime — don't commit private
   key material to the repo, even to a private one).

## What actually gets signed

Just the `Packages` index file itself — not each individual package blob.
Since every entry's checksum (`SHA512`/`SIZE`) lives inside the index, a
valid signature on the index transitively guarantees the integrity of every
package it references, as long as Portage is configured to actually check
those checksums against the downloaded file (default behavior).

## Rotating the key

Run the `Re-publish Packages index` workflow manually after updating the
`GPG_SIGNING_KEY_ID` / `GPG_PASSPHRASE` secrets — it re-signs the existing
index without needing a full package rebuild.
