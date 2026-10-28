'use strict';
'require view';
'require dom';
'require mu300.common as M';

/* Every value from the backend goes into E() inside an array: LuCI's E() takes a lone string as HTML, an array's
 * strings as text. */
/* System > LEDs: mu300-led's settings (kanoqwq's LED settings page, rebuilt over mu300-led instead of LuCI's own LED
 * page): each lamp on or off and, where it dims, its brightness; how long the LEDs stay lit; the boot indicator; how
 * the heat alarm shows. The backend is mu300dash led_get/led_set (unisoc-modem/led); the values are checked there and
 * by mu300-led, which shows the LEDs by them at once. A refused one comes back as ok != 1 and the page reloads. */
return view.extend({
	load: function() { return L.resolveDefault(M.callLedGet(), {}); },

	render: function(state) {
		M.injectCss();
		this.root = E('div', { 'class': 'mud' });
		this.paint(state || {});
		return this.root;
	},

	reload: function() {
		var self = this;
		return L.resolveDefault(M.callLedGet(), {}).then(function(st) { self.paint(st || {}); return st; });
	},

	/* led_set KEY VALUE; on failure the error is shown, and the page reloads to the real values either way */
	save: function(key, value) {
		var self = this;
		return M.callLedSet(key, String(value)).then(function(r) {
			if (!r || r.ok !== 1)
				M.toast(_('Failed: %s').format(M.errText(r)), { type: 'error', timeout: 6000 });
			else
				M.toast(_('Saved'), { type: 'success' });
			return self.reload();
		}, function() {
			M.toast(_('Management connection lost'), { type: 'error', timeout: 6000 });
			return self.reload();
		});
	},

	/* a select whose change is saved as KEY */
	choice: function(key, current, options, id) {
		var self = this;
		var sel = E('select', { 'class': 'cbi-input-select', 'id': id }, options.map(function(o) {
			return E('option', { 'value': o[0] }, [ o[1] ]);
		}));
		sel.value = String(current);
		sel.addEventListener('change', function() { self.save(key, sel.value); });
		return sel;
	},

	/* a whole number min..max saved as KEY; one outside the range is refused here and the page reloads */
	number: function(key, current, min, max, id) {
		var self = this;
		var inp = E('input', { 'type': 'number', 'class': 'cbi-input-text', 'min': String(min), 'max': String(max),
			'step': '1', 'style': 'width:6em', 'id': id, 'value': current == null ? '' : String(current) });
		inp.addEventListener('change', function() {
			var v = inp.value.trim();
			if (!/^[0-9]+$/.test(v) || +v < min || +v > max) {
				M.toast(_('Enter a whole number from 0 to %d').format(max), { type: 'error', timeout: 6000 });
				return self.reload();
			}
			self.save(key, String(+v));
		});
		return inp;
	},

	line: function(label, id, parts) {
		return E('div', { 'style': 'display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:10px 0' },
			[ E('label', { 'for': id, 'style': 'min-width:12em' }, [ label ]) ].concat(parts));
	},

	paint: function(st) {
		var self = this;
		/* no settings in the reply: the backend did not answer; do not paint defaults as if they were real */
		if (!st || st.ok !== 1 || !Array.isArray(st.lamps)) {
			dom.content(this.root, E('section', { 'class': 'mud-card', 'id': 'mud-leds-unavailable' }, [
				E('h3', {}, _('LEDs')),
				E('div', { 'class': 'mud-note' }, [ M.errText(st) ])
			]));
			return;
		}
		var names = { network: _('Network lamp'), wifi: _('Wi-Fi lamp'), battery: _('Battery lamp') };
		var onoff = [ [ '1', _('On') ], [ '0', _('Off') ] ];
		var dims = st.dimmable || [];

		var lamps = E('section', { 'class': 'mud-card', 'id': 'mud-leds-lamps' }, [ E('h3', {}, _('LEDs')) ]);
		st.lamps.forEach(function(l) {
			if (!names[l]) return;
			var parts = [ self.choice(l, st[l] ? '1' : '0', onoff, 'mud-leds-' + l) ];
			if (dims.indexOf(l) >= 0)
				parts.push(E('span', {}, [ _('Brightness (%)') ]),
					self.number(l + '_brightness', st[l + '_brightness'], 1, 100, 'mud-leds-' + l + '-brightness'));
			lamps.appendChild(self.line(names[l], 'mud-leds-' + l, parts));
		});
		lamps.appendChild(E('div', { 'class': 'mud-note' }, _('A lamp switched off stays dark; the heat alarm still shows on its LED.')));

		/* the timeout: empty or "Device default" is the device's own (60 s on the U30 Air, 0 elsewhere) */
		var t = self.number('timeout', st.timeout_default ? null : st.timeout, 0, 86400, 'mud-leds-timeout');
		t.setAttribute('placeholder', String(st.default_timeout));
		var def = E('button', { 'class': 'mud-btn', 'id': 'mud-leds-timeout-default', 'disabled': st.timeout_default ? '' : null },
			[ _('Device default') ]);
		def.addEventListener('click', function() { self.save('timeout', 'default'); });
		lamps.appendChild(self.line(_('LED timeout (seconds)'), 'mud-leds-timeout', [ t, def ]));
		lamps.appendChild(E('div', { 'class': 'mud-note' },
			_('How long the LEDs stay lit after a key press or a change; 0 keeps them on. On a charger they stay lit.')));

		var more = E('section', { 'class': 'mud-card', 'style': 'margin-top:14px', 'id': 'mud-leds-more' }, [
			self.line(_('Boot indicator'), 'mud-leds-boot', [ self.choice('boot', st.boot ? '1' : '0', onoff, 'mud-leds-boot') ]),
			E('div', { 'class': 'mud-note' }, _('The LED shows its colours in turn while the system starts')),
			self.line(_('Heat alarm'), 'mud-leds-thermal', [ self.choice('thermal', st.thermal || 'siren', [
				[ 'siren', _('Siren') ], [ 'blink', _('Red blink') ], [ 'off', _('Off') ] ], 'mud-leds-thermal') ])
		]);
		dom.content(this.root, [ lamps, more ]);
	}
});
