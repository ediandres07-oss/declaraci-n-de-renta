/* Medidor propio de Tributando (src/medidor.py). Sin cookies, sin almacenamiento local,
   sin datos personales: manda solo la página, el origen, utm_*, celular/computador,
   el paso del recorrido y los clics importantes. */
(function () {
  var R = location.pathname, q = new URLSearchParams(location.search);
  var d = (matchMedia('(pointer:coarse)').matches || innerWidth < 760) ? 'cel' : 'pc';
  function env(o) {
    o.r = R; o.d = d;
    try {
      var s = JSON.stringify(o);
      if (!(navigator.sendBeacon && navigator.sendBeacon('/api/visita', new Blob([s], {type: 'text/plain'}))))
        fetch('/api/visita', {method: 'POST', body: s, keepalive: true, headers: {'Content-Type': 'text/plain'}});
    } catch (e) {}
  }
  env({e: 'vista', ref: document.referrer,
       utm: {source: q.get('utm_source') || '', medium: q.get('utm_medium') || '', campaign: q.get('utm_campaign') || ''}});
  var vistos = {};
  addEventListener('recorrido:paso', function (ev) {
    var n = +ev.detail; if (n >= 1 && n <= 6 && !vistos[n]) { vistos[n] = 1; env({e: 'paso', n: n}); }
  });
  document.addEventListener('click', function (ev) {
    var a = ev.target.closest && ev.target.closest('a,button'); if (!a) return;
    var h = a.getAttribute('href') || '';
    var b = a.hasAttribute('data-ar') ? 'ar'
          : /wa\.me\//.test(h) ? 'whatsapp'
          : /descargar-lector-mac/.test(h) ? 'lector_mac'
          : /descargar-lector/.test(h) ? 'lector_win'
          : /\/contadores\/recorrido/.test(h) ? 'ver3d'
          : /onrender\.com/.test(h) ? 'app' : '';
    if (b) env({e: 'clic', b: b});
  }, true);
})();
