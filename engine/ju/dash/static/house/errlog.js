/* errlog.js: the client error log (house-kit 0.0.2, pasted; jusage adds the localStorage ring). Load it FIRST on every page, right after house-config.js.
   Lifted from artist-sites (web/js/errlog.js). House rule: everything we build logs its errors.
   Catches window errors, unhandled promise rejections, Content-Security-Policy refusals, and every failed
   fetch / XMLHttpRequest the page makes. Each becomes one small record:
     kind, message, source file:line, page path (no query), step, short UA, app version.
   Never form values: no request bodies, URLs with queries, or response text are read. At most 8 per page load.
   The record is kept in memory (clientErr.recent(), which the Report module attaches) and, when HOUSE.errUrl
   is set, POSTed there (server/clienterr.py writes it to a dated .jsonl file).
     clientErr.report(kind, errorOrMessage, {source, step})   log something the page caught itself
     clientErr.recent()                                       the last 50 records, oldest first */
(function () {
  var cfg = window.HOUSE || {};
  var URL_ERR = cfg.errUrl || '';
  var UA = navigator.userAgent || '';
  var MAX_SENT = 8, MAX_KEPT = 50;
  var sent = 0, seen = {}, kept = [];
  /* jusage: the dashboard is a file:// page with no server, so the ring also lives in localStorage
     (<appId>.errlog, the last 50) and Report attaches errors from earlier loads too. */
  var RING_KEY = (cfg.appId || 'app') + '.errlog';
  try { var prev = JSON.parse(localStorage.getItem(RING_KEY) || '[]'); if (Array.isArray(prev)) kept = prev.slice(-MAX_KEPT); } catch (e) {}
  function keep() { try { localStorage.setItem(RING_KEY, JSON.stringify(kept)); } catch (e) {} }
  /* A page path can carry a secret (an order link, a share token). HOUSE.cleanPath(path) may blank it. */
  function clean(p) { try { return cfg.cleanPath ? String(cfg.cleanPath(String(p))) : String(p); } catch (e) { return String(p); } }
  function msg(e) { return String((e && ((e.name ? e.name + ': ' : '') + (e.message || e))) || 'unknown').slice(0, 200); }
  /* Delivery. While the page is alive: XMLHttpRequest (WebKit's sendBeacon with a Blob body sometimes arrives
     with an empty body). sendBeacon only when the page is going away, where an XHR would be cancelled, or
     when an XHR cannot start or fails. Reports still in flight when the page hides are beaconed again. */
  var inflight = [];
  function beacon(data) {
    try { return !!(navigator.sendBeacon && navigator.sendBeacon(URL_ERR, new Blob([data], { type: 'application/json' }))); } catch (e) { return false; }
  }
  function post(data) {
    if (!URL_ERR) return;
    if (document.visibilityState === 'hidden') { if (beacon(data)) return; }
    var item = { data: data };
    try {
      var x = new XMLHttpRequest();
      x.__houseErrLog = true;
      x.open('POST', URL_ERR);
      x.setRequestHeader('Content-Type', 'application/json');
      x.onload = function () { var i = inflight.indexOf(item); if (i >= 0) inflight.splice(i, 1); };
      x.onerror = x.ontimeout = x.onabort = function () { var i = inflight.indexOf(item); if (i >= 0) { inflight.splice(i, 1); beacon(data); } };
      inflight.push(item);
      x.send(data);
    } catch (e) { var j = inflight.indexOf(item); if (j >= 0) inflight.splice(j, 1); beacon(data); }
  }
  function flush() { if (!URL_ERR) return; while (inflight.length) beacon(inflight.shift().data); }
  addEventListener('pagehide', flush);
  addEventListener('visibilitychange', function () { if (document.visibilityState === 'hidden') flush(); });
  function report(kind, err, extra) {
    try {
      extra = extra || {};
      var message = msg(err), key = kind + '|' + message + '|' + (extra.source || '') + '|' + (extra.step || '');
      if (seen[key]) return;
      seen[key] = 1;
      var rec = { kind: kind, message: message, source: extra.source, path: clean(location.pathname), step: extra.step,
                  ua: UA.slice(0, 80), version: cfg.version || '', app: cfg.appId || '', at: new Date().toISOString() };
      kept.push(rec); if (kept.length > MAX_KEPT) kept.shift();
      keep();
      if (sent >= MAX_SENT) return;
      sent++;
      post(JSON.stringify(rec));
    } catch (e) {}
  }
  window.clientErr = { report: report, recent: function () { return kept.slice(); } };

  addEventListener('error', function (ev) {
    if (ev.target && ev.target !== window) return;            // a failed <img>/<script> load is not a script error
    report('error', ev.error || ev.message, { source: (ev.filename || '').replace(location.origin, '') + ':' + (ev.lineno || 0) });
  });
  addEventListener('unhandledrejection', function (ev) { report('rejection', ev.reason); });
  /* Content-Security-Policy refusals: the directive + the blocked origin only (a path or query could carry a token). */
  addEventListener('securitypolicyviolation', function (ev) {
    var blocked = String(ev.blockedURI || '');
    try { if (/^https?:/.test(blocked)) blocked = new URL(blocked).origin; } catch (e) {}
    report('csp', (ev.effectiveDirective || ev.violatedDirective || 'csp') + ' blocked ' + (blocked || 'inline'),
           { source: String(ev.sourceFile || '').replace(location.origin, '') + ':' + (ev.lineNumber || 0) });
  });

  function path(u) { try { return clean(new URL(u, location.href).pathname); } catch (e) { return ''; } }
  var errPath = URL_ERR ? path(URL_ERR) : null;
  if (window.fetch) {                                         // failed fetches anywhere on the page
    var f = window.fetch;
    window.fetch = function (input, init) {
      var p = path(typeof input === 'string' ? input : (input && input.url));
      var out = f.apply(this, arguments);
      if (p === errPath || !out || !out.then) return out;
      out.then(function (r) { if (r && r.status >= 500) report('http', 'HTTP ' + r.status, { step: 'fetch ' + p }); },
               function (e) { report('net', e, { step: 'fetch ' + p }); });
      return out;                                             // the caller still gets the original promise (and its rejection)
    };
  }
  if (window.XMLHttpRequest) {
    var open = XMLHttpRequest.prototype.open;
    XMLHttpRequest.prototype.open = function (m, u) {
      var p = path(u);
      if (!this.__houseErrLog) {
        this.addEventListener('error', function () { report('net', 'xhr network error', { step: 'xhr ' + p }); });
        this.addEventListener('timeout', function () { report('net', 'xhr timeout', { step: 'xhr ' + p }); });
        this.addEventListener('load', function () { if (this.status >= 500) report('http', 'HTTP ' + this.status, { step: 'xhr ' + p }); });
      }
      return open.apply(this, arguments);
    };
  }
})();
