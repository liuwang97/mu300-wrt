#!/bin/sh
# MU300: the USB host's address on the LAN, sourced by preinit (06_mu300_early_usb) and uci-defaults (90-mu300) so
# both give the host the same one. .200 of the router's /24 when that is inside the LAN subnet and not the router
# itself (.199 then); in a smaller subnet (a /25 without .200), the last host address that is not the router.
mu300_ip2int() {  # mu300_ip2int A.B.C.D
	set -- $(echo "$1" | tr . ' ')
	echo $(( ($1 << 24) | ($2 << 16) | ($3 << 8) | $4 ))
}
mu300_int2ip() {
	echo "$(( ($1 >> 24) & 255 )).$(( ($1 >> 16) & 255 )).$(( ($1 >> 8) & 255 )).$(( $1 & 255 ))"
}
mu300_prefix2mask() {  # mu300_prefix2mask 8..30
	mu300_int2ip $(( (0xffffffff << (32 - $1)) & 0xffffffff ))
}
mu300_usb_host_ip() {  # mu300_usb_host_ip ROUTER NETMASK
	_r=$(mu300_ip2int "$1"); _m=$(mu300_ip2int "$2")
	_net=$(( _r & _m )); _bc=$(( _net | (~_m & 0xffffffff) ))
	_h=$(( (_r & 0xffffff00) | 200 ))
	[ $_h -eq $_r ] && _h=$(( _h - 1 ))
	if [ $_h -le $_net ] || [ $_h -ge $_bc ]; then
		_h=$(( _bc - 1 ))
		[ $_h -eq $_r ] && _h=$(( _bc - 2 ))
	fi
	mu300_int2ip $_h
}
