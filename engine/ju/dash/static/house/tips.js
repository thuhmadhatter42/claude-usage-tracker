/* tips.js: BOARD TALK for a web face (BOARD-TALK.md). The web twin of Tips.swift.
   A tip is the control's `title` attribute (or `data-tip`): a phrase, 60 characters at most, none of the
   banned words. The rules are checked, not hoped for:
     HouseTips.problems()            every tip on the page that breaks a rule (the test page fails on any)
     HouseTips.checkAtLoad()         the same, each one logged through errlog.js; call once after the page is built
     HouseTips.check('text')         the rules for one string
   Load after errlog.js. */
(function () {
  'use strict';
  var MAX = 60;
  var BANNED = ['please', 'you can', 'allows you to', 'lets you', 'simply', 'easily', 'in order to', 'this will'];
  function check(text) {
    var out = [], t = String(text || '');
    if (!t.trim()) out.push('empty');
    if (t.length > MAX) out.push('too long (' + t.length + ')');
    /* whole words only: punctuation becomes a space, so "Please." is caught and "Displeased" is not */
    var lower = ' ' + t.toLowerCase().replace(/[^a-z0-9]+/g, ' ') + ' ';
    BANNED.forEach(function (w) { if (lower.indexOf(' ' + w + ' ') >= 0) out.push('uses "' + w + '"'); });
    return out;
  }
  function label(el) { return (el.getAttribute('aria-label') || el.textContent || el.id || el.tagName).trim().slice(0, 30); }
  function problems(root) {
    var out = [];
    (root || document).querySelectorAll('[title],[data-tip]').forEach(function (el) {
      var tip = el.hasAttribute('data-tip') ? el.getAttribute('data-tip') : el.getAttribute('title');
      check(tip).forEach(function (p) { out.push(p + ': ' + label(el)); });
    });
    return out;
  }
  window.HouseTips = {
    MAX: MAX, BANNED: BANNED, check: check, problems: problems,
    checkAtLoad: function (root) {
      var p = problems(root);
      p.forEach(function (line) {
        if (window.clientErr) window.clientErr.report('tips', line); else if (window.console) console.error('[tips] ' + line);
      });
      return p;
    }
  };
})();
