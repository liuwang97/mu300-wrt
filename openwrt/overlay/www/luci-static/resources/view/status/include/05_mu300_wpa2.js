'use strict';
'require baseclass';
'require fs';
'require ui';

/* MU300: WPA3 (SAE) on the hotspot is disabled on this Wi-Fi chip (the U30 Air's firmware SAE offload, mu300-linux
 * docs/U30AIR-SAE.md, FINDINGS 80). openwrt-wifi-wpa2-only switched a hotspot that had it to WPA2-PSK at boot (or
 * switched it off, without a usable password) and left /etc/mu300/wpa3-disabled; this says so on Status > Overview,
 * and on the control panel's home page (which loads this module), until it is dismissed. Nothing is shown without
 * the file. It carries its own English, Turkish and Chinese: the plain OpenWrt image has no catalog of the panel's.
 * (Chinese as \u escapes: the repository keeps CJK text in the catalogs only.) */

var NOTICE = '/etc/mu300/wpa3-disabled';

var TEXT = {
	en: {
		title: 'Wi-Fi security notice',
		migrated: 'WPA3 (SAE) on the hotspot is disabled for security: with it, a client could join without knowing the password (FINDINGS 80). The hotspot was switched to WPA2-PSK (CCMP) with the same password: %s. Devices that joined with WPA3 connect again with the same password.',
		down: 'WPA3 (SAE) on the hotspot is disabled for security (FINDINGS 80), and switching it to WPA2 failed: %s is off, not open. Set a WPA2 password (8-63 characters) under Network > Wireless and enable it.',
		dismiss: 'Dismiss'
	},
	tr: {
		title: 'Wi-Fi güvenlik bildirimi',
		migrated: 'Erişim noktasında WPA3 (SAE) güvenlik nedeniyle kapatıldı: onunla bir istemci parolayı bilmeden bağlanabilirdi (FINDINGS 80). Erişim noktası aynı parolayla WPA2-PSK\'ye (CCMP) geçirildi: %s. WPA3 ile bağlanan cihazlar aynı parolayla yeniden bağlanır.',
		down: 'Erişim noktasında WPA3 (SAE) güvenlik nedeniyle kapatıldı (FINDINGS 80) ve WPA2\'ye geçiş başarısız oldu: %s kapalı, şifresiz açık değil. Ağ > Kablosuz altında bir WPA2 parolası (8-63 karakter) girin ve etkinleştirin.',
		dismiss: 'Kapat'
	},
	zh: {
		title: 'Wi-Fi 安全提示',
		migrated: '出于安全原因，热点上的 WPA3（SAE）已被禁用：启用它时，客户端可能无需知道密码即可接入（FINDINGS 80）。热点已切换为 WPA2-PSK（CCMP），密码不变：%s。之前通过 WPA3 连接的设备可用同一密码重新连接。',
		down: '出于安全原因，热点上的 WPA3（SAE）已被禁用（FINDINGS 80），但切换到 WPA2 失败：%s 已关闭，而不是开放。请在“网络 > 无线”中设置 WPA2 密码（8-63 个字符）并启用它。',
		dismiss: '知道了'
	}
};

function lang() {
	var l = String((typeof document !== 'undefined' && document.documentElement && document.documentElement.getAttribute('lang')) || 'en').toLowerCase();
	return l.indexOf('zh') === 0 ? 'zh' : l.indexOf('tr') === 0 ? 'tr' : 'en';
}

function text(key) {
	var t = TEXT[lang()];
	return t[key] != null ? t[key] : TEXT.en[key];
}

/* the notice file: state=migrated|down, time=, migrated=SECTIONS, down=SECTIONS (no password in it) */
function parse(content) {
	var o = {};
	String(content || '').split('\n').forEach(function(line) {
		var i = line.indexOf('=');
		if (i > 0) o[line.substring(0, i)] = line.substring(i + 1);
	});
	return (o.state === 'migrated' || o.state === 'down') ? o : null;
}

return baseclass.extend({
	title: text('title'),

	load: function() {
		return L.resolveDefault(fs.read(NOTICE), null).then(parse);
	},

	render: function(n) {
		if (!n)
			return null;
		var box = E('div', { 'class': 'alert-message ' + (n.state === 'down' ? 'error' : 'warning'), 'id': 'mu300-wpa2-notice' });
		var msg = n.state === 'down' ? text('down').replace('%s', n.down || '-') : text('migrated').replace('%s', n.migrated || '-');
		box.appendChild(E('p', {}, [ msg + (n.time ? ' (' + n.time + ')' : '') ]));
		box.appendChild(E('button', {
			'class': 'btn cbi-button',
			'click': ui.createHandlerFn(this, function() {
				return L.resolveDefault(fs.remove(NOTICE), null).then(function() {
					box.style.display = 'none';
					/* on Status > Overview the section around it (title and all) goes too */
					var sec = box.parentNode && box.parentNode.parentNode;
					if (sec && sec.classList && sec.classList.contains('cbi-section'))
						sec.style.display = 'none';
				});
			})
		}, [ text('dismiss') ]));
		return box;
	}
});
