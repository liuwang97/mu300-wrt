# Shared SIM preference storage. Runtime routing uses /run/mu300/sim-slot, never this preference.
# The caller sets SIM_CONFIG_U30=1 only after positive U30 Air/OpenWrt identification.
sim_config_root=${MU300_SIM_ROOT:-}
sim_cfg_valid() { case "$1" in 0|1) return 0;; *) echo 'SIM slot must be 0 or 1' >&2; return 1;; esac; }
sim_cfg_default() {
    local slot
    if command -v uci >/dev/null 2>&1; then
        slot=$(uci -q get network.wan.sim_slot || true)
        if [ -z "$slot" ] && [ "${SIM_CONFIG_U30:-0}" = 1 ]; then
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
    hot=$(uci -q get network.wan.sim_hot || true)
    [ -n "$hot" ] || hot=$(cat "$sim_config_root/etc/mu300-sim-hot" 2>/dev/null || true)
    hot=${hot:-0}
    case "$hot" in 0|1) printf '%s\n' "$hot";; *) echo 'Invalid SIM hot-switch policy' >&2; return 1;; esac
}
sim_cfg_save_default() {
    local tmp
    sim_cfg_valid "$1" || return 1
    if command -v uci >/dev/null 2>&1; then
        uci set "network.wan.sim_slot=$1" && uci commit network
    else
        mkdir -p "$sim_config_root/etc/mu300" || return 1
        tmp=$(mktemp "$sim_config_root/etc/mu300/sim-slot.XXXXXX") || return 1
        printf '%s\n' "$1" > "$tmp" && mv -f "$tmp" "$sim_config_root/etc/mu300/sim-slot"
    fi
}
sim_cfg_save_hot() {
    sim_cfg_valid "$1" || return 1
    [ "${SIM_CONFIG_U30:-0}" = 1 ] && command -v uci >/dev/null 2>&1 || return 1
    uci set "network.wan.sim_hot=$1" && uci commit network
}
sim_cfg_migrate() {
    local slot hot saved_slot saved_hot changed=0 key value old destination
    [ "${SIM_CONFIG_U30:-0}" = 1 ] || return 0
    command -v uci >/dev/null 2>&1 || return 1
    saved_slot=$(uci -q get network.wan.sim_slot || true)
    saved_hot=$(uci -q get network.wan.sim_hot || true)
    [ -z "$saved_slot" ] || sim_cfg_valid "$saved_slot" || return 1
    [ -z "$saved_hot" ] || sim_cfg_valid "$saved_hot" || return 1
    slot=$(cat "$sim_config_root/etc/mu300-sim-slot" 2>/dev/null || true)
    hot=$(cat "$sim_config_root/etc/mu300-sim-hot" 2>/dev/null || true)
    if [ -z "$saved_slot" ] && [ -n "$slot" ]; then
        sim_cfg_valid "$slot" || return 1
        uci set "network.wan.sim_slot=$slot" || return 1
        changed=1
    fi
    if [ -z "$saved_hot" ] && [ -n "$hot" ]; then
        sim_cfg_valid "$hot" || return 1
        uci set "network.wan.sim_hot=$hot" || return 1
        changed=1
    fi
    # The old U30 configuration had one profile. Preserve it for SIM2 unless an internal profile already exists.
    if [ "$slot" = 1 ]; then
        for key in apn pdptype; do
            old=$(uci -q get "network.wan.${key}_internal" || true)
            value=$(uci -q get "network.wan.$key" || true)
            if [ -z "$old" ] && [ -n "$value" ]; then
                uci set "network.wan.${key}_internal=$value" || return 1
                changed=1
            fi
        done
    fi
    [ "$changed" = 0 ] || uci commit network || return 1
    # Keep the old preference files as backups, but stop using them as another source of truth.
    for old in "$sim_config_root/etc/mu300-sim-slot" "$sim_config_root/etc/mu300-sim-hot"; do
        [ -e "$old" ] || continue
        destination=$old.migrated
        [ ! -e "$destination" ] || destination=$old.migrated.$(date +%s).$$
        mv "$old" "$destination" || return 1
    done
}
