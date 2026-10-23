/* SPDX-License-Identifier: GPL-2.0-only */
/* MU300: the SC2355 firmware runs SAE for a soft AP (hunting-and-pecking, group 19); hostapd runs the 4-way
 * handshake with the PMK the firmware appends to EVT_NEW_STATION (dd 34 40:45:da:04 <PMK 32><PMKID 16>).
 * START_AP first hands the firmware the standard nl80211 SAE password. Included by cmdevt.c. */
static int sc2355_prepare_sae(struct sprd_priv *priv, struct sprd_vif *vif,
			    struct cfg80211_ap_settings *settings)
{
	struct sc2355_sae_password {
		__le16 request_type;
		__le16 entry_type, entry_len;
		__le16 password_type, password_len;
		u8 data[];
	} __packed;
	const struct cfg80211_crypto_settings *crypto = &settings->crypto;
	struct sc2355_sae_password *p;
	struct sprd_msg *msg;
	bool sae = false;
	int i;

	for (i = 0; i < crypto->n_akm_suites; i++) {
		if (crypto->akm_suites[i] == WLAN_AKM_SUITE_FT_OVER_SAE)
			return -EOPNOTSUPP;
		if (crypto->akm_suites[i] == WLAN_AKM_SUITE_SAE)
			sae = true;
	}
	if (!sae)
		return 0; /* WPA2, EAP and open need no password here */
	if (!(priv->extend_feature & SPRD_EXTEND_SOATAP_WPA3) ||
	    !(priv->fw_capa & SPRD_CAPA_AP_SME) ||
	    !(priv->fw_std & SPRD_STD_11W))
		return -EOPNOTSUPP;
	/* the firmware's password entry has no H2E: an AP that advertises it would refuse every client */
	if (crypto->sae_pwe == NL80211_SAE_PWE_HASH_TO_ELEMENT ||
	    crypto->sae_pwe == NL80211_SAE_PWE_BOTH)
		return -EOPNOTSUPP;
	if (!crypto->sae_pwd || !crypto->sae_pwd_len ||
	    crypto->sae_pwd_len > 63)
		return -EINVAL;

	/* the wire format of Android's vendor command 43: request 2, then ENTRY(0), PWD(7) and END(255) with 16-bit LE
	 * TLV headers; the password length has no terminator */
	msg = get_cmdbuf(priv, vif, sizeof(*p) + crypto->sae_pwd_len + 4,
			 CMD_SET_SAE_PARAM);
	if (!msg)
		return -ENOMEM;
	p = (struct sc2355_sae_password *)msg->data;
	memset(p, 0, sizeof(*p) + crypto->sae_pwd_len + 4);
	p->request_type = cpu_to_le16(SPRD_SAE_PASSWORD_ENTRY);
	p->password_type = cpu_to_le16(7);
	p->password_len = cpu_to_le16(crypto->sae_pwd_len);
	memcpy(p->data, crypto->sae_pwd, crypto->sae_pwd_len);
	p->data[crypto->sae_pwd_len] = 0xff;
	return send_cmd_recv_rsp(priv, msg, NULL, NULL);
}
