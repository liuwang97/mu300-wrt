# Sourced (POSIX sh, busybox ash): the hotspot on or off, one way for the panel's Wi-Fi button (unisoc-modem/action
# wifi) and the Wi-Fi key held (mu300-sysd), so that each undoes the other. After kanoqwq's db4e5d2 (hotspot.uc).
# It turns the access point's own wifi-iface on or off, not the whole radio: a Wi-Fi client or a second network on
# the same radio stays as it is. The radio is turned on with the access point only when it was off and nothing else
# on it would come up with it (a radio that the panel's button switched off as a whole comes back). Then netifd
# reloads the wireless configuration, which it does asynchronously: "pending" is all that can be said at that point.
#
# hotspot_ap_set on|off prints one word, and returns 0 for the first three:
#   pending    the flag is written and committed, netifd reloads; the access point comes or goes in a few seconds
#   unchanged  it already was so: nothing written
#   saved      written and committed, but netifd's reload failed (a reboot, or wifi reload, applies it)
#   staged     the wireless configuration has changes that are not saved (LuCI's, or uci's on the command line):
#              nothing is done, so that they are neither committed with this nor lost
#   busy       another toggle is running
#   noap       no access point section, or several and none of them on the Wi-Fi device (unisoc_modem.main.wifi_iface
#              names one, wifi_device is the interface it runs as: wlan0)
#   conflict   the radio is off and turning it on would bring up another interface on it as well
#   client     on, while the radio is the Wi-Fi client's (wifi-client): this driver cannot be both, the client keeps it
#   failed     uci refused
# Nothing from the wireless configuration is printed: never a key.
# MU300_HOTSPOT_RPCD, MU300_HOTSPOT_LOCK and MU300_RUN are for the tests.
HOTSPOT_RPCD=${MU300_HOTSPOT_RPCD:-/var/run/rpcd}
HOTSPOT_LOCK=${MU300_HOTSPOT_LOCK:-/var/run/mu300-hotspot.lock}
HOTSPOT_CLIENT=${MU300_RUN:-/run}/mu300-wifi-client.active

# hotspot_ap_section: the access point's wifi-iface section name (a uci section name, nothing else)
hotspot_ap_section() {
    _hw=$(uci -q show wireless 2>/dev/null) || return 1
    _ha=$(printf '%s\n' "$_hw" | sed -n "s/^wireless\.\([A-Za-z0-9_]*\)\.mode='\{0,1\}ap'\{0,1\}$/\1/p")
    _hs=$(uci -q get unisoc_modem.main.wifi_iface 2>/dev/null)
    if [ -n "$_hs" ]; then
        printf '%s\n' "$_ha" | grep -qx -- "$_hs" && { echo "$_hs"; return 0; }
        return 1
    fi
    _hd=$(uci -q get unisoc_modem.main.wifi_device 2>/dev/null); _hd=${_hd:-wlan0}
    for _hs in $_ha; do
        [ "$(uci -q get "wireless.$_hs.ifname")" = "$_hd" ] && { echo "$_hs"; return 0; }
    done
    [ "$(printf '%s\n' "$_ha" | grep -c .)" = 1 ] && { echo "$_ha"; return 0; }
    return 1
}

# hotspot_staged: uncommitted wireless changes, uci's own (/tmp/.uci) or a LuCI session's (rpcd keeps them apart)
hotspot_staged() {
    [ -n "$(uci -q changes wireless 2>/dev/null)" ] && return 0
    for _hf in "$HOTSPOT_RPCD"/uci-*/wireless; do [ -s "$_hf" ] && return 0; done
    return 1
}

hotspot_ap_set() {
    case ${1:-} in on) _hv=0 ;; off) _hv=1 ;; *) echo failed; return 1 ;; esac
    # (flock: busybox's applet on OpenWrt; a lock file that is a symlink is not opened)
    if command -v flock >/dev/null 2>&1 && [ -d "${HOTSPOT_LOCK%/*}" ] && [ ! -L "$HOTSPOT_LOCK" ]; then
        exec 8>>"$HOTSPOT_LOCK" && flock -n 8 || { echo busy; return 1; }
    fi
    _hr=$(_hotspot_ap_set "$_hv"); _hc=$?
    command -v flock >/dev/null 2>&1 && flock -u 8 2>/dev/null
    echo "$_hr"; return $_hc
}
_hotspot_ap_set() {
    [ "$1" = 1 ] || [ ! -e "$HOTSPOT_CLIENT" ] || { echo client; return 1; }
    hotspot_staged && { echo staged; return 1; }
    _hs=$(hotspot_ap_section) || { echo noap; return 1; }
    _hr=$(uci -q get "wireless.$_hs.device")
    [ -n "$_hr" ] && [ "$(uci -q get "wireless.$_hr")" = wifi-device ] || { echo noap; return 1; }
    _hrd=$(uci -q get "wireless.$_hr.disabled")
    _hcur=$(uci -q get "wireless.$_hs.disabled"); [ "$_hcur" = 1 ] || _hcur=0
    _hradio=0
    if [ "$1" = 0 ] && [ "$_hrd" = 1 ]; then
        # another interface on this radio that is not disabled would come up with it
        for _ho in $(uci -q show wireless | sed -n "s/^wireless\.\([A-Za-z0-9_]*\)=wifi-iface$/\1/p"); do
            [ "$_ho" != "$_hs" ] && [ "$(uci -q get "wireless.$_ho.device")" = "$_hr" ] &&
                [ "$(uci -q get "wireless.$_ho.disabled")" != 1 ] && { echo conflict; return 1; }
        done
        _hradio=1
    fi
    # (on with nothing to write still reloads: an access point that did not come up gets another start)
    [ "$1" = 1 ] && [ "$_hcur" = 1 ] && { echo unchanged; return 0; }
    if [ "$_hcur" != "$1" ] || [ "$_hradio" = 1 ]; then
        _hok=1
        uci -q set "wireless.$_hs.disabled=$1" || _hok=0
        [ "$_hradio" = 0 ] || uci -q set "wireless.$_hr.disabled=0" || _hok=0
        [ "$_hok" = 1 ] && uci -q commit wireless || { uci -q revert wireless; echo failed; return 1; }
    fi
    if ubus -t 10 call network reload >/dev/null 2>&1; then echo pending; else echo saved; fi
    return 0
}
