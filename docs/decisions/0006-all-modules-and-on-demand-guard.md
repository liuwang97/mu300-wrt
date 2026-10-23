# Every tristate is a module, and on-demand loading is guarded
Applies to: Linux 7.2.11, OpenWrt 25.12.5 (F50, U30 Air). Checked against the code: 2026-10-23.

## Context
Users want USB devices, extra filesystems and netfilter. With every module present, any local user can make the kernel load rare protocol, filesystem and line-discipline modules (`net-pf-*`, `fs-*` in a user namespace, `tty-ldisc-*`).

## Decision
`kernel/all-modules.py` turns every `tristate` that is off into `m` (fixpoint of `olddefconfig`); `kernel/all-modules.exclude` keeps out tests, ASoC, panels, SoC GPU drivers, VFIO and chip drivers wired only by other boards' trees; `kernel/check-dt-modules.py` fails on a module matching a `kernel/dt-compatibles.txt` compatible. Risky families are not built; others are listed in `autoload-blocked` (`kernel/autoload-blocked`, shipped in the bundle and installed as `/lib/modules/<release>/autoload-blocked`) and refused by `openwrt/opt/mu300/bin/mu300-modprobe-guard`, the kernel's modprobe helper, by module (names and aliases, dependants included). Root loads one with `modprobe NAME`; `/etc/mu300/autoload-allow-all` turns the guard off. Without its table (built once per boot) it fails closed for `net-pf-*`, `fs-*`, `tty-ldisc-*`, `rtnl-*`.

## Consequences
- 7.2.11: 3034 modules, 55.0 MB bundle; the ramdisk loads only `module-order.txt`.
- `kernel/check-autoload-blocked.py` runs in `make-bundle.sh`.

## Revisit when
RAM or disk pressure, a new DT compatible, or restricted user namespaces.
