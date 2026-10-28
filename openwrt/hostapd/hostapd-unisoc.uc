// Firmware-SME configuration shared by the OpenWrt ucode AP generator.
export function configure(config, module) {
	if (!(config.auth_type in [ 'sae', 'psk-sae' ]))
		return;
	if (config.auth_type == 'sae')
		config.ieee80211w = 2;
	else if (!config.ieee80211w)
		config.ieee80211w = 1;
	if (module != '/sys/module/sprd_wlan_combo')
		return;
	config.unisoc_sae = 1;
	config.sae_pwe ??= 0;
	// The firmware's SAE runs group 19 only (hostapd refuses any other list for unisoc_sae), where the generator
	// would offer '19 20 21'; and it is given no beacon protection, which the generator turns on with PMF.
	config.sae_groups = '19';
	config.beacon_prot ??= 0;
	// This legacy SME does not report the RSN Selection extension in its
	// association event. Advertising RSN Override then breaks message 2/4.
	config.rsn_override = 0;
	config.rsn_override_pairwise = null;
	config.rsn_override_key_mgmt = null;
	config.rsn_override_mfp = null;
};
