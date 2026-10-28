# Vendor modules

Applies to: Linux 7.2.13, OpenWrt 25.12.5 (F50, U30 Air). Checked against the code: 2026-10-23.

Out-of-tree Unisoc/ZTE code in `kernel/modules/`: `sprd_modem` (SIPC, SIPA, mailbox, Trusty, modem loader), `wcn_bsp`,
`sprd_wlan_combo`, `sprdbt_tty`, `sprd_pmic_wdt`, `mu300_thermal`, `ums9620_lpintc`. Load order: `kernel/initramfs/module-order.txt`. In-tree port and patches: `drivers.md`.

## Modem stack (SIPC, SIPA)

- The downlink is the modem's decision: over SIPC channel 120 (dst 5) the modem sends `sipa_dele` type=5 flag=1, the AP grants
  `SIPA_RM_RES_CONS_WWAN_DL` and acks type=6. Without that `rx_packets` stays 0 although everything else looks right; check
  `dmesg | grep -E 'sipa_dele|sipa_rm'` (unverified on 7.2). `dts parsing failed` and `get resource failed for remote-base!` are benign.
- `conn_thread` gets one attempt (`smsg_ch_open(dst, chan, -1)` waits for ever or errors out) and `sipa_dele` is `[permanent]`, so
  it is not in `module-order.txt`: the root filesystem inserts it after `sprd-sbuf: channel 5-*` or
  `sbuf ready for pmic wdt init` appears (~60 s, when `modem_control` has the CP talking) (unverified on 7.2).
- `sipa_delegate` treats `-EINPROGRESS` from `sipa_delegator_start()` as success (the WAN is up before the delegate loads, so
  `CONS_WWAN_UL` is already granted); the vendor code failed the probe and freed the delegator under a running thread.
  `pm_runtime_get_sync()` returns 1 for an active device, which is not a failure (a retry every millisecond locked the CPU)
  (both unverified on 7.2).
- An idle link reads as broken: SIPA runtime-suspends seconds after the last packet. Read `/sys/kernel/debug/sipa/` only while
  traffic flows. "fail to send, ret -115" (`-EINPROGRESS`) on the first packet after ~25 s idle is normal flow control;
  `sipa_eth_start_xmit()` undoes its 14-byte Ethernet push before `NETDEV_TX_BUSY`. `sipa_eth` is not reload-safe (a reload
  restarted the F50): never `rmmod` it.
- The mailbox queues in a software fifo when `send()` fails and only the inbox interrupt woke the deliver thread; a slow
  remote core raises none, so one `-ETIMEDOUT` silenced the AP for good. Queueing wakes the thread, which retries every 1 ms
  (unverified on 7.2).
- Memory shared with the modem lies inside System RAM (no `no-map`) and the modem does not snoop AP caches, so it must be mapped
  non-cached: `vmap(..., pgprot_noncached(PAGE_KERNEL))` (`sipc/smem.c`, `wcn_bsp/platform/wcn_misc.c`); `memremap` gives write-back
  and `ioremap_wc()` is refused for System RAM. A cached mapping shows as `fail SIZE` in the NV server and `wait modem alive timeout`.
- Kernel threads in D state raise the load average (~12 at idle) and trigger hung-task reports. So `sipa-set-rps` is not
  created, `sipa-free-N`/`sipa-fill-recv-N`, `slog-0-0` and the Wi-Fi TX thread sleep interruptibly or `TASK_IDLE`; idle load
  is ~0.4 (6.18).
- Per-round messages (`sipa_rm`, `sipa_dele` smsg, WCN loopcheck, `edma-tp`) are `pr_debug`/`dev_dbg`, real errors rate-limited
  (idle `kmsg`: ~5 lines a minute). Kernel 7.x requires `WQ_PERCPU` or `WQ_UNBOUND` on workqueues.
- Boot-warning fixes: MACs via `eth_hw_addr_random()`/`eth_hw_addr_set()` (never writing `dev_addr`); no `base_addr` sysfs
  attributes without show/store; `sprdbt_tty` links one tty port only.

## Wi-Fi: `wcn_bsp`, `sprd_wlan_combo` (SC2355 on PCIe)

- Needs `pcie-sprd-misc`, `pcie-sprd`, `wcn_bsp`, `sprd_wlan_combo` and Android's `wcnmodem.bin`, `gnssmodem.bin`,
  `wifi_board_config.ini`, `wifi_board_config_ab.ini` in `/lib/firmware`. For unknown projects the driver falls back to `wifi_board_config_hulk.ini`;
  the firmware then asserts (`LOAD_INI_DATA_FAILED`) and `wlan0` never comes up. The MAC is LK's
  `androidboot.wifimac` from `/chosen/bootargs`. `rmmod wcn_bsp` crashes the kernel (unverified on 7.2).
- The chip may appear on PCIe later than the 5 s `wcn_bsp` waits: the timeout removes the half-probed card, so
  `remove_done` is initialised before every scan, `remove` checks for a probe that never set its data, and one timed-out scan is
  retried before Wi-Fi/BT are given up (unverified on 7.2).
- RX uses `dma_sync_single_for_cpu()`: since 5.19 `_for_device` only cleans `DMA_FROM_DEVICE` buffers on arm64, so the CPU read stale
  lines (`hw csum failure`) and logging hung the board (unverified on 7.2). The firmware's checksum is trusted only when it matches
  the frame's pseudo-header.
- The command-list `complock` is taken in the PCIe tx-complete interrupt, so it uses `spin_lock_irqsave`. A PCIe pop callback that
  runs before `tx_init()` made `hif->tx_mgmt` returns the buffers and leaves, else an Oops in interrupt (unverified on 7.2).
- Power transitions: `sprd_iface_set_power` holds `hif->power_lock`; the probe powers the chip off outside RTNL while netifd opens
  `wlan0` within 130 ms, and the overlap cleared `sc2355_hif.hif` under a live interface. `sc2355_hif.hif` is published with a release store
  before the channels register. `sprd_wlan_remove` must call `sprd_iface_remove()`, else the recovery watchdog timer outlives the
  module (panic in `__run_timers` on `rmmod`). Timers in `priv` need an owner at free.
- TX: `skb_cow_head()` instead of replacing the skb (callers kept using a freed skb); the AP's IPv6 multicast goes through the data queue
  (the command path failed 2 of every 8 frames); a full EDMA pending ring returns `-EAGAIN` without counting a failure and a
  `tx_complete` callback wakes the TX thread. Per-frame lines are rate-limited.
- `sc2355_send_cmd_recv_rsp()` returns `-EIO` when the lock is refused and `-EBUSY` in the blocked windows (it returned 0 without
  sending); `get_station` starts zeroed (stack bytes reached nl80211). In AP mode it answers only for firmware peer-table entries and
  `iw station dump` shows no figures: the firmware keeps none.
- `sc2355/rx.c` compares the big-endian `eth->h_proto` with host order, so every client group frame is copied back to the BSS and
  also handed to the stack. Left as is: a bridge never sends a frame back out its ingress port, so the copy is what lets
  mDNS/SSDP between Wi-Fi clients work (a duplicate with hairpin).
- `edma_rx_push_isr()` (`wcn_bsp/pcie/edma_engine.c`) pops what lies between ring head and hardware cursor, then refills
  (unverified on 7.2). `sprd_wlan_combo.refill_bus_resume=1` releases a refused refill at once; off by default, unmeasured.
- Without a WoWLAN configuration cfg80211 closes every interface on suspend while the WCN PCI function has taken the bus down; the
  firmware assert (`WCN Assert in mchn.c line 137`) follows. The driver sets WoWLAN "any" at registration. `at+armlog=1` after resume made the
  firmware pull WAKE# and end `mem`: the log is off by default (`/sys/devices/virtual/misc/wcn/devices/armlog_status`).
- `iw dev wlan0 set type __ap` on a down interface fails once with `EINVAL`; a retry succeeds. A good `CMD_START_AP` leaves no
  kernel log line, and `/sys/class/firmware` does not exist (no sysfs fallback).

## Bluetooth and the GPU

- `sprdbt_tty` exposes `/dev/ttyBT0` (H4) over the WCN link and a `bluetooth` rfkill; `btattach -B /dev/ttyBT0 -P h4` makes `hci0`. Vendor
  init first (the root filesystem's Bluetooth init program): `0xFCA0` PSKey (BD address at bytes 20..25), `0xFCA2` RF, `0xFCA1 00 00 01`. Linux uses
  `BDADDR=` from `/etc/mu300/bluetooth.conf` or an address from `machine-id`. The firmware rejects Hold and Park in the default
  link policy: `0002-bluetooth-marlin3-link-policy.patch` requests Role Switch and Sniff only (all unverified on 7.2).
- The Mali-G57 (`23140000.gpu`) has no driver: the vendor DDK is not built, and Panfrost is kept out
  (`all-modules.exclude`) until the GPU is brought up with it. The GPU stays powered down; a GPU-side access at the
  wrong time is fatal on this SoC (the vendor driver's teardown ended in an SError).
