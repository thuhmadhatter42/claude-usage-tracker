/* report.js: REPORT for a web face. One button on screen, one dialog with a text box, the log attached.
   The web twin of Report.swift (Artemis' report sheet). Needs house-config.js, errlog.js, glow.css, report.css.

     HouseReport.mountButton(element)     draw the on-screen REPORT button into element
     HouseReport.open()                   open the dialog (a "Send Report…" menu entry calls this)
     HouseReport.extra = function () { return ['Room: Studio D']; }   app facts for every report (optional)

   Where it goes is ONE constant: HOUSE.reportTo (house-config.js).
   How it gets there:
     HOUSE.reportUrl set    -> POST {to, subject, body, log} to that route; the server mails it (the app's mailer)
     no route, or it fails  -> the report is saved as a text file (the browser's download) AND the mail app opens
                               addressed to HOUSE.reportTo, asking for the file to be attached
   The "log" is errlog.js's record of this page's errors (clientErr.recent()). A page cannot attach a file to
   an email by itself; that is why the no-server path is download + mailto. */
(function () {
  'use strict';
  var cfg = window.HOUSE || {};
  var el = null, draft = '';

  function logErr(msg, e) {
    if (window.clientErr) window.clientErr.report('report', msg + (e ? ': ' + (e.message || e) : ''));
    else if (window.console) console.error('[report] ' + msg, e || '');
  }
  function subject() { return (cfg.appName || 'App') + ' ' + (cfg.version || '?') + ' report'; }
  function pad(n) { return (n < 10 ? '0' : '') + n; }
  function stamp(d) { return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()) + ' ' + pad(d.getHours()) + '-' + pad(d.getMinutes()) + '-' + pad(d.getSeconds()); }
  /* The body: the user's words first, then what a reader needs to place the report. */
  function body(text, when) {
    var said = String(text || '').trim(), extra = [];
    try { extra = api.extra ? api.extra() || [] : []; } catch (e) { logErr('extra() failed', e); }
    return [said || '(no description given)', '', (cfg.appName || 'App') + ' ' + (cfg.version || '?'),
            'Page: ' + location.pathname, 'Browser: ' + (navigator.userAgent || '?')]
      .concat(extra).concat([(when || new Date()).toString()]).join('\n') + '\n';
  }
  function logText() {
    var recs = window.clientErr ? window.clientErr.recent() : [];
    if (!recs.length) return '(no errors recorded on this page)\n';
    return recs.map(function (r) { return [r.at, r.kind, r.message, r.source || '', r.step || ''].join('  ').trim(); }).join('\n') + '\n';
  }
  function fileText(text, when) { return body(text, when) + '\n---- log ----\n' + logText(); }
  function mailto(text) {
    var note = 'Please attach the file "' + fileName(new Date()) + '" from your Downloads folder.\n\n' + String(text || '').trim();
    return 'mailto:' + encodeURIComponent(cfg.reportTo || '').replace(/%40/g, '@') + '?subject=' + encodeURIComponent(subject()) + '&body=' + encodeURIComponent(note.slice(0, 1500));
  }
  function fileName(when) { return (cfg.appName || 'App') + ' report ' + stamp(when) + '.txt'; }

  /* THE SEAMS: every side effect is one of these three, so the test page swaps them and nothing real happens. */
  var deliver = {
    post: function (url, payload) {
      return fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })
        .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return true; });
    },
    download: function (name, text) {
      var a = document.createElement('a');
      a.href = URL.createObjectURL(new Blob([text], { type: 'text/plain' }));
      a.download = name; document.body.appendChild(a); a.click();
      setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
    },
    openMail: function (href) { location.href = href; }
  };
  function fallback(text, when) {
    try { deliver.download(fileName(when), fileText(text, when)); }
    catch (e) { logErr('could not save the report file', e); return Promise.resolve({ ok: false, how: 'failed' }); }
    try { deliver.openMail(mailto(text)); } catch (e) { logErr('could not open the mail app', e); }
    return Promise.resolve({ ok: true, how: 'file+mail' });
  }
  /* Send. Resolves {ok, how}: 'posted' | 'file+mail' | 'failed'. Never rejects. */
  function send(text) {
    var when = new Date();
    if (!cfg.reportUrl) return fallback(text, when);
    var payload = { to: cfg.reportTo, subject: subject(), body: body(text, when), log: logText(), app: cfg.appId || '', version: cfg.version || '' };
    return deliver.post(cfg.reportUrl, payload).then(function () { return { ok: true, how: 'posted' }; }, function (e) {
      logErr('the report route failed, saving a file instead', e);
      return fallback(text, when);
    });
  }

  function close() { if (el) el.classList.remove('show'); }
  function open() {
    if (!el) {
      el = document.createElement('div');
      el.className = 'hk-report-overlay';
      el.innerHTML = '<div class="hk-report-panel" role="dialog" aria-modal="true" aria-label="Send a Report">' +
        '<div class="hk-report-title">Send a Report</div>' +
        '<div class="hk-report-line">What happened? The log is attached.</div>' +
        '<textarea class="hk-report-text" aria-label="What happened" rows="6"></textarea>' +
        '<div class="hk-report-status" role="status"></div>' +
        '<div class="hk-report-foot"><button type="button" class="hk-btn" data-act="save" title="Save the report as a file.">Save Log</button><span class="hk-report-sp"></span>' +
        '<button type="button" class="hk-btn" data-act="cancel">Cancel</button><button type="button" class="hk-btn primary" data-act="send">Send</button></div></div>';
      el.addEventListener('click', function (e) { if (e.target === el) close(); });
      el.addEventListener('keydown', function (e) { if (e.key === 'Escape') { e.stopPropagation(); close(); } });
      var ta = el.querySelector('textarea'), status = el.querySelector('.hk-report-status');
      ta.addEventListener('input', function () { draft = ta.value; });
      el.querySelector('[data-act=cancel]').addEventListener('click', close);
      el.querySelector('[data-act=save]').addEventListener('click', function () {
        var when = new Date();
        try { deliver.download(fileName(when), fileText(draft, when)); status.textContent = 'Saved to Downloads.'; }
        catch (e) { logErr('could not save the report file', e); status.textContent = 'Could not save the file.'; }
      });
      el.querySelector('[data-act=send]').addEventListener('click', function () {
        status.textContent = 'Sending…';
        send(draft).then(function (r) {
          if (!r.ok) { status.textContent = 'Not sent. Your words are still here.'; return; }   // nothing is lost
          draft = ''; ta.value = ''; status.textContent = ''; close();
        });
      });
      document.body.appendChild(el);
    }
    el.querySelector('textarea').value = draft;
    el.querySelector('.hk-report-status').textContent = '';
    el.classList.add('show');
    el.querySelector('textarea').focus();
  }

  var api = window.HouseReport = {
    extra: null, deliver: deliver,
    subject: subject, body: body, logText: logText, fileText: fileText, fileName: fileName, mailto: mailto,
    send: send, open: open, close: close,
    isOpen: function () { return !!el && el.classList.contains('show'); },
    mountButton: function (target) {
      var b = document.createElement('button');
      b.type = 'button'; b.className = 'hk-btn hk-report-btn'; b.textContent = 'Report';
      b.title = 'Send a report, log attached.';
      b.addEventListener('click', open);
      target.appendChild(b);
      return b;
    }
  };
})();
