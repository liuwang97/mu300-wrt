'use strict';
'require view';
'require dom';
'require mu300.common as M';

/* Every value from the backend goes into E() inside an array: LuCI's E() takes a lone string as HTML, an array's
 * strings as text. */
/* Cellular > SIM settings: per card, VoLTE and VoNR (mobile-data voice, docs/FINDINGS.md 181; after kanoqwq's
 * per-SIM voice switches). The backend is mu300dash simcard_get/simcard_set. A choice for the card in use is sent at
 * once and read back (a modem that does not take it saves nothing); one for the other card waits for its next start.
 * Every bring-up of a card sends its choice again: VoLTE is on unless switched off, VoNR is the modem's own until set. */
return view.extend({
	load: function() { return L.resolveDefault(M.callSimcardGet(), {}); },

	render: function(st) {
		M.injectCss();
		this.root = E('div', { 'class': 'mud' });
		this.paint(st || {});
		return this.root;
	},

	reload: function() {
		var self = this;
		return L.resolveDefault(M.callSimcardGet(), {}).then(function(st) { self.paint(st || {}); return st; });
	},

	set: function(btn, kind, slot, state) {
		var self = this;
		M.busy(btn, true);
		return M.callSimcardSet(kind, String(slot), state).then(function(r) {
			M.busy(btn, false);
			if (!r || r.ok !== 1) {
				M.toast(_('Failed: %s').format(M.errText(r)), { type: 'error', timeout: 6000 });
				return;
			}
			M.toast(_('Saved'), { type: 'success' });
			return self.reload();
		}, function() {
			M.busy(btn, false);
			M.toast(_('Management connection lost'), { type: 'error', timeout: 6000 });
		});
	},

	paint: function(st) {
		var self = this;
		if (st.ok !== 1 || !st.voice || !st.voice.slots) {
			dom.content(this.root, E('section', { 'class': 'mud-card' }, [
				E('h3', {}, [ _('SIM settings') ]),
				E('p', {}, [ st.error ? M.errText(st) : _('SIM settings are not available') ])
			]));
			return;
		}
		var v = st.voice, live = v.live || null;
		var word = function(b) { return b === true ? _('On') : b === false ? _('Off') : _('Modem default'); };
		var cards = [];
		[ 0, 1 ].forEach(function(slot) {
			if (slot === 1 && st.slot1_supported !== 1 && v.slot !== 1) return;
			var saved = v.slots[slot] || {}, inUse = v.slot === slot;
			var card = E('section', { 'class': 'mud-card', 'id': 'mud-simcard-' + slot }, [
				E('h3', {}, [ _('SIM%d').format(slot + 1) + (inUse ? ' (' + _('in use') + ')' : '') ])
			]);
			[ [ 'volte', _('VoLTE') ], [ 'vonr', _('VoNR') ] ].forEach(function(k) {
				var now = saved[k[0]];
				var text = _('Saved: %s').format(word(now));
				if (inUse && live && (live[k[0]] === true || live[k[0]] === false))
					text += ' · ' + _('Modem: %s').format(word(live[k[0]]));
				var on = E('button', { 'class': 'mud-btn' + (now === true ? ' on' : ''), 'id': 'mud-' + k[0] + '-' + slot + '-on' }, [ _('On') ]);
				var off = E('button', { 'class': 'mud-btn' + (now === false ? ' on' : ''), 'id': 'mud-' + k[0] + '-' + slot + '-off' }, [ _('Off') ]);
				on.addEventListener('click', function() { self.set(on, k[0], slot, 'on'); });
				off.addEventListener('click', function() { self.set(off, k[0], slot, 'off'); });
				card.appendChild(E('div', { 'class': 'mud-r' }, [
					E('span', { 'class': 'mud-k' }, [ k[1] ]),
					E('span', { 'class': 'mud-v' }, [ text ])
				]));
				card.appendChild(E('div', { 'style': 'display:flex;gap:8px;margin:6px 0 12px' }, [ on, off ]));
			});
			if (!inUse)
				card.appendChild(E('div', { 'class': 'mud-note' }, [ _('Sent to this SIM when its radio next starts.') ]));
			cards.push(card);
		});
		dom.content(this.root, [
			E('section', { 'class': 'mud-card' }, [
				E('h3', {}, [ _('SIM settings') ]),
				E('div', { 'class': 'mud-note' }, [ _('VoLTE and VoNR for each SIM, kept across reboots and sent again whenever that SIM starts. VoLTE is on unless switched off; VoNR stays as the modem has it until set.') ])
			])
		].concat(cards));
	}
});
