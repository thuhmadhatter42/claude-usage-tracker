/* The ONE file of per-app values for the web modules (house-kit config, filled for jusage's dashboard).
   Loaded first, after the page's data block. The version is the data block's: the engine writes it from
   VERSION when it builds the page, so there is no second copy to stamp. */
window.HOUSE = {
  appName: 'jusage',
  appId: 'jusage',                            // prefixes every localStorage key: jusage.accentColor, ...
  version: (function () {
    try { return String(JSON.parse(document.getElementById('data').textContent).version); }
    catch (e) { return '0.0.0'; }             // version.js logs a 0.0.0 as an unstamped build
  })(),
  reportTo: 'contact@fourletterrecords.com',  // jusage is used by others (the public repo)
  errUrl: '',                                 // a file:// page has no server: errors stay on the page
  reportUrl: ''                               // Report saves a file and opens the mail app
};
