# Tests of the root filesystem tree

```sh
cd openwrt/tests
python3 -m unittest discover                                   # everything this machine can run
MU300_TEST_SHELLS="busybox sh" python3 -m unittest discover   # one shell only (quick)
python3 -m unittest $(python3 shard.py 4 0)                    # what CI shard 0 of 4 runs
```

Standard library only. The device scripts run under OpenWrt's busybox ash; every shell test runs under each POSIX
shell the machine has (dash, bash, busybox sh), or those MU300_TEST_SHELLS names. Commands that touch the device are
stubs on PATH (`helpers.ShellTest.stub`), the device's files a scratch directory (MU300_SYSROOT and the like). The
ucode programs (mu300-sysd, mu300-modemd, the panel's collectors) need a ucode interpreter: `ucode` on PATH or
MU300_TEST_UCODE (a command line, e.g. a wrapper around the device's own ucode under qemu-aarch64); without one their
tests are skipped. The C programs (src/atd-io, src/carry) are built with the machine's C compiler, Linux only.

These tests never read a file under the kernel tree: what they need of the kernel is the bundle and
docs/contract.md. The `test_from_kernel_*` modules are the checks of this tree that the kernel tree's contract asks for.

| module | what |
|---|---|
| `test_static.py` | every script parses under the shell that runs it, executable bits, the image's build wiring, rules past bugs taught |
| `test_device_scripts.py` | mu300-device, mu300-lan-ip, mu300-at, mobile-data (the dial, the radio, the watchdog, data roaming, suspend), the SIM scripts, sipa-dele-start, bootmark, android-vendor-start, mu300-os, mu300-led, mu300-nfc, mu300-ttl, mu300-usb, mu300-usb-reset, mu300-vpn-uplink, mu300-next-boot, early-recorder, mu300-toolkit stress, the sysupgrade wrapper |
| `test_vendor_ids.py` | the vendor daemons' account: ueventd-perms.sh gives nobody more than before, android-run and android-vendor-start decide the account once |
| `test_power.py` | mu300-power: profiles, idle radios, the charging boot, low-battery power-off |
| `test_sysd.py` | mu300-sysd: the keys, the LED tick and the thermal guard on events |
| `test_cpu.py` | mu300-cpu: profiles, cores, the services' CPUs |
| `test_zram.py` | mu300-zram: compressed swap in RAM and its settings |
| `test_traffic.py` | mu300-traffic: the mobile data count, its cycle and its cap |
| `test_wifi.py`, `test_wifi_channels.py`, `test_wifi_client.py`, `test_wifi_sae.py` | Wi-Fi: wifi-start, a DFS channel becomes 36, wifi-client, the hotspot's WPA3-SAE (openwrt/hostapd) |
| `test_modemd.py`, `test_atd_io.py`, `test_sim2.py`, `test_sim_hot.py`, `test_sim_selection.py`, `test_modem_coldboot.py`, `test_mu300cell.py`, `test_ttl.py`, `test_tcp.py`, `test_sms_pool.py`, `test_sms_forward.py`, `test_run_dir.py`, `test_panel_resilience.py` | the modem: the AT daemon and its bridge, the SIMs, the netifd protocol, SMS |
| `test_update.py`, `test_update_page.py`, `test_carry.py`, `test_delta.py`, `test_user_packages.py`, `test_extra.py`, `test_releases.py`, `test_smoke.py` | updates, extras, releases and the release smoke test |
| `test_api.py`, `test_backup.py`, `test_usb_management.py`, `test_early_dhcp.py`, `test_uhttpd_key.py` | mu300-api, mu300-backup, USB, the early DHCP lease, the uhttpd key |
| `test_mu300dash_security.py`, `test_hotspot_ap.py`, `test_imei_diag.py`, `test_mu300vpn.py`, `test_terminal.py`, `test_dashboard_info.py`, `test_dashboard_power.py`, `test_luci_i18n.py`, `test_po2lmo.py`, `test_unisoc_plugin.py`, `test_early_replay_hook.py`, `test_vpn_hooks.py` | the LuCI control panel |
| `test_from_kernel_*.py` | the root filesystem's side of the contract: the autoload guard, the CPU voltage offset, eMMC-only lookups, logd's buffer, no call-audio stack |

`fixtures/` holds captured inputs (wifi-scripts, po2lmo catalogs, release tags, the v2026.10.18 ueventd-perms.sh the
account test compares with, fakes of the VPN module's CLI); `ucode-stubs/` the stand-ins for the device's ucode
modules.
