# Modem, SIM and SMS on OpenWrt
Applies to: Linux 7.2.11, OpenWrt 25.12.5 (F50, U30 Air). Checked against the code: 2026-10-23.

## AT channel ownership
- `/dev/stty_nr*` are SIPC channels: each line goes to one reader, a second opener reads nothing. `openwrt/opt/mu300/bin/mu300-modemd` (ucode, uloop) is the only owner of every AT channel of the boot and serves one command at a time per channel through fifos in `/run/mu300-at` (`mu300-at "AT+CSQ"`); the channels themselves are held by the C bridge `mu300-atd-io` (`openwrt/src/atd-io`), because ucode cannot read a tty without blocking and closing a SIPC channel silences it until a reboot. Nothing else may open a channel, not even `stty -F` (an open and close).
- modemd publishes `mu300.modem` on ubus (`at`, `status`, `cell`) and every unsolicited line as the event `mu300.modem.urc`; the SMS daemon, `mobile-data`'s watch and the panel wait for those events instead of polling. A restarted modemd takes over the bridge's open channels.
- The spipe driver's non-blocking read answers `ENODATA` when the ring is empty: the bridge takes `EAGAIN`, `ENODATA` and `EBUSY` for "nothing now" and never closes a channel on a read error.
- Two daemons on the channel each get part of every reply; this looks like a lost network while the modem is fine. So modemd is the only owner, and a client without modemd's command fifo has no AT at all: nothing falls back to opening a channel itself.
- `/run/mu300-at/owner` (daemon) and `/run/mu300-at/lock` (one client, one command) are separate; conflating them makes every client wait 90 s and print `busy` after a daemon restart.
- nr0 is drained into a capped log under `/run/mu300-at/urc/`; registration, PDP changes and `+CUSD` answers appear only there. A drainer owns its channel, so nr2-nr5 are left alone.
- The tty nodes exist before the channel works (open gives `ENODEV` until the modem starts its sbuf), so a broker waits for its `ready=sbuf:` condition in `/dev/sipc_sbuf` before the first open.
- Shell clients that wait need `read -t` (poll-based, only bash and busybox ash have it). OpenWrt's `sleep` and `read -t` refuse fractions, so they use the static busybox, else a 0.1 s wait costs 1 s.
- After a modem restart an open descriptor is dead and locks every opener out. Reopening a live modem's nr1 can leave it never answering (seen on 5.4, unverified on 7.2), so reopen only on evidence: five unanswered commands, at most one per 5 min, nr0 log quiet for 240 s.

## Command quirks
- The radio is off at boot (`+CFUN: 0`); `mobile-data` turns it on (`AT+SFUN=4`).
- `AT+CGDATA="M-ETHER",1` answers `^ORIG`, then `CONNECT` 10-20 s later: use a 45 s timeout, else a late `CONNECT` is read as the next answer. Drain before sending; read to a final result code.
- Long commands need a confirmation in the panel terminal; a client holding the lock past `lock/until` is ended. The terminal refuses `AT+SPACTCARD=`, `AT+SMMSWAP=`, `AT+SPSWDATA` (they move the route under a live connection).
- A USSD code is sent as hex (`AT+CUSD=1,"2A31303123",15`); the answer arrives seconds later on nr0. Its third field is a CBS coding scheme (15 GSM, 72 UCS2).
- `AT+CSCS` does not say how operator names are written (UCS2 hex arrives under HEX); `openwrt/luci-app-mu300/root/usr/libexec/unisoc-modem/cell` decodes only text that cannot be a name otherwise.
- The carrier returns no ICMP: test with TCP or DNS and watch `rx_packets` (`mu300-ipa-dump`).

## Data bearer and watchdog (`openwrt/opt/mu300/bin/mobile-data`)
- `AT+CGCONTRDP=1` gives the address (`a.b.c.d.m.m.m.m`) and DNS; `M-ETHER` binds the bearer to `sipa_eth<cid-1>` (raw IP, NOARP): assign /32, route `default dev sipa_eth0`. Measured 9.3 MB/s down, 1.3 MB/s up (5G NSA SIM).
- cid 11 is the IMS context, not a second bearer. A SIM without a data plan registers and gets an address but no data flows.
- The modem firmware occasionally resets and returns with `+CFUN: 0` and no context while `sipa_eth0` keeps its stale address. The watch waits for modemd's URC events (`+CGEV`, `NO CARRIER`, a registration loss) and netifd's wan events, and looks in full (`AT+CGACT?`, `AT+CGPADDR` against the interface address) on such an event, every 300 s, and when the bearer sends without receiving; `up` after two failures (a context may return with a new address); `mobile-data down` sets `/run/mu300-mobile-data-down`.
- A downlink can be dead with a healthy bearer when the VPN engine started just before it. `openwrt/overlay/etc/hotplug.d/iface/96-mu300-vpn-uplink` restarts the VPN once then; the watch also treats at least 20 TX and at most 2 RX packets over 90 s as a stall (VPN restart, `ifup wan`, radio cycle, then reboot only if under 200 packets were received since boot, at most 3 in a row; `network.wan.dl_reboot=0` turns it off).
- APN credentials go as `AT+CGPCO=0,"<user>","<password>",<cid>,<auth>` after `AT+CGDCONT`; the password is never in argv, the environment of others or logs (`mu300-at -` reads stdin). Unverified against a credential-checking APN.
- The wan option `iccid` binds the APN, PDP type and credentials to one card: `mobile-data` asks `AT+CCID` first; another card dials with its own context (credentials taken back), a card that gives no ICCID gets no dial.
- Roaming policy is `network.wan.roaming` (`roaming_internal` for SIM2), default allowed, from `AT+CEREG?` stat 5 (9, 10 as well); held dial exits 4 (`DATA_ROAMING`). Never shown with a real roaming SIM.
- VoLTE/VoNR choices live in `/etc/mu300/sim-voice` and are replayed at each card start; the F50 firmware does not take VoNR on.

## SIM
- `mu300-sim-state` keeps `ready|locked|absent` per slot in `/run/mu300/sim<slot>.state`; only the card in use is asked, since a powered-off SIM answers `+CME ERROR: 10` whatever is in it. `absent` twice makes the dial exit 6 (`SIM_MISSING`).
- `/etc/mu300/sim-radio` (`<slot0> <slot1>`, 1 on) is persistent; a card's radio off is `AT+SFUN=5`, on is `AT+SFUN=4`; CFUN 0 or 2 count as off. A bad file means `1 1`.
- U30 Air SIM2 uses nr3/nr4/nr5 and `sipa_eth8`; the ttys work once `/dev/sipc_sbuf` shows `sbuf_5_006, state: 1,`. One radio is on at a time: a live switch is card in use off, then the other on (`mu300-sim-u30`, `mu300-sim2-warmup`); the reverse order hangs `AT+SFUN=4` on nr4 and SIM1 loses its radio. Unproven with two working cards.

## SMS
- PDU mode only (text mode mangles alphanumeric senders); `openwrt/opt/mu300/lib/sms-pdu.awk` decodes. Turkish needs the national language locking shift IE `0x25`. Sending uses GSM 7-bit when it fits, else UCS-2.
- Long-message parts are grouped by sender, 8-bit reference, total and a 600 s window. After `MU300_SMS_PARTIAL_WAIT` (an hour) the pool stores the message with `[...]` for missing parts.
- Pool files are fsynced before rename, and before the first `AT+CMGD`. A full SIM takes no more messages, so `mu300-smsd` moves them to the pool.
- `mu300-smsd` asks `AT+CNMI=3,2,2,0,1` for direct `+CMT` delivery; `mu300-cmt-spool` spools it and `AT+CNMA=1` follows only once the pool holds it. Three failed rounds fall back to the SIM path (`AT+CNMI=2,1,0,0,0`).
- `mu300-sms send` holds the AT lock for the whole send, re-writes the SMSC read from `AT+CSCA?` (cured IMS "Busy Everywhere" on one network) and keeps `AT+CMGS=N<CR><PDU><Ctrl-Z>` one write.
- Forwarding (`openwrt/opt/mu300/bin/mu300-sms-forward`): body and headers reach the HTTP helper through stdin, never argv; `webhook_body` values are escaped for the content type in one awk pass, never evaluated; `webhook_expect KEY=VALUE` makes a 2xx count only if the JSON has that value.

## Cell locks and panel data
- `lock reset` (`openwrt/luci-app-mu300/root/usr/libexec/unisoc-modem/lock`) is verified: it writes seven fixed values, stops on an explicit ERROR (silence is not failure), reads back, restarts the stack once and reads again, and deletes the saved locks only then; it rewrites the panel's lock cache to the verified automatic state (new `ts`) before its result says ok, and the page paints an ok reset only from a reading since it began. A failed reset keeps them. Mode `auto152` (`SPTESTMODE=152`, no 2G) is not run on a device.
- LTE has no capability query; a cleared mask reads back as the modem's own table, fixed per board in `lock` (F50 `0,482,0,149,0` = B1/3/5/8/34/38-41; U30 Air W25.30.2 `0,2018,2057,2271,2` = B1-5/7/8/12/17/20/28/34/38-43/66). It is the panel's candidates and the apply filter, and a read-back holding all of it is automatic. Learning it from a read-back would take a lock left in NV for the table.
- The own number is `AT+CNUM`, else the IMS identity from `AT+SPPAURI?`; it is asked on full rounds only because IMS registers after the SIM is read, and is never written to flash.
- Bearer QoS maps `sipa_eth0-7` to CID 1-8 of slot 0 and `sipa_eth8-15` to slot 1 (`data_cid` in `openwrt/luci-app-mu300/root/usr/share/unisoc-modem/lib.sh`), so a stale or other-bearer answer is never shown.
- With `option ipv6 'relay'` the carrier's /64 comes by RA and `+CGCONTRDP` gives only the interface identifier; `mu300cell-v6.sh` follows `+CGEV` on the channels of the card in use.
