# Releases are signed, ordered by tag, and downgrades need a flag
Applies to: Linux 7.2.11, OpenWrt 25.12.5 (F50, U30 Air). Checked against the code: 2026-10-23.

## Context
A release from GitHub, a mirror or a file must be trusted before the updater writes a system.

## Decision
`tools/sign-release.sh` signs `SHA256SUMS` (usign Ed25519) into `SHA256SUMS.sig`; the public key is `openwrt/opt/mu300/lib/release.pub` in the image, not under `/etc/mu300` (an update carries that over, so a new key would never arrive). `mu300-update` always refuses a bad signature, a key file that is not root's own regular file, and checksums naming another release. A system with the key refuses unsigned releases (`--unsigned`) and older ones (`--downgrade`) unless asked; the panel cannot set either flag. A release is `vYYYY.MM.DD.N` (N = 1..99); four-field tags are newer than three-field ones; `tools/make-release.sh` computes the next tag and refuses a tree with the key but no signing key. Kernel sources are checked by two pinned-key signatures (`kernel/verify-kernel-src.sh`, `kernel/keys/`); anything missing stops the build.

## Consequences
- The signature does not cover the VPN extra.

## Revisit when
The key rotates, or a different offline format is introduced.
