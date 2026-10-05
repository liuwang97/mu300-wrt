# Tests

```sh
python3 -m unittest discover -s tests            # everything this machine can run
MU300_TEST_SHELLS="busybox sh" python3 -m unittest discover -s tests   # one shell only
powershell -File tests\installer.Tests.ps1        # Windows: install.ps1's functions
```

Standard library only (`lz4`, the command or `pip install lz4`, for the boot image tests). CI runs them on Ubuntu,
macOS and Windows (`.github/workflows/tests.yml`).

| file | what |
|---|---|
| `test_static.py` | every script parses under each shell that runs it; device programs are executable; rules from past bugs (no double quotes in `install.ps1`'s device commands, ASCII-only PowerShell, init looks for partitions after the modules) |
| `test_i18n.py` | `tools/i18n.sh`: every translation with every placeholder, arguments passed through untouched, answers in all three languages |
| `test_device_scripts.py` | `mu300-device`, `mu300-lan-ip`, `mu300-led` (both devices, 4G/5G, the timeout, the siren, the 5.4 LDO switches), `thermal-guard` (the heat alarm), `mu300-nfc` (a fake NFC tag: ZTE's own Wi-Fi record byte for byte, URLs, text, what `sync` leaves alone), `mu300-ttl` (stub `nft`), `mu300-wifi-band`, `mu300-buttons`, `mu300-usb` (a fake charger: never 5 V against a supply) |
| `test_mu300cell.py` | `mu300cell.sh` (the OpenWrt netifd protocol), sourced with the netifd functions, `ip`, `mobile-data` stubbed: external updates, the v4 address installed by the script, v6 flush, teardown, `sleep 20` after an attach failure, the extendprefix interface; relay mode (K30, K35-K37, K64): the monitor `mu300cell-v6.sh`, `ndp-learn`, `init.d/mu300-ndp`, `91-mu300-luci`, mobile-data's `v6_relay_ra` |
| `test_atd_dash.py` | `init.d/mu300-atd-dash` (K19): two procd instances on nr6/nr7 with their own `MU300_AT_DIR` and no URC channel, START 19, mode 100755, enabled only in the luci block of `build-rootfs.sh`; the panel's collector tries `/run/mu300-at6`, `at7`, then nr1 |
| `test_early_dhcp.py` | the USB host's early DHCP lease from `boot/init` into the system (K5, K9, K11, K13, K25-K27): OpenWrt's preinit server on the system's own LAN subnet, the LAN hook that ends it and reattaches `rndis0`, the first-boot defaults (the host pinned with a broadcast lease, never the router; the `earlyusb` zone once; `rndis0` bridged only when it exists), Ubuntu's `lan-start`; under each shell |
| `test_ttl.py` | `mu300-ttl` with fake `uci`/`fw4`: OpenWrt's flow offloading is off while a TTL is set (also at boot), back on after `off`, untouched without fw4 (Ubuntu) |
| `test_installer.py` | which adb device the installers take: they ask whenever it is not the only one and an F50/U30 Air |
| `test_vpn.py` | `mu300-vpn`: VLESS URI parsing, JSON, which networks stay out of the tunnel, the sing-box config |
| `test_update.py` | `mu300-update`: release files per system and kernel, boot image byte helpers, whether a kernel bundle may go onto this device |
| `test_boot_init.py` | `boot/init`: the card is looked for before the internal region, under any `mmcblkN`; a foreign or empty card falls back; the 300 s timer and the conditional wait |
| `test_android_install.py` | `tools/android-install.sh` on the SD card: a blank card is formatted, a foreign ext4 and an adopted or busy card are refused |
| `test_boot_image.py` | `boot/build-boot-image.py`: the generic ramdisk, the U30 Air's modules and order |
| `test_luci_i18n.py` | the control panel's catalogs: complete in Turkish and Chinese, no Chinese outside them |
| `test_po2lmo.py` | `tools/po2lmo.py` against catalogs LuCI's own po2lmo produced (`tests/fixtures/po2lmo`) |
| `test_mu300dash_security.py` | the panel's rpcd backend `mu300dash` and its adapters (S1): every parameter checked against an allow-list, untrusted text only as one exact argument or on stdin, every reply JSON, the shared escaper, the ACL |
| `test_sms_pool.py` | the SMS pool (K69, D11): `mu300-sms` and `mu300-smsd` keep the SIM's and the panel's messages in `/etc/mu300/sms-pool`, through a stub `mu300-at`; the panel side runs on the real `mu300-sms` |
| `test_usb_management.py` | the panel's USB management (`device-usb`, K7, K8, D12): role switch, host mode, NIC reattachment, the USB network mode file `boot/init` applies on the next boot |
| `test_wifi.py` | `wifi-start` loads the WCN modules without `modules.dep` (K55); `mu300-hw` restarts the AP once the country is live (K15) |
| `test_early_replay_hook.py` | the early hook that replays the LuCI plugin's saved locks before the radio comes on (K65): its place in `mobile-data` |
| `test_unisoc_plugin.py` | the plugin's portable adapter boundary (K81): the custom AT backend contract, `--available` sends nothing, `boot-replay` (zero AT traffic when disabled, late replay, the early window), `lock replay early` restores every saved lock without `SFUN` |
| `installer.Tests.ps1` | `install.ps1`: `T` with every translation, `NormalizeAnswer`, `Gib` |

The device scripts run under dash (Ubuntu's `/bin/sh`), bash and busybox ash (OpenWrt); every shell test runs under
each of them that is installed. Commands that touch the device (`nft`, `ip`, `id`, `sing-box`) are stubs, and the
scripts read a fake `/` through `MU300_SYSROOT`, `MU300_DISK` and friends; `mu300-update` and `mu300-vpn` are sourced
with `MU300_LIB=1`, which defines their functions and runs nothing.
