/* version.js: the version on screen, for a web face. The version lives in ONE place (the VERSION file); the
   build stamps it into house-config.js (HOUSE.version); this prints it wherever the page asks:
     <span data-house-version></span>      ->  v1.2.3
     <span data-house-about></span>        ->  App Name 1.2.3 · Four Letter Records
   HouseVersion.apply() fills them (called once at load; call again after drawing new markup).
   Stamp step for the build (any language):   sed -i '' "s/version: '[^']*'/version: '$(cat VERSION)'/" house-config.js */
(function () {
  'use strict';
  var cfg = window.HOUSE || {};
  function valid(v) { return /^\d+\.\d+\.\d+$/.test(String(v || '')); }
  function apply(root) {
    var v = cfg.version || '0.0.0';
    if (!valid(v) || v === '0.0.0') {
      var msg = 'HOUSE.version is "' + v + '": the build did not stamp it from VERSION';
      if (window.clientErr) window.clientErr.report('app', msg); else if (window.console) console.error('[version] ' + msg);
    }
    (root || document).querySelectorAll('[data-house-version]').forEach(function (el) { el.textContent = 'v' + v; });
    (root || document).querySelectorAll('[data-house-about]').forEach(function (el) {
      el.textContent = (cfg.appName || 'App') + ' ' + v + ' · Four Letter Records';
    });
    return v;
  }
  window.HouseVersion = { apply: apply, valid: valid };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', function () { apply(); }); else apply();
})();
