# An update keeps what the image did not bring
Applies to: Linux 7.2.11, OpenWrt 25.12.5 (F50, U30 Air). Checked against the code: 2026-10-23.

## Context
A list of "user places" lost or altered files outside it.

## Decision
Every image carries `etc/mu300/image-manifest.gz` (`openwrt/tools/image-manifest.py`); what the old system has beyond its image's manifest is the user's. The new image's `mu300-update carry` (with `openwrt/src/carry/mu300-carry.c`, which keeps holes, xattrs, capabilities, nanosecond times and hard links that busybox `cp -a` loses) builds the new system. Under `/etc /var /home /root /srv /usr/local /opt /mnt /media` the user's file wins; changed image files elsewhere and the project's own programs, units and init scripts take the release's, with the user's version set aside in `/etc/mu300/update-kept/`; deletions stay deleted; security settings never become less protected; files follow accounts by name. Unchanged binaries become hard links to the installed ones, so an update writes about 8-10 MiB, not 58-60 MiB. The old system stays as `<os>.old` for `rollback`.

## Consequences
- Every image keeps `etc/mu300/image-manifest.gz` and `etc/mu300/image-version`.

## Revisit when
The image layout changes, or a device report shows a wrongly judged file class.
