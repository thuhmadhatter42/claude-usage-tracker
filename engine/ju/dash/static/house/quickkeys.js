/* quickkeys.js: QUICK KEYS (⇧⌃K) for a web face. Every shortcut the page has, in one table, listed in one
   window, each one rebindable by clicking it and pressing the new keys. The web twin of QuickKeys.swift;
   generalised from Artemis (faceplate-quickkeys.js + the table logic in shell.m).
   Needs house-config.js, glow.css, quickkeys.css. Load after errlog.js.

     HouseQuickKeys.init({ rows: [...], reserved: fn })     once at load
     HouseQuickKeys.on('export', function (e) { ... })      run this when the row's chord is pressed
     HouseQuickKeys.matches(event, 'export')                or test an event yourself
     HouseQuickKeys.display('export')                       "⇧⌘E", for a label or tip (never typed twice)
     HouseQuickKeys.open() / .close()                       the window (⇧⌃K opens it by itself)

   A row: { id, title, group, kind, key, mods }
     kind  'key'     rebindable; needs at least one modifier
           'locked'  belongs to the browser or macOS: shown, never changed, never taken
     key   lower case: a letter or digit by its physical cap ('k', '3'), or delete, return, tab, space, escape,
           left, right, up, down, or the character the layout types (',')
     mods  HouseQuickKeys.SHIFT | CTRL | OPT | CMD
   Rules (the same as native): ONE table; a chord the OS or browser holds cannot be assigned; a chord another
   row uses is a clash (Cancel / Reassign, the other row is left unassigned); locked rows never move.
   A page cannot read macOS's own shortcuts, so `reserved` is a list: RESERVED below (what Safari, Chrome and
   macOS take before a page sees the key), plus whatever a native shell hands in through `reserved: fn`.
   Stored in localStorage: <appId>.quickKeys = { id: {key, mods} }. */
(function () {
  'use strict';
  var SHIFT = 1 << 17, CTRL = 1 << 18, OPT = 1 << 19, CMD = 1 << 20, MASK = SHIFT | CTRL | OPT | CMD;
  var UNASSIGNED = 'Unassigned';
  var cfg = window.HOUSE || {};
  var KEY = (cfg.appId || 'app') + '.quickKeys';
  /* Chords a page never receives, or must never take: the browser's and macOS's own. */
  var RESERVED = [
    ['q', CMD], ['w', CMD], ['t', CMD], ['n', CMD], ['n', CMD | SHIFT], ['t', CMD | SHIFT], ['r', CMD], ['l', CMD],
    ['h', CMD], ['h', CMD | OPT], ['m', CMD], ['tab', CMD], ['space', CMD], ['space', CMD | OPT], ['space', CTRL],
    ['3', CMD | SHIFT], ['4', CMD | SHIFT], ['5', CMD | SHIFT], ['escape', CMD | OPT], ['q', CMD | CTRL], ['f', CMD | CTRL],
    ['c', CMD], ['v', CMD], ['x', CMD], ['a', CMD], ['z', CMD], ['z', CMD | SHIFT], [',', CMD], ['i', CMD | OPT], ['j', CMD | OPT]
  ].map(function (c) { return { key: c[0], mods: c[1] }; });
  var STANDARD = [
    { id: 'quickKeys', title: 'Quick Keys', group: 'App', kind: 'key', key: 'k', mods: CTRL | SHIFT },
    { id: 'undo', title: 'Undo', group: 'Edit', kind: 'locked', key: 'z', mods: CMD },
    { id: 'redo', title: 'Redo', group: 'Edit', kind: 'locked', key: 'z', mods: CMD | SHIFT },
    { id: 'cut', title: 'Cut', group: 'Edit', kind: 'locked', key: 'x', mods: CMD },
    { id: 'copy', title: 'Copy', group: 'Edit', kind: 'locked', key: 'c', mods: CMD },
    { id: 'paste', title: 'Paste', group: 'Edit', kind: 'locked', key: 'v', mods: CMD },
    { id: 'selectAll', title: 'Select All', group: 'Edit', kind: 'locked', key: 'a', mods: CMD }
  ];
  var rows = [], overrides = {}, handlers = {}, extraReserved = function () { return []; }, storage = null;
  var ui = { el: null, listen: null, clash: null, msg: '', scroll: 0 };

  function logErr(msg, e) {
    if (window.clientErr) window.clientErr.report('quickkeys', msg + (e ? ': ' + (e.message || e) : ''));
    else if (window.console) console.error('[quickkeys] ' + msg, e || '');
  }
  function esc(s) { return String(s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); }
  function eventMods(e) { return (e.shiftKey ? SHIFT : 0) | (e.ctrlKey ? CTRL : 0) | (e.altKey ? OPT : 0) | (e.metaKey ? CMD : 0); }
  /* The key by its PHYSICAL name where it has one: with Option held a letter key types another character. */
  function eventKey(e) {
    var c = e.code || '';
    if (/^Key[A-Z]$/.test(c)) return c.slice(3).toLowerCase();
    if (/^Digit[0-9]$/.test(c)) return c.slice(5);
    var k = String(e.key || '').toLowerCase();
    if (k === 'backspace' || k === 'delete') return 'delete';
    if (k === ' ') return 'space';
    if (k === 'enter') return 'return';
    if (k === 'arrowleft') return 'left';
    if (k === 'arrowright') return 'right';
    if (k === 'arrowup') return 'up';
    if (k === 'arrowdown') return 'down';
    return k;
  }
  function isModifierKey(e) { return e.key === 'Shift' || e.key === 'Control' || e.key === 'Alt' || e.key === 'Meta'; }
  function typingTarget(t) { return !!t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable); }
  function display(key, mods) {
    if (!key) return UNASSIGNED;
    var d = '';
    if (mods & CTRL) d += '⌃';
    if (mods & OPT) d += '⌥';
    if (mods & SHIFT) d += '⇧';
    if (mods & CMD) d += '⌘';
    var names = { 'delete': '⌫', 'return': '↩', 'tab': '⇥', 'space': 'Space', 'escape': '⎋', 'left': '←', 'right': '→', 'up': '↑', 'down': '↓' };
    return d + (names[key] || key.toUpperCase());
  }
  function def(id) { for (var i = 0; i < rows.length; i++) if (rows[i].id === id) return rows[i]; return null; }
  function chord(id) {
    var d = def(id); if (!d) return { key: '', mods: 0 };
    var o = d.kind !== 'locked' ? overrides[id] : null;
    return o ? { key: o.key, mods: o.mods } : { key: d.key, mods: d.mods };
  }
  function same(a, b) { return a.key === b.key && a.mods === b.mods; }
  function isReserved(c) {
    var extra = [];
    try { extra = extraReserved() || []; } catch (e) { logErr('reserved() failed', e); }
    return RESERVED.concat(extra).some(function (r) { return same(r, c); });
  }
  function save() {
    try {
      if (Object.keys(overrides).length) storage.setItem(KEY, JSON.stringify(overrides)); else storage.removeItem(KEY);
    } catch (e) { logErr('could not save the shortcuts', e); }
  }
  function load() {
    overrides = {};
    try {
      var raw = storage.getItem(KEY); if (raw === null) return;
      var o = JSON.parse(raw);
      if (!o || typeof o !== 'object' || Array.isArray(o)) { logErr('stored ' + KEY + ' unreadable, using the defaults'); return; }
      Object.keys(o).forEach(function (id) {
        var e = o[id];
        if (!def(id)) return;                                   // a row another build had
        if (!e || typeof e.key !== 'string' || typeof e.mods !== 'number') { logErr('stored shortcut for ' + id + ' unreadable, using its default'); return; }
        overrides[id] = { key: e.key.toLowerCase(), mods: e.mods & MASK };
      });
    } catch (e) { logErr('stored ' + KEY + ' unreadable, using the defaults', e); }
  }
  /* Defaults that collide, or rebindable rows with no modifier. Logged at init; the test page fails on any. */
  function problems(list) {
    var out = [], by = {};
    list.forEach(function (d) {
      if (!d.key) return;
      var k = d.key + '|' + d.mods;
      if (by[k]) out.push(d.id + ' and ' + by[k] + ' both default to ' + display(d.key, d.mods)); else by[k] = d.id;
      if (d.kind !== 'locked' && !d.mods) out.push(d.id + ': a shortcut needs a modifier key');
    });
    return out;
  }
  /* Rebind. Returns {ok:true} | {ok:false, reason} | {ok:false, reason, clash:{id,title}}. */
  function set(id, key, mods, reassign) {
    var d = def(id);
    if (!d) return { ok: false, reason: 'There is no such shortcut.' };
    if (d.kind === 'locked') return { ok: false, reason: 'That shortcut belongs to macOS and cannot be changed.' };
    key = String(key || '').toLowerCase(); mods = mods & MASK;
    if (!key) return { ok: false, reason: 'Press a key.' };
    var c = { key: key, mods: mods };
    if (isReserved(c)) return { ok: false, reason: display(key, mods) + ' belongs to macOS or the browser and cannot be assigned.' };
    if (!mods) return { ok: false, reason: 'A shortcut needs a modifier key.' };
    var next = {}; Object.keys(overrides).forEach(function (k) { next[k] = overrides[k]; });
    for (var i = 0; i < rows.length; i++) {
      var o = rows[i];
      if (o.id === id || !same(chord(o.id), c)) continue;
      if (o.kind === 'locked') return { ok: false, reason: o.title + ' is a macOS shortcut and cannot be reassigned.', clash: { id: o.id, title: o.title, locked: true } };
      if (!reassign) return { ok: false, reason: o.title + ' already uses that shortcut.', clash: { id: o.id, title: o.title } };
      next[o.id] = { key: '', mods: 0 };                        // the other action is left unassigned
    }
    next[id] = c; overrides = next; save(); changed();
    return { ok: true };
  }
  function clear(id) { var d = def(id); if (!d || d.kind === 'locked') return; overrides[id] = { key: '', mods: 0 }; save(); changed(); }
  function resetAll() { overrides = {}; save(); ui.msg = ''; ui.clash = null; changed(); }
  var listeners = [];
  function changed() { listeners.forEach(function (fn) { try { fn(); } catch (e) { logErr('listener failed', e); } }); if (isOpen()) render(); }
  function matches(e, id) { var c = chord(id); return !!c.key && c.mods !== 0 && eventKey(e) === c.key && eventMods(e) === c.mods; }

  /* ---- the window ---- */
  function isOpen() { return !!ui.el && ui.el.classList.contains('show'); }
  function ensureEl() {
    if (ui.el) return;
    ui.el = document.createElement('div');
    ui.el.className = 'hk-qk-overlay';
    ui.el.innerHTML = '<div class="hk-qk-panel" role="dialog" aria-modal="true" aria-label="Quick Keys"></div>';
    ui.el.addEventListener('click', function (e) { if (e.target === ui.el) close(); });
    document.body.appendChild(ui.el);
  }
  function open() { ensureEl(); ui.listen = null; ui.clash = null; ui.msg = ''; ui.el.classList.add('show'); render(); }
  function close() { if (!ui.el) return; ui.listen = null; ui.clash = null; ui.msg = ''; ui.el.classList.remove('show'); }
  function render() {
    var panel = ui.el.firstChild, old = panel.querySelector('.hk-qk-list'); if (old) ui.scroll = old.scrollTop;
    var h = '<div class="hk-qk-head"><div class="hk-qk-title glow-text">QUICK KEYS</div><button type="button" class="hk-qk-x" aria-label="Close">&#10005;</button></div>';
    if (ui.clash) {
      h += '<div class="hk-qk-clash"><div class="hk-qk-clash-msg">' + esc(ui.clash.reason) + '</div>' +
           '<button type="button" class="hk-btn" data-act="cancel">Cancel</button><button type="button" class="hk-btn primary" data-act="reassign">Reassign</button></div>';
    } else if (ui.msg) {
      h += '<div class="hk-qk-clash"><div class="hk-qk-clash-msg">' + esc(ui.msg) + '</div></div>';
    }
    h += '<div class="hk-qk-list">';
    var group = null;
    rows.forEach(function (r) {
      if (r.group !== group) { group = r.group; h += '<div class="hk-qk-group">' + esc(group) + '</div>'; }
      var c = chord(r.id), listening = ui.listen === r.id, locked = r.kind === 'locked', none = !c.key;
      h += '<div class="hk-qk-row" data-id="' + esc(r.id) + '"><div class="hk-qk-name">' + esc(r.title) + '</div>' +
           '<button type="button" class="hk-qk-chip' + (listening ? ' listening' : '') + (locked ? ' locked' : '') + (none ? ' none' : '') + '"' +
           (locked ? ' disabled title="This shortcut belongs to macOS"' : ' title="Click, then press the new keys"') + '>' +
           esc(listening ? 'Press the new keys…' : display(c.key, c.mods)) + '</button>' +
           '<button type="button" class="hk-qk-clear' + (locked || none ? ' gone' : '') + '" title="Leave this action without a shortcut" aria-label="Clear ' + esc(r.title) + ' shortcut">&#10005;</button></div>';
    });
    h += '</div><div class="hk-qk-foot"><button type="button" class="hk-btn" data-act="reset">Reset All</button><span class="hk-qk-sp"></span>' +
         '<button type="button" class="hk-btn primary" data-act="done">Done</button></div>';
    panel.innerHTML = h;
    var list = panel.querySelector('.hk-qk-list'); if (list) list.scrollTop = ui.scroll;
    panel.querySelector('.hk-qk-x').addEventListener('click', close);
    panel.querySelectorAll('[data-act]').forEach(function (b) {
      b.addEventListener('click', function () {
        var act = b.dataset.act;
        if (act === 'done') close();
        else if (act === 'reset') resetAll();
        else if (act === 'cancel') { ui.clash = null; render(); }
        else if (act === 'reassign') { var p = ui.clash; ui.clash = null; answer(p.id, set(p.id, p.key, p.mods, true), p.key, p.mods); }
      });
    });
    panel.querySelectorAll('.hk-qk-row').forEach(function (rowEl) {
      var id = rowEl.dataset.id;
      rowEl.querySelector('.hk-qk-chip').addEventListener('click', function () {
        var d = def(id); if (!d || d.kind === 'locked') return;
        ui.listen = ui.listen === id ? null : id; ui.clash = null; ui.msg = ''; render();
      });
      rowEl.querySelector('.hk-qk-clear').addEventListener('click', function () { clear(id); });
    });
  }
  function answer(id, res, key, mods) {
    if (res.ok) { ui.msg = ''; ui.clash = null; }
    else if (res.clash && !res.clash.locked) { ui.clash = { id: id, key: key, mods: mods, reason: res.reason }; ui.msg = ''; }
    else { ui.msg = res.reason; ui.clash = null; }
    if (isOpen()) render();
  }
  /* A chord arrives for the listening row (the key handler calls it; the test page calls it directly). */
  function offer(id, key, mods) { ui.listen = null; var res = set(id, key, mods, false); answer(id, res, key, mods); return res; }

  /* ONE key handler, capture phase: while a row listens it swallows every key; else it runs the rows' handlers. */
  function onKeyDown(e) {
    if (isOpen() && ui.listen) {
      e.preventDefault(); e.stopPropagation();
      if (isModifierKey(e)) return;
      if (eventMods(e) === 0 && e.key === 'Escape') { ui.listen = null; render(); return; }
      offer(ui.listen, eventKey(e), eventMods(e));
      return;
    }
    if (isOpen()) { if (e.key === 'Escape') { e.preventDefault(); close(); } return; }
    if (matches(e, 'quickKeys')) { e.preventDefault(); open(); return; }
    if (e.defaultPrevented || e.repeat) return;
    for (var id in handlers) {
      if (!matches(e, id)) continue;
      /* a bare or Shift-only chord is ordinary typing inside a text field */
      if (typingTarget(e.target) && !(eventMods(e) & (CMD | CTRL | OPT))) return;
      e.preventDefault();
      try { handlers[id](e); } catch (err) { logErr('handler for ' + id + ' failed', err); }
      return;
    }
  }

  window.HouseQuickKeys = {
    SHIFT: SHIFT, CTRL: CTRL, OPT: OPT, CMD: CMD, RESERVED: RESERVED, STANDARD: STANDARD, UNASSIGNED: UNASSIGNED,
    init: function (opts) {
      opts = opts || {};
      storage = opts.storage || window.localStorage;
      extraReserved = opts.reserved || function () { return []; };
      var seen = {};
      rows = (opts.rows || []).concat(STANDARD).filter(function (r) {
        if (seen[r.id]) { logErr("row id '" + r.id + "' is in the table twice; the second is ignored"); return false; }
        seen[r.id] = 1; return true;
      });
      problems(rows).forEach(function (p) { logErr('table: ' + p); });
      load();
      if (!opts.noKeys) document.addEventListener('keydown', onKeyDown, true);
      return rows.length;
    },
    rows: function () { return rows.map(function (r) { var c = chord(r.id); return { id: r.id, title: r.title, group: r.group, kind: r.kind, key: c.key, mods: c.mods, display: display(c.key, c.mods), locked: r.kind === 'locked', custom: r.kind !== 'locked' && !!overrides[r.id] }; }); },
    problems: function (list) { return problems(list || rows); },
    display: function (id) { var c = chord(id); return display(c.key, c.mods); },
    displayChord: display,
    chord: chord, set: set, clear: clear, resetAll: resetAll, offer: offer, matches: matches,
    eventKey: eventKey, eventMods: eventMods,
    on: function (id, fn) { if (!def(id)) logErr("on('" + id + "'): no such row"); handlers[id] = fn; },
    onChange: function (fn) { listeners.push(fn); },
    open: open, close: close, isOpen: isOpen,
    listen: function (id) { var d = def(id); if (!d || d.kind === 'locked') return false; ui.listen = id; ui.clash = null; ui.msg = ''; if (isOpen()) render(); return true; },
    state: function () { return { listening: ui.listen, clash: ui.clash, message: ui.msg }; }
  };
})();
