# A daemon's own directory under /run, for mu300-atd and mu300-voice (sourced; POSIX sh, busybox ash included).
#
# /run takes files from every user on these images (boot/init mounts it 1777), so a name there may be somebody
# else's, made first: a directory whose owner reads what is written into it (the modem's answers, the callers'
# numbers) and puts his own files where a daemon expects its own (a command fifo, pid files it kills), or a symlink
# that sends root's writes elsewhere. Existing is therefore not enough: the directory must be a real directory,
# its owner the daemon's user, and nobody else may write it. One helper for both, so the two checks cannot drift.
#
#   run_dir_ours PATH [UID]         true when PATH is a directory (not a symlink to one) that UID (default 0) owns
#                                   and neither its group nor others may write
#   run_dir_take PATH [UID] [WHO]   make PATH that: created when missing; a foreign entry (another owner, a symlink,
#                                   a file) is said so on stderr and replaced, a directory of UID's that others may
#                                   write has that taken away - so a squatted name is recovered from, not a reason
#                                   to stay down. Only UID itself replaces anything; for anybody else (a member
#                                   of the voice group reading the events) this is the check alone. False, with
#                                   the reason on stderr as "WHO: ...", when PATH is still not UID's own after that.
run_dir_ours() {
    set -- "$1" "${2:-0}" $(ls -lnd "$1" 2>/dev/null)
    case ${3:-} in d????w*|d???????w*) return 1 ;; d*) ;; *) return 1 ;; esac
    [ "${5:-}" = "$2" ]
}
run_dir_take() {
    _rd=$1; _ru=${2:-0}; _rw=${3:-run-dir}
    # never "mkdir -p" first: through a symlink of somebody else's that would make the directory where it points
    [ -e "$_rd" ] || [ -L "$_rd" ] || ( umask 077; mkdir "$_rd" ) 2>/dev/null
    run_dir_ours "$_rd" "$_ru" && return 0
    if [ "$(id -u 2>/dev/null)" != "$_ru" ]; then
        echo "$_rw: $_rd is missing or not the daemon's own directory" >&2
        return 1
    fi
    set -- $(ls -lnd "$_rd" 2>/dev/null)
    case ${1:-}/${3:-} in
        d*/"$_ru")
            echo "$_rw: others could write $_rd; that is taken away" >&2
            chmod go-w "$_rd" 2>/dev/null ;;
        *)
            echo "$_rw: $_rd was somebody else's (made first in /run, or a symlink); replaced" >&2
            # rm does not follow a symlink, here or inside a directory it empties
            rm -rf "$_rd" 2>/dev/null
            ( umask 077; mkdir "$_rd" ) 2>/dev/null ;;
    esac
    run_dir_ours "$_rd" "$_ru" && return 0
    echo "$_rw: $_rd cannot be made the daemon's own directory (still somebody else's); remove it by hand" >&2
    return 1
}
