/* dash.js (D6): the dashboard's page script. The page is read hours, sometimes days, after it was
   built, so countdowns, clock labels and greying run on the READER's clock every minute from the data
   attributes the engine wrote (R-52, R-126). The staleness window comes from the data block
   (rules.stale_after_s), never from a constant here (R-51). Formats follow tests/fixtures/formats.json,
   the table the Python (E19) and Swift (S2) formatters pass too (R-112).
   Pure functions are tested under JavaScriptCore (tests/js); boot() wires the DOM only in a browser. */
var JDash = (function () {
  'use strict';
  var RESET_DUE_TEXT = 'Reset due, waiting for a fresh reading';
  var BAD_DATA = 'Data block unreadable \u2014 rebuild with jusage dashboard';
  var HIDE_KEY = 'jusage.hide', THEME_KEY = 'jusage.theme';
  var WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
  var ISO = /^\d{4}-\d\d-\d\dT\d\d:\d\d(:\d\d(\.\d+)?)?(Z|[+-]\d\d:\d\d)$/;

  function pad2(n) { return (n < 10 ? '0' : '') + n; }
  function logErr(msg, e) {
    var g = (0, eval)('this');
    if (g.clientErr) g.clientErr.report('dash', msg + (e ? ': ' + (e.message || e) : ''));
  }

  /* ISO 8601 with an offset -> ms. A time without an offset is an engine bug: refused. */
  function parseIso(s) {
    if (typeof s !== 'string' || !ISO.test(s)) throw new Error('not an ISO time with offset: ' + s);
    var t = Date.parse(s);
    if (isNaN(t)) throw new Error('not an ISO time: ' + s);
    return t;
  }

  /* 1.23B / 4.5M / 450k / 12. Halves round up; a value that rounds into the next unit uses it. */
  function fmtTokens(n) {
    n = Math.floor(n);
    if (n < 1000) return String(n);
    var k = Math.floor((n + 500) / 1000);
    if (k < 1000) return k + 'k';
    var tenths = Math.floor((n + 50000) / 100000);
    if (tenths < 10000) return Math.floor(tenths / 10) + '.' + (tenths % 10) + 'M';
    var h = Math.floor((n + 5000000) / 10000000);
    return Math.floor(h / 100) + '.' + pad2(h % 100) + 'B';
  }

  function commas(n) { return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ','); }

  /* >= $100 no cents with a thousands separator, else two decimals. */
  function fmtUsd(x) {
    var cents = Math.round(x * 100);
    if (cents < 10000) return '$' + (cents / 100).toFixed(2);
    return '$' + commas(Math.round(x));
  }

  /* Whole minutes, floored: '42m', '2h 05m', '5d 21h'. Zero or less is 'due'. */
  function fmtCountdown(seconds) {
    if (seconds <= 0) return 'due';
    var minutes = Math.floor(seconds / 60), hours = Math.floor(minutes / 60), mins = minutes % 60;
    if (hours === 0) return mins + 'm';
    if (hours < 24) return hours + 'h ' + pad2(mins) + 'm';
    return Math.floor(hours / 24) + 'd ' + (hours % 24) + 'h';
  }

  /* '14:05' / '2:05 PM' on the same calendar day as now, else 'Fri 14:05'. Days are counted in
     tzOffsetMin (minutes east of UTC): the reader's zone in the browser, fixed in the tests. */
  function fmtClock(atMs, nowMs, hour12, tzOffsetMin) {
    var shift = tzOffsetMin * 60000, a = new Date(atMs + shift), b = new Date(nowMs + shift);
    var h = a.getUTCHours(), m = a.getUTCMinutes();
    var body = hour12 ? (h % 12 || 12) + ':' + pad2(m) + ' ' + (h < 12 ? 'AM' : 'PM') : pad2(h) + ':' + pad2(m);
    var same = a.getUTCFullYear() === b.getUTCFullYear() && a.getUTCMonth() === b.getUTCMonth() && a.getUTCDate() === b.getUTCDate();
    return same ? body : WEEKDAYS[a.getUTCDay()] + ' ' + body;
  }

  /* Grey at stale_after_s and after: 899 s keeps its colour, 900 s is grey (R-197). */
  function freshness(readAt, nowMs, staleAfterS) {
    var ageS = Math.floor((nowMs - parseIso(readAt)) / 1000);
    return { stale: ageS >= staleAfterS, ageS: ageS };
  }

  function staleTitle(readAt, nowMs, hour12, tzOffsetMin) {
    var t = parseIso(readAt), ageS = Math.floor((nowMs - t) / 1000);
    var age = ageS < 3600 ? Math.floor(ageS / 60) + ' min' : fmtCountdown(ageS);
    return 'Updated ' + age + ' ago (' + fmtClock(t, nowMs, hour12, tzOffsetMin) + ').';
  }

  function each(root, sel, fn) {
    Array.prototype.forEach.call(root.querySelectorAll(sel), function (el) {
      try { fn(el); } catch (e) { logErr('tick ' + sel, e); }
    });
  }

  /* One pass over the page, in place: no element is rebuilt. */
  function tick(root, nowMs, rules, hour12, tzOffsetMin) {
    var staleAfter = rules.stale_after_s;
    each(root, '[data-read-at]', function (el) {
      var f = freshness(el.dataset.readAt, nowMs, staleAfter);
      el.classList.toggle('stale', f.stale);
      el.title = f.stale ? staleTitle(el.dataset.readAt, nowMs, hour12, tzOffsetMin) : '';
    });
    each(root, '.meter[data-reset]', function (el) {
      var t = parseIso(el.dataset.reset), left = (t - nowMs) / 1000, rt = el.querySelector('.rt');
      if (left <= 0) { el.classList.add('reset-due'); if (rt) rt.textContent = RESET_DUE_TEXT; return; }
      if (rt) rt.textContent = 'resets ' + fmtClock(t, nowMs, hour12, tzOffsetMin) + ' \u00b7 in ' + fmtCountdown(left);
    });
    each(root, '[data-runs-out]', function (el) {
      var pw = el.querySelector('.pw');
      if (pw) pw.textContent = el.dataset.words.replace('{runs_out}', fmtClock(parseIso(el.dataset.runsOut), nowMs, hour12, tzOffsetMin));
    });
    each(root, 'time[data-clock]', function (el) {
      el.textContent = fmtClock(parseIso(el.dataset.clock), nowMs, hour12, tzOffsetMin);
    });
  }

  /* The data block, checked at bring-up: unreadable JSON or no rules.stale_after_s refuses. */
  function readData(text) {
    var d = JSON.parse(text);
    if (!d || typeof d !== 'object' || !d.rules || typeof d.rules.stale_after_s !== 'number') {
      throw new Error('rules.stale_after_s missing');
    }
    return d;
  }

  /* The page's Quick Keys rows (the house table adds Quick Keys itself and the Edit rows). */
  function quickKeyRows(QK) {
    return [
      { id: 'customize', title: 'Customize', group: 'Page', kind: 'key', key: ',', mods: QK.CMD | QK.SHIFT },
      { id: 'closeDrawer', title: 'Close Customize', group: 'Page', kind: 'locked', key: 'escape', mods: 0 }
    ];
  }

  function store(fn, fallback) { try { return fn(); } catch (e) { logErr('localStorage', e); return fallback; } }

  function wire(doc, data) {
    var g = (0, eval)('this');
    var hour12 = store(function () { return new Intl.DateTimeFormat(undefined, { hour: 'numeric' }).resolvedOptions().hour12 === true; }, false);
    var cfg = doc.getElementById('cfg');

    // ---- hide (saved view, per browser)
    var hide = {};
    store(function () { JSON.parse(localStorage.getItem(HIDE_KEY) || '[]').forEach(function (k) { hide[k] = 1; }); });
    function applyHide() {
      Array.prototype.forEach.call(doc.querySelectorAll('[data-k]'), function (el) { el.hidden = !!hide[el.dataset.k]; });
      Array.prototype.forEach.call(doc.querySelectorAll('#cfg input[type=checkbox][value]'), function (i) { i.checked = !hide[i.value]; });
    }
    Array.prototype.forEach.call(doc.querySelectorAll('#cfg input[type=checkbox][value]'), function (i) {
      i.addEventListener('change', function () {
        if (i.checked) delete hide[i.value]; else hide[i.value] = 1;
        store(function () { localStorage.setItem(HIDE_KEY, JSON.stringify(Object.keys(hide))); });
        applyHide();
      });
    });

    // ---- themes (the head script already applied the saved one before the first paint)
    var box = doc.getElementById('themes');
    function applyTheme(id) {
      if (!id || id === 'system') doc.documentElement.removeAttribute('data-theme'); else doc.documentElement.setAttribute('data-theme', id);
      Array.prototype.forEach.call(box.querySelectorAll('.th'), function (b) { b.classList.toggle('on', b.dataset.theme === (id || 'system')); });
    }
    data.themes.forEach(function (t) {
      var b = doc.createElement('button'), sw = doc.createElement('i');
      b.type = 'button'; b.className = 'th'; b.dataset.theme = t.id;
      sw.style.background = t.bg.length > 1 ? 'linear-gradient(90deg,' + t.bg[0] + ' 50%,' + t.bg[1] + ' 50%)' : t.bg[0];
      sw.style.setProperty('--dot', t.dot);
      b.appendChild(sw); b.appendChild(doc.createTextNode(t.name));
      b.addEventListener('click', function () { applyTheme(t.id); store(function () { localStorage.setItem(THEME_KEY, t.id); }); });
      box.appendChild(b);
    });
    applyTheme(doc.documentElement.getAttribute('data-theme') || 'system');

    // ---- drawer
    function openDrawer() { cfg.hidden = false; }
    function closeDrawer() { cfg.hidden = true; }
    doc.getElementById('gear').addEventListener('click', openDrawer);
    doc.getElementById('cfg-x').addEventListener('click', closeDrawer);
    doc.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && !cfg.hidden && !(g.HouseQuickKeys && g.HouseQuickKeys.isOpen())) { e.preventDefault(); closeDrawer(); }
    });

    // ---- house modules
    g.HouseAccent.init();
    g.HouseAccent.mountPicker(doc.getElementById('accent-picker'));
    g.HouseQuickKeys.init({ rows: quickKeyRows(g.HouseQuickKeys) });
    g.HouseQuickKeys.on('customize', function () { if (cfg.hidden) openDrawer(); else closeDrawer(); });
    doc.getElementById('qk-open').addEventListener('click', g.HouseQuickKeys.open);
    g.HouseReport.mountButton(doc.getElementById('report-slot'));
    g.HouseReport.extra = function () { return ['Dashboard data as of ' + (data.generated || 'no reports')]; };

    // ---- chart tooltips, on hover and on keyboard focus
    Array.prototype.forEach.call(doc.querySelectorAll('.chart'), function (ch) {
      var svg = ch.querySelector('svg'), tip = ch.querySelector('.tip'), td = data.tips[svg.dataset.chart];
      Array.prototype.forEach.call(svg.querySelectorAll('.bar-hit'), function (bar) {
        bar.setAttribute('tabindex', '0');
        function show(x, y) {
          var day = bar.dataset.day, v = td.days[day] || {}, tot = 0, rows = [];
          td.series.forEach(function (s, i) {
            var n = v[s[0]] || 0; tot += n;
            if (n) rows.unshift('<div class="r"><span><i style="background:var(--s' + i + ')"></i>' + esc(s[1]) + '</span><span>' + fmtTokens(n) + '</span></div>');
          });
          var label = new Date(day + 'T12:00:00Z').toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric', timeZone: 'UTC' });
          tip.innerHTML = '<b>' + esc(label) + ' \u00b7 ' + fmtTokens(tot) + '</b>' + (rows.join('') || '<span>nothing</span>');
          tip.style.display = 'block';
          var r = ch.getBoundingClientRect(), left = x - r.left + 14, top = y - r.top + 14;
          if (left + tip.offsetWidth > r.width - 8) left -= tip.offsetWidth + 28;
          tip.style.left = left + 'px'; tip.style.top = top + 'px';
        }
        function hideTip() { tip.style.display = 'none'; }
        bar.addEventListener('mousemove', function (e) { show(e.clientX, e.clientY); });
        bar.addEventListener('focus', function () { var b = bar.getBoundingClientRect(); show(b.left + b.width / 2, b.top); });
        bar.addEventListener('mouseleave', hideTip);
        bar.addEventListener('blur', hideTip);
      });
    });

    applyHide();
    g.HouseTips.checkAtLoad();
    function run() { tick(doc, Date.now(), data.rules, hour12, -new Date().getTimezoneOffset()); }
    run();
    setInterval(run, 60000);
  }

  function esc(s) {
    return String(s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; });
  }

  /* Bring-up: a bad data block paints one red line and nothing else. */
  function boot(doc) {
    var data;
    try {
      var el = doc.getElementById('data');
      if (!el) throw new Error('no data block');
      data = readData(el.textContent);
    } catch (e) {
      doc.querySelector('main').innerHTML = '<p class="fatal">' + BAD_DATA + '</p>';
      logErr('data block', e);
      return false;
    }
    wire(doc, data);
    return true;
  }

  return {
    RESET_DUE_TEXT: RESET_DUE_TEXT, parseIso: parseIso, fmtTokens: fmtTokens, fmtUsd: fmtUsd,
    fmtCountdown: fmtCountdown, fmtClock: fmtClock, freshness: freshness, staleTitle: staleTitle,
    tick: tick, readData: readData, quickKeyRows: quickKeyRows, boot: boot
  };
})();
if (typeof document !== 'undefined' && document.getElementById) JDash.boot(document);
