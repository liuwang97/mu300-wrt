# Sourced (sh, bash, busybox sh): module_sources DIR - the SHA-256 of every file under DIR, one "SUM  ./PATH" line
# each, sorted by path. build-modules.sh keeps this list of each vendor module's directory as it built it
# ($MU300_OUT/modules/NAME.sources); make-bundle.sh compares it with the tree, so a module built from older code
# never reaches a bundle. By content, not by time: a checkout and the release's cache of kernel/out set any times.
module_sources() {
    (cd "$1" && find . -type f -exec sha256sum {} + | LC_ALL=C sort -k2)
}
