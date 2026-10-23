# The release tags, for tools/make-release.sh. The block below is word for word the one in mu300-update
# (openwrt/tests/test_releases.py holds the two to each other and to its fixture of tags).

# --- release-tag begin
# The release tags: vYYYY.MM.DD.N, the real (UTC) day the release was built and N = 1..99 for the releases of that
# day; the older three-field vYYYY.MM.DD tags are still read, and every four-field tag is newer than every three-field
# one. Four-field tags compare by the date, then by N, as numbers. The same block, word for word, is in
# tools/releases.sh and mu300-update (openwrt/tests/test_releases.py holds the two to each other).
# tag_ok TAG: TAG is a release tag (in the shell's own patterns: no grep, no locale, no regex engine to differ)
tag_ok() {
    case $1 in
        v[0-9][0-9][0-9][0-9].[0-9][0-9].[0-9][0-9]) return 0 ;;
        v[0-9][0-9][0-9][0-9].[0-9][0-9].[0-9][0-9].[1-9]|v[0-9][0-9][0-9][0-9].[0-9][0-9].[0-9][0-9].[1-9][0-9]) ;;
        *) return 1 ;;
    esac
    case ${1#v?????} in 0[1-9].*|1[0-2].*) ;; *) return 1 ;; esac
    case ${1#v????????} in 0[1-9].*|[12][0-9].*|3[01].*) ;; *) return 1 ;; esac
}
# tag_key TAG: a number in the order of the releases (1YYYYMMDD00 for vYYYY.MM.DD, 2YYYYMMDDNN for vYYYY.MM.DD.N);
# nothing and status 1 for what is no release tag
tag_key() {
    tag_ok "$1" || return 1
    case $1 in
        *.*.*.*) _tn=${1##*.}; [ ${#_tn} = 2 ] || _tn=0$_tn; _tk=${1%.*}; _te=2 ;;
        *) _tn=00; _tk=$1; _te=1 ;;
    esac
    _tk=${_tk#v}; _td=${_tk#*.}
    echo "$_te${_tk%%.*}${_td%.*}${_td#*.}$_tn"
}
# newer A B: release A is newer than release B; false when either is no release tag. The key is compared in two
# parts (the era and the date, nine digits; then N without its leading zero), so that no shell's integers overflow
# and none reads 08 as octal.
newer() {
    _na=$(tag_key "$1") && _nb=$(tag_key "$2") || return 1
    [ "${_na%??}" -gt "${_nb%??}" ] && return 0
    [ "${_na%??}" = "${_nb%??}" ] || return 1
    _na=${_na#?????????}; _nb=${_nb#?????????}
    [ "${_na#0}" -gt "${_nb#0}" ]
}
# --- release-tag end
