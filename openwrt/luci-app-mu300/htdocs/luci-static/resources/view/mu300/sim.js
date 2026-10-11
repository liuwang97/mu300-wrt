'use strict';
'require view';
'require dom';
'require rpc';
'require poll';
'require mu300.common as M';

return view.extend({
	load: function() { return L.resolveDefault(M.callSimGet(), {}); },
	render: function(st) {
		var self = this;
		M.injectCss();
		this.root = E('div', { 'class': 'mud' });
		this.paint(st);
		poll.add(function() { return M.callSimGet().then(function(s) { self.paint(s); }); }, 3);
		return this.root;
	},
	set: function(op, value) {
		var self = this;
		return M.callSimSet(op, String(value)).then(function(r) {
			if (!r || r.ok !== 1) M.toast(M.errText(r), { type: 'error', timeout: 6000 });
			else if (r.started) M.toast(_('Switching SIM. Mobile data will disconnect briefly.'));
			return M.callSimGet().then(function(s) { self.paint(s); });
		});
	},
	paint: function(st) {
		var self = this;
		var results = {
			failed: _('SIM switch failed; check SIM readiness and hot switching.'),
			idle: _('Ready'), switching: _('Switching SIM'), switched: _('SIM switched; saved as boot default'),
			rolled_back: _('Switch failed; previous SIM restored'),
			no_data: _('The other SIM got no mobile data within a minute; the previous SIM is back and stays the default'),
			reboot_required: _('Radio state unknown; reboot required')
		};
		/* mu300-sim answers ok:0 everywhere but on the U30 Air with OpenWrt: the page says so and offers nothing */
		if (!st || st.supported === 0 || (st.ok !== 1 && !st.result)) {
			dom.content(this.root, E('section', { 'class': 'mud-card' }, [
				E('h3', {}, [ _('Dual SIM') ]),
				E('p', {}, [ _('Dual SIM control is only for the U30 Air on OpenWrt.') ])
			]));
			return;
		}
		/* the last boot's cold start of the chosen SIM failed: the device is on SIM1 without live switching */
		var fallbacks = {
			'cold-start-failed': _('The chosen SIM could not be started at the last boot (no card, PIN, or the modem refused it).'),
			'brokers-missing': _('The second SIM\'s modem channels did not come up at the last boot.'),
			'unconfirmed': _('The last dual-SIM boot never confirmed itself.')
		};
		var card = E('section', { 'class': 'mud-card' }, [
			E('h3', {}, [ _('Dual SIM') ]),
			E('p', {}, [ _('A switch saves that SIM as the next cold-boot default only once it has mobile data; without data within a minute the previous SIM comes back and stays the default. Both SIMs share the one APN of the WAN interface.') ]),
			E('p', {}, [ _('Active SIM') + ': ' + (st.active ? 'SIM' + st.active : _('Unknown')) ]),
			E('p', {}, [ _('Cold-boot default') + ': ' + (st.default ? 'SIM' + st.default : _('Unknown')) ]),
			E('p', {}, [ results[st.result] || _('SIM control unavailable') ])
		]);
		if (st.fallback)
			card.appendChild(E('p', { 'style': 'color:#b45309;font-weight:600' }, [
				(fallbacks[st.fallback] || st.fallback) + ' ' + _('The device is back on SIM1 without live switching; choose again below to try once more.') ]));
		var hot = E('input', { 'type': 'checkbox', 'checked': st.hot ? '' : null, 'disabled': st.busy ? '' : null });
		hot.addEventListener('change', function() { self.set('hot', hot.checked ? 1 : 0); });
		card.appendChild(E('label', {}, [ hot, ' ', _('Enable hot switching') ]));
		if (!st.available)
			card.appendChild(E('p', {}, [ _('Reboot after enabling hot switching to prepare both SIM channels.') ]));
		[ 1, 2 ].forEach(function(n) {
			var row = E('div', { 'style': 'display:flex;gap:8px;margin:12px 0' });
			var b = E('button', { 'class': 'mud-btn',
				'disabled': (!st.hot || !st.available || st.busy || !st.active || st.active === n) ? '' : null },
				[ _('Switch to SIM%d').format(n) ]);
			b.addEventListener('click', function() { self.set('switch', n); });
			var d = E('button', { 'class': 'mud-btn', 'disabled': st.busy || st.default === n ? '' : null },
				[ _('Use SIM%d at next boot').format(n) ]);
			d.addEventListener('click', function() { self.set('default', n); });
			row.appendChild(b); row.appendChild(d); card.appendChild(row);
		});
		dom.content(this.root, card);
	},
	handleSaveApply: null,
	handleSave: null,
	handleReset: null
});
