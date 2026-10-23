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
		var live = st && st.capabilities ? st.capabilities.live_switch === true : !!(st && st.supported);
		var results = {
			failed: _('SIM switch failed; check SIM readiness and hot switching.'),
			idle: _('Ready'), switching: _('Switching SIM'), switched: _('SIM switched; saved as boot default'),
			rolled_back: _('Switch failed; previous SIM restored'),
			no_data: _('The target SIM got no mobile data; the previous SIM is restored and the saved boot preference is unchanged.'),
			no_data_kept: _('The target SIM has no mobile data yet, and the previous SIM had none either: the device stays on the target. The saved boot preference is unchanged.'),
			reboot_required: _('Radio state unknown; reboot required'),
			restarting: _('Radio state unknown; the device is restarting.')
		};
		/* The shared controller exposes boot selection on F50 and live switching only on verified U30 Air/OpenWrt. */
		if (!st || (st.capabilities && !st.capabilities.boot_selection) || (st.ok !== 1 && !st.result)) {
			dom.content(this.root, E('section', { 'class': 'mud-card' }, [
				E('h3', {}, [ _('Dual SIM') ]),
				E('p', {}, [ _('Dual SIM control is only for the U30 Air.') ])
			]));
			return;
		}
		/* the last boot's cold start of the chosen SIM failed: the device is on SIM1 without live switching */
		/* (the reasons are identifiers of mu300-sim2-start and mu300-sim2-warmup; one it does not know is shown as it is) */
		var generic = _('The chosen SIM could not be started at the last boot (no card, PIN, or the modem refused it).');
		var fallbacks = {
			'cold-start-failed': generic,
			'no-sim': _('No card answered in the chosen SIM position at the last boot.'),
			'sim-pin': _('The chosen SIM is locked by its PIN.'),
			'no-data': _('The chosen SIM got no mobile data within the time at its first boot.'),
			'brokers-missing': _('The second SIM\'s modem channels did not come up at the last boot.'),
			'unconfirmed': _('The last dual-SIM boot never confirmed itself.')
		};
		var card = E('section', { 'class': 'mud-card' }, [
			E('h3', {}, [ _('Dual SIM') ]),
			E('p', {}, [ _('A live switch saves the target as the next boot default only after it gets mobile data. Without data, the previous SIM is restored and the saved boot preference is unchanged. Each SIM uses its own APN/PDP profile from the WAN settings.') ]),
			E('p', {}, [ _('Active SIM') + ': ' + (st.active ? 'SIM' + st.active : _('Unknown')) ]),
			E('p', {}, [ _('Cold-boot default') + ': ' + (st.default ? 'SIM' + st.default : _('Unknown')) ]),
			E('p', {}, [ results[st.result] || _('SIM control unavailable') ])
		]);
		if (st.fallback)
			card.appendChild(E('p', { 'style': 'color:#b45309;font-weight:600' }, [
				(fallbacks[st.fallback] || (generic + ' (' + st.fallback + ')')) + ' ' + _('The device is back on SIM1 without live switching; choose again below to try once more.') ]));
		/* a second SIM that never had data under Linux is on trial for its first boot (mu300-sim2-warmup) */
		if (st.boot_check === 'waiting')
			card.appendChild(E('p', { 'style': 'color:#b45309;font-weight:600' }, [
				_('Waiting for the first mobile data on SIM2: %d s left. Without data the device goes back to SIM1 and restarts.').format(st.boot_check_left || 0) ]));
		var hot = E('input', { 'type': 'checkbox', 'checked': st.hot ? '' : null, 'disabled': (!live || st.busy) ? '' : null });
		hot.addEventListener('change', function() { self.set('hot', hot.checked ? 1 : 0); });
		card.appendChild(E('label', {}, [ hot, ' ', _('Enable hot switching') ]));
		if (!live)
			card.appendChild(E('p', {}, [ _('This device supports next-boot SIM selection only; live switching is unavailable.') ]));
		else if (!st.available)
			card.appendChild(E('p', {}, [ _('Reboot after enabling hot switching to prepare both SIM channels.') ]));
		[ 1, 2 ].forEach(function(n) {
			var row = E('div', { 'style': 'display:flex;gap:8px;margin:12px 0' });
			var b = E('button', { 'class': 'mud-btn',
				'disabled': (!live || !st.hot || !st.available || st.busy || !st.active || st.active === n) ? '' : null },
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
