/* accent.js: the user's accent colour for a web face. The web twin of Accent.swift (same maths, same default).
   Needs house-config.js (window.HOUSE) and glow.css (the --accent variables). Load after errlog.js.
     HouseAccent.init()                 apply the saved pick (or amber) to :root; call once at load
     HouseAccent.set([r,g,b])           pick a colour (saved)
     HouseAccent.reset()                back to amber (the saved pick is removed)
     HouseAccent.setHighContrast(bool)  brighter text, no glow (saved)
     HouseAccent.mountPicker(element)   draw the picker: preset swatches, Custom (colour wheel), Reset, High Contrast
   Stored in localStorage: <appId>.accentColor = [r,g,b] (sRGB 0-255), <appId>.highContrast = "1".
   A pick too dark to read on the base is shown brighter (WCAG 4.5:1); the pick itself is what is saved. */
(function () {
  'use strict';
  var AMBER = [255, 176, 32];            // house amber #ffb020 (Artemis)
  var BASE = [30, 30, 30];               // the dark surface the accent sits on; CHANGE if the page's is lighter
  var MIN_CONTRAST = 4.5;
  var PRESETS = [['Amber', AMBER], ['Tally Red', [255, 99, 82]], ['Lime', [166, 226, 46]], ['Mint', [60, 225, 150]],
                 ['Sky', [80, 170, 255]], ['Violet', [170, 130, 255]], ['Pink', [255, 105, 180]], ['White', [235, 235, 240]]];
  var cfg = window.HOUSE || {};
  var KEY = (cfg.appId || 'app') + '.accentColor', HC_KEY = (cfg.appId || 'app') + '.highContrast';
  var picked = AMBER, effective = AMBER, clamped = false, highContrast = false, listeners = [];

  function logErr(msg, e) {
    if (window.clientErr) window.clientErr.report('accent', msg + (e ? ': ' + (e.message || e) : ''));
    else if (window.console) console.error('[accent] ' + msg, e || '');
  }
  function clamp255(v) { v = Number(v); return Math.min(255, Math.max(0, v)); }
  function lum(c) {
    function lin(v) { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }
    return 0.2126 * lin(c[0]) + 0.7152 * lin(c[1]) + 0.0722 * lin(c[2]);
  }
  function contrast(a, b) { var x = lum(a), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); }
  function ink(c) { return contrast(c, [0, 0, 0]) >= contrast(c, [255, 255, 255]) ? [0, 0, 0] : [255, 255, 255]; }
  function toHSB(c) {
    var R = c[0] / 255, G = c[1] / 255, B = c[2] / 255, mx = Math.max(R, G, B), mn = Math.min(R, G, B), d = mx - mn, h = 0;
    if (d > 0) {
      if (mx === R) h = ((G - B) / d) % 6; else if (mx === G) h = (B - R) / d + 2; else h = (R - G) / d + 4;
      h /= 6; if (h < 0) h += 1;
    }
    return [h, mx === 0 ? 0 : d / mx, mx];
  }
  function fromHSB(h, s, v) {
    var i = Math.floor(h * 6), f = h * 6 - i, p = v * (1 - s), q = v * (1 - f * s), t = v * (1 - (1 - f) * s), o;
    switch (i % 6) { case 0: o = [v, t, p]; break; case 1: o = [q, v, p]; break; case 2: o = [p, v, t]; break;
                     case 3: o = [p, q, v]; break; case 4: o = [t, p, v]; break; default: o = [v, p, q]; }
    return [o[0] * 255, o[1] * 255, o[2] * 255];
  }
  /* The colour as shown: unchanged when it reads on the base, else brighter first, then paler. */
  function readable(c) {
    if (contrast(c, BASE) >= MIN_CONTRAST) return { accent: c, clamped: false };
    var hsb = toHSB(c), h = hsb[0], s = hsb[1], v = hsb[2], lo, hi, m, k;
    function ok(a) { return contrast(a, BASE) >= MIN_CONTRAST; }
    if (ok(fromHSB(h, s, 1))) {
      lo = v; hi = 1;
      for (k = 0; k < 24; k++) { m = (lo + hi) / 2; if (ok(fromHSB(h, s, m))) hi = m; else lo = m; }
      v = hi;
    } else {
      v = 1; lo = 0; hi = s;
      for (k = 0; k < 24; k++) { m = (lo + hi) / 2; if (ok(fromHSB(h, m, 1))) lo = m; else hi = m; }
      s = lo;
    }
    return { accent: fromHSB(h, s, v), clamped: true };
  }
  function hex(c) { return '#' + c.map(function (v) { return ('0' + Math.round(v).toString(16)).slice(-2); }).join(''); }
  function fromHex(s) {
    var m = /^#?([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(String(s));
    return m ? [parseInt(m[1], 16), parseInt(m[2], 16), parseInt(m[3], 16)] : null;
  }
  function same(a, b) { return a[0] === b[0] && a[1] === b[1] && a[2] === b[2]; }

  function paint() {
    var r = readable(picked); effective = r.accent; clamped = r.clamped;
    var st = document.documentElement.style, i = ink(effective);
    st.setProperty('--accent', hex(effective));
    st.setProperty('--accent-rgb', effective.map(Math.round).join(','));
    st.setProperty('--accent-ink', hex(i));
    document.documentElement.classList.toggle('high-contrast', highContrast);
    listeners.forEach(function (fn) { try { fn(api.state()); } catch (e) { logErr('listener failed', e); } });
  }
  function load() {
    try {
      var raw = localStorage.getItem(KEY);
      if (raw !== null) {
        var a = JSON.parse(raw);
        if (Array.isArray(a) && a.length === 3 && a.every(function (v) { return typeof v === 'number' && isFinite(v); })) picked = a.map(clamp255);
        else logErr('stored ' + KEY + ' unreadable, using amber');
      }
      highContrast = localStorage.getItem(HC_KEY) === '1';
    } catch (e) { logErr('could not read the saved accent', e); }
  }
  function save() {
    try {
      if (same(picked, AMBER)) localStorage.removeItem(KEY); else localStorage.setItem(KEY, JSON.stringify(picked));
      if (highContrast) localStorage.setItem(HC_KEY, '1'); else localStorage.removeItem(HC_KEY);
    } catch (e) { logErr('could not save the accent', e); }
  }

  var api = window.HouseAccent = {
    AMBER: AMBER, PRESETS: PRESETS, MIN_CONTRAST: MIN_CONTRAST,
    contrast: contrast, readable: readable, ink: ink, hex: hex, fromHex: fromHex,
    state: function () { return { picked: picked.slice(), effective: effective.slice(), clamped: clamped, highContrast: highContrast }; },
    init: function () { load(); paint(); return api.state(); },
    set: function (c) { picked = [clamp255(c[0]), clamp255(c[1]), clamp255(c[2])]; save(); paint(); },
    reset: function () { picked = AMBER; save(); paint(); },
    setHighContrast: function (on) { highContrast = !!on; save(); paint(); },
    onChange: function (fn) { listeners.push(fn); },
    mountPicker: function (el) {
      el.classList.add('hk-accent');
      function draw() {
        var h = '';
        PRESETS.forEach(function (p, i) {
          h += '<button type="button" class="hk-swatch' + (same(p[1], picked) ? ' on' : '') + '" data-i="' + i + '" title="' + p[0] +
               '" aria-label="' + p[0] + '" aria-pressed="' + same(p[1], picked) + '" style="background:' + hex(p[1]) + '"></button>';
        });
        h += '<label class="hk-btn hk-custom">Custom<input type="color" value="' + hex(picked) + '" aria-label="Custom accent colour"></label>' +
             '<button type="button" class="hk-btn hk-reset"' + (same(picked, AMBER) ? ' disabled' : '') + '>Reset</button>' +
             '<label class="hk-hc"><input type="checkbox"' + (highContrast ? ' checked' : '') + '> High Contrast</label>' +
             '<div class="hk-clamp">' + (clamped ? 'Too dark to read here. Shown brighter.' : '') + '</div>';
        el.innerHTML = h;
        el.querySelectorAll('.hk-swatch').forEach(function (b) {
          b.addEventListener('click', function () { api.set(PRESETS[+b.dataset.i][1]); });
        });
        el.querySelector('input[type=color]').addEventListener('input', function (e) {
          var c = fromHex(e.target.value); if (c) { picked = c; save(); paintQuiet(); }
        });
        el.querySelector('input[type=color]').addEventListener('change', function () { draw(); });
        el.querySelector('.hk-reset').addEventListener('click', function () { api.reset(); });
        el.querySelector('.hk-hc input').addEventListener('change', function (e) { api.setHighContrast(e.target.checked); });
      }
      var quiet = false;
      /* While the wheel drags the picker is NOT redrawn (redrawing would close the native colour popup). */
      function paintQuiet() { quiet = true; paint(); quiet = false; }
      listeners.push(function () { if (!quiet) draw(); });
      draw();
    }
  };
})();
