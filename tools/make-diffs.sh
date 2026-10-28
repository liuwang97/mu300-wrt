#!/bin/sh
# Maintainer: the diffs of a release, run by tools/make-release.sh before it writes and signs SHA256SUMS.
#   tools/make-diffs.sh DIR TAG
# For each image in DIR (mu300-*-rootfs.tar.gz) and each of the MU300_DIFF_BASES releases before TAG (5) that
# published that image's manifest: DIR/mu300-<image>-rootfs.diff-<BASE>.tar.gz (openwrt/tools/image-diff.py).
# SHA256SUMS lists them with the rest, so the release key signs them; mu300-update downloads one instead of the whole
# image when the device runs its base release, and installs only what the release's signed manifest says the image is.
# The bases come from this repository's own releases on GitHub (gh), or from MU300_DIFF_BASES_DIR/<tag>/ (tests, a
# build without the network): each base's manifests are taken only as its SHA256SUMS lists them, and where the tree
# has the release key, only when that SHA256SUMS is signed with it (mu300-update's own sums_verdict). A diff that
# saves less than a fifth of the image is not published.
set -eu
D=${1:?usage: tools/make-diffs.sh DIR TAG}
TAG=${2:?usage: tools/make-diffs.sh DIR TAG}
TOP=$(cd "$(dirname "$0")/.." && pwd)
REPO=${MU300_REPO:-liuwang97/mu300-wrt}
N=${MU300_DIFF_BASES:-5}
case $N in ''|*[!0-9]*) echo "MU300_DIFF_BASES=$N is not a number" >&2; exit 1 ;; esac
[ "$N" -gt 0 ] || { echo "no diffs (MU300_DIFF_BASES=0)"; exit 0; }
. "$TOP/tools/releases.sh"
# (MU300_DIFF_PUB: another key, for tests)
PUB=${MU300_DIFF_PUB:-$TOP/openwrt/opt/mu300/lib/release.pub}
W=$(umask 077; mktemp -d "${TMPDIR:-/tmp}/mu300-diffs.XXXXXX")
trap 'rm -rf "${W:?}"' EXIT
# the key as a file of our own (mu300-update's key_state: root's own, written by nobody else; sudo -E runs this)
[ ! -s "$PUB" ] || { cat "$PUB" > "$W/release.pub"; chmod 644 "$W/release.pub"; }

# the releases before TAG, newest first
if [ -n "${MU300_DIFF_BASES_DIR:-}" ]; then
    tags=$(ls "$MU300_DIFF_BASES_DIR")
else
    tags=$(gh release list -R "$REPO" --limit 60 --json tagName,isDraft -q '.[] | select(.isDraft | not) | .tagName') ||
        { echo "cannot read the releases of $REPO (gh): no diffs" >&2; exit 0; }
fi
older=$(for t in $tags; do tag_ok "$t" && newer "$TAG" "$t" && printf '%s %s\n' "$(tag_key "$t")" "$t"; done | sort -rn | cut -d' ' -f2)

# base_ok TAG DIR: DIR/SHA256SUMS is TAG's and as trusted as an update trusts it (signed, where the tree has the key)
base_ok() (
    set +eu
    MU300_LIB=1 MU300_RELEASE_PUB=$W/release.pub
    [ -s "$W/release.pub" ] || MU300_RELEASE_PUB=$W/none
    export MU300_LIB MU300_RELEASE_PUB
    . "$TOP/openwrt/opt/mu300/bin/mu300-update"
    sums_verdict "$1" "$(sig_state "$2/SHA256SUMS" "$2/SHA256SUMS.sig")" "release $1" "$2/SHA256SUMS" >/dev/null
)

sha() { { sha256sum "$1" 2>/dev/null || shasum -a 256 "$1"; } | cut -d' ' -f1; }
made=0 bases=0
for b in $older; do
    [ "$bases" -lt "$N" ] || break
    B=$W/$b
    mkdir -p "$B"
    if [ -n "${MU300_DIFF_BASES_DIR:-}" ]; then
        cp "$MU300_DIFF_BASES_DIR/$b"/SHA256SUMS* "$MU300_DIFF_BASES_DIR/$b"/mu300-*-rootfs.manifest.gz "$B/" 2>/dev/null || :
    else
        gh release download "$b" -R "$REPO" -D "$B" -p SHA256SUMS -p SHA256SUMS.sig -p 'mu300-*-rootfs.manifest.gz' 2>/dev/null || :
    fi
    ls "$B"/mu300-*-rootfs.manifest.gz >/dev/null 2>&1 && [ -s "$B/SHA256SUMS" ] || { echo "$b: no manifests, no diffs from it"; continue; }
    base_ok "$b" "$B" || { echo "$b: its SHA256SUMS is not signed with the release key, no diffs from it" >&2; continue; }
    bases=$((bases + 1))
    for img in "$D"/mu300-*-rootfs.tar.gz; do
        a=${img##*/}; s=${a%.tar.gz}; m=$B/$s.manifest.gz
        [ -f "$m" ] || continue
        want=$(awk -v f="$s.manifest.gz" '$2 == f || $2 == "*" f {print $1}' "$B/SHA256SUMS")
        [ -n "$want" ] && [ "$(sha "$m")" = "$want" ] ||
            { echo "$b: $s.manifest.gz does not match its SHA256SUMS" >&2; exit 1; }
        out=$D/$s.diff-$b.tar.gz
        python3 "$TOP/openwrt/tools/image-diff.py" make "$m" "$img" "$out"
        python3 "$TOP/openwrt/tools/image-diff.py" check "$m" "$img" "$out"
        if [ $(( $(wc -c < "$out") * 5 )) -gt $(( $(wc -c < "$img") * 4 )) ]; then
            echo "$s from $b: the diff saves less than a fifth, not published"
            rm -f "$out"
        else
            made=$((made + 1))
        fi
    done
done
echo "$made diffs from $bases releases before $TAG"
