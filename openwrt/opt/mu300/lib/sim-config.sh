# Shared SIM preference storage. Runtime routing uses /run/mu300/sim-slot, never this preference.
# The caller sets SIM_CONFIG_U30=1 only after positive U30 Air/OpenWrt identification.
sim_config_root=${MU300_SIM_ROOT:-}
sim_cfg_valid() { case "$1" in 0|1) return 0;; *) echo 'SIM slot must be 0 or 1' >&2; return 1;; esac; }
sim_cfg_default() {
    local slot
    if command -v uci >/dev/null 2>&1; then
        if slot=$(uci -q get network.wan.sim_slot); then :
        elif [ "${SIM_CONFIG_U30:-0}" = 1 ]; then
            slot=$(cat "$sim_config_root/etc/mu300-sim-slot" 2>/dev/null || true)
        fi
    else
        slot=$(cat "$sim_config_root/etc/mu300/sim-slot" 2>/dev/null || true)
    fi
    slot=${slot:-0}
    sim_cfg_valid "$slot" || return 1
    printf '%s\n' "$slot"
}
sim_cfg_hot() {
    local hot
    [ "${SIM_CONFIG_U30:-0}" = 1 ] || { echo 0; return 0; }
    if hot=$(uci -q get network.wan.sim_hot); then :
    else hot=$(cat "$sim_config_root/etc/mu300-sim-hot" 2>/dev/null || true); fi
    hot=${hot:-0}
    case "$hot" in 0|1) printf '%s\n' "$hot";; *) echo 'Invalid SIM hot-switch policy' >&2; return 1;; esac
}
sim_cfg_write() {
    local option=$1 value=$2 old present=0
    case "$option" in sim_slot|sim_hot) ;; *) return 1;; esac
    if old=$(uci -q get "network.wan.$option"); then present=1; fi
    uci set "network.wan.$option=$value" || return 1
    if uci commit network; then return 0; fi
    # A failed commit must not advertise the unsaved staged value as the next-boot preference.
    if [ "$present" = 1 ]; then uci set "network.wan.$option=$old" || true
    else uci -q delete "network.wan.$option" || true; fi
    return 1
}
sim_cfg_save_default() {
    local tmp
    sim_cfg_valid "$1" || return 1
    if command -v uci >/dev/null 2>&1; then
        sim_cfg_write sim_slot "$1"
    else
        mkdir -p "$sim_config_root/etc/mu300" || return 1
        tmp=$(mktemp "$sim_config_root/etc/mu300/sim-slot.XXXXXX") || return 1
        printf '%s\n' "$1" > "$tmp" && mv -f "$tmp" "$sim_config_root/etc/mu300/sim-slot"
    fi
}
sim_cfg_save_hot() {
    sim_cfg_valid "$1" || return 1
    [ "${SIM_CONFIG_U30:-0}" = 1 ] && command -v uci >/dev/null 2>&1 || return 1
    sim_cfg_write sim_hot "$1"
}
sim_cfg_unstage_added() {
    local key
    for key in "$@"; do
        case "$key" in sim_slot|sim_hot|apn_internal|pdptype_internal) uci -q delete "network.wan.$key" || true;; esac
    done
}
sim_cfg_migrate() {
    local slot hot saved_slot saved_hot slot_present=0 hot_present=0 touched= key value old destination
    [ "${SIM_CONFIG_U30:-0}" = 1 ] || return 0
    command -v uci >/dev/null 2>&1 || return 1
    if saved_slot=$(uci -q get network.wan.sim_slot); then slot_present=1; fi
    if saved_hot=$(uci -q get network.wan.sim_hot); then hot_present=1; fi
    [ "$slot_present" = 0 ] || sim_cfg_valid "${saved_slot:-0}" || return 1
    [ "$hot_present" = 0 ] || sim_cfg_valid "${saved_hot:-0}" || return 1
    slot=$(cat "$sim_config_root/etc/mu300-sim-slot" 2>/dev/null || true)
    hot=$(cat "$sim_config_root/etc/mu300-sim-hot" 2>/dev/null || true)
    # Validate every legacy value before making any staged change.
    [ "$slot_present" = 1 ] || [ -z "$slot" ] || sim_cfg_valid "$slot" || return 1
    [ "$hot_present" = 1 ] || [ -z "$hot" ] || sim_cfg_valid "$hot" || return 1
    if [ "$slot_present" = 0 ] && [ -n "$slot" ]; then
        uci set "network.wan.sim_slot=$slot" || { sim_cfg_unstage_added $touched; return 1; }
        touched="sim_slot"
    fi
    if [ "$hot_present" = 0 ] && [ -n "$hot" ]; then
        uci set "network.wan.sim_hot=$hot" || { sim_cfg_unstage_added $touched; return 1; }
        touched="$touched sim_hot"
    fi
    # An explicit empty APN is also a user choice; copy the old shared profile only into absent options.
    if [ "$slot" = 1 ]; then
        for key in apn pdptype; do
            if old=$(uci -q get "network.wan.${key}_internal"); then continue; fi
            value=$(uci -q get "network.wan.$key" || true)
            if [ -n "$value" ]; then
                uci set "network.wan.${key}_internal=$value" || { sim_cfg_unstage_added $touched; return 1; }
                touched="$touched ${key}_internal"
            fi
        done
    fi
    if [ -n "$touched" ] && ! uci commit network; then
        sim_cfg_unstage_added $touched
        return 1
    fi
    for old in "$sim_config_root/etc/mu300-sim-slot" "$sim_config_root/etc/mu300-sim-hot"; do
        [ -e "$old" ] || continue
        mkdir -p "$sim_config_root/etc/mu300/sim-migration" || return 1
        destination=$sim_config_root/etc/mu300/sim-migration/${old##*/}.migrated
        [ ! -e "$destination" ] || destination=$destination.$(date +%s).$$
        mv "$old" "$destination" || return 1
    done
}
