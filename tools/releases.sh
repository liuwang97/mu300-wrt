# Which release install.sh installs. Sourced by install.sh (t, ask and die are its own; needs $TOP, $REPO, $RELEASE).
#   MU300_RELEASE=<tag>       that release, and nothing is asked
#   MU300_RELEASE_URL=<url>   another place with the same files (the release is then called "custom"); nothing asked
# Otherwise the newest full release. When GitHub also has a prerelease published after it, release_resolve asks
# whether to install that beta instead (default: no). tools/release_pick.py decides which is which, for install.ps1
# and the graphical installer too.

# release_list: LATEST=<tag> and, with a newer prerelease, BETA=<tag>, from GitHub's list of releases. No token is
# needed. Nothing at all when the list does not come (no network, the API's rate limit, a proxy's page).
release_list() {
    curl -fsSL --connect-timeout 10 --max-time 30 "https://api.github.com/repos/$REPO/releases?per_page=15" 2>/dev/null |
        python3 "$TOP/tools/release_pick.py" 2>/dev/null || true
}

# release_resolve: RELEASE, when it is not set yet. BETA_OFFERED is the prerelease that was offered (empty: none).
release_resolve() {
    BETA_OFFERED=
    [ -z "$RELEASE" ] || return 0
    if [ -n "${MU300_RELEASE_URL:-}" ]; then RELEASE=custom; return 0; fi
    _rl=$(release_list)
    RELEASE=$(printf '%s\n' "$_rl" | sed -n 's/^LATEST=//p' | head -n1)
    _rb=$(printf '%s\n' "$_rl" | sed -n 's/^BETA=//p' | head -n1)
    if [ -z "$RELEASE" ]; then
        # the list did not come: the newest release alone, the way it was always looked up, and no question
        _rb=
        RELEASE=$(curl -fsSL "https://api.github.com/repos/$REPO/releases/latest" 2>/dev/null \
            | sed -n 's/.*"tag_name"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -n1)
        [ -n "$RELEASE" ] || die "$(t 'cannot find the newest release of {1} (set MU300_RELEASE=<tag> to pick one)' "$REPO")"
    fi
    [ -n "$_rb" ] || return 0
    BETA_OFFERED=$_rb
    echo "  $(t 'A beta is out: {1}, a prerelease for testing, newer than the newest full release {2}.' "$_rb" "$RELEASE")"
    ask _ra "$(t 'Install the beta {1} instead of {2}? (yes/no)' "$_rb" "$RELEASE")" no MU300_ANS_BETA
    if [ "$_ra" = yes ]; then RELEASE=$_rb; fi
    return 0
}
