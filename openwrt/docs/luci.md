# LuCI control panel (luci-app-mu300)
Applies to: Linux 7.2.11, OpenWrt 25.12.5 (F50, U30 Air). Checked against the code: 2026-10-23.

The panel is `openwrt/luci-app-mu300`: pages in `htdocs/luci-static/resources/view/mu300/`, shared code `htdocs/luci-static/resources/mu300/common.js`, backend `root/usr/libexec/rpcd/mu300dash` and `mu300vpn`.

## Languages
The image ships English (the source ids in the JavaScript) and Chinese (`po/zh_Hans`, built by `openwrt/build-rootfs.sh` into `mu300.zh-cn.lmo` with `openwrt/tools/po2lmo.py`). LuCI names the language `zh-cn`; another catalog name is not loaded. `openwrt/tools/luci-i18n.py` allows a non-literal `_()` argument only for names it lists.

## Calls never block on the modem
- rpcd ends a call at 30 s (`rpcd -t 30`) and the browser at 20 s (LuCI's `rpctimeout`), but rpcd never kills the call's children. So no request reads the modem inline: `lock_get` serves a cache in `/run` and refreshes it in the background, one reader at a time. Collector rounds have deadlines (14 s live, 12 s and 25 s in the background; at most three collectors).
- With the AT daemons stopped for 150 s: `sysinfo` and `status` answered in 0.5 s, the AT terminal said "busy" after 3 s, `lock_get fresh` served the cache after 12 s, `signal` gave up at 14 s (unverified on 7.2).
- LuCI batches a page's calls into one HTTP request that uhttpd answers one after another, so one slow call blanks the page. Every panel call is `nobatch` (`common.js`) and polls back off after failures.
- A lock change (`lock_set`, except `reset` and `auto_apply`) is a job: `lock start KIND VALUE` answers at once with an id, `lock_job` reads how it ended (no AT), and the page waits for `done` before its read-back, so a write the modem refused is said as such. At most 4 open jobs and 64 in all; one past 420 s or with its worker gone ends in error.
- The panel logs its calls (method, time, outcome, never a value) in RAM, copied to `/etc/unisoc-modem/panel-ops.log` every five minutes at most and at shutdown.
- Channel Analysis scans only on a click while the driver has an access point: a scan takes the AP off its channel.

## Status in rpcd
A shell backend cost 70.7 % of one core with the home page open and 9.5-11 % idle (~700 processes a minute). `mu300status` (rpcd-mod-ucode, module `mu300.dash` in `root/usr/share/ucode/mu300/dash.uc`) serves `status`, `sysinfo` and `sms_head` from caches sized to how fast each value changes: about 5.4 ms a call, never waiting for the modem; a stale cache starts a new collector round. Measured with it: idle 1.3 %, 2.8 % with the page open (U30 Air, unverified on 7.2).

## Identity and cells
- The identity collector (`root/usr/libexec/unisoc-modem/cell`) caches IMEI, IMSI and ICCID for 6 h on flash (`/etc/unisoc-modem/sim.json`, `sim-slot1.json` for the second slot) and only when all three were read. A partial read is shown for that round and kept 60 s in `/var/run/unisoc-modem/sim.json.partial`, never replacing a complete cache. No SIM or a SIM waiting for its PIN is the same partial case.
- Answers are parsed strictly: the value is a line of its own after an optional `+CGSN:`-like prefix, and an error line is none (`+CME ERROR: 10` must not become the IMEI "10"). The ICCID is 18-22 upper-case hex nibbles without the `F` padding.
- `carrierName` (`common.js`) translates a name the catalog has exactly; else it folds case and spacing among the five mainland carriers (`CARRIER_NAMES`), then looks up the PLMN (`PLMN_CN`), because catalog keys match exactly (`CHINA BROADNET` from `+COPS` would stay English).
- Network locks accept a cell by hand: LTE or NR, channel number (EARFCN 0-262143, NR-ARFCN 1-3279165, not MHz), PCI (0-503 LTE, 0-1007 NR); it locks like a neighbor row's button (`lock apply cell lte:EARFCN,PCI`) and the backend's `valid_cell` (digits, at most 7 and 4) is the check.

## Actions and VPN page
- The restart button asks Cancel / Power off / Restart; both go through `root/usr/libexec/unisoc-modem/action`: `reboot` or `poweroff` after a 3 s grace for the reply, orderly via procd, never forced.
- `mu300vpn`'s `cli_message` takes the line before the CLI's closing line (`profile ID does not check: ...`) as the message, never a `note:` line, so a refusal shows its reason.
