/* Red de fondo de Tributando: los nodos de la contabilidad conectados y una luz
   que recorre Factura DIAN → Lector → Libro → IVA → Retención → Renta.
   Uso: <canvas data-red-fondo data-centro="0.5,0.46" data-radio="0.44,0.40"></canvas>
   - Si el canvas es position:fixed toma el tamaño de la ventana; si no, el de su padre.
   - Respeta prefers-reduced-motion (dibuja quieto) y se pausa con la pestaña oculta. */
(function () {
  var NOMBRES = ['Factura DIAN', 'Lector', 'Libro', 'IVA 300', 'Retención 350', 'Nómina', 'PILA', 'Banco',
    'Cartera', 'Exógena', 'Renta 110', 'Cliente', 'Proveedor', 'Inventario', 'Estados financieros', 'Cierre de mes'];
  var RUTA = ['Factura DIAN', 'Lector', 'Libro', 'IVA 300', 'Retención 350', 'Renta 110'];
  var EXTRA = [['Nómina', 'PILA'], ['Nómina', 'Libro'], ['Banco', 'Libro'], ['Cartera', 'Cliente'],
    ['Proveedor', 'Factura DIAN'], ['Libro', 'Estados financieros'], ['Libro', 'Exógena'], ['Inventario', 'Libro'],
    ['Cierre de mes', 'Estados financieros'], ['Banco', 'Cartera'], ['Cliente', 'Factura DIAN'], ['PILA', 'Retención 350']];
  var QUIETO = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  function par(v, d) { var p = (v || '').split(',').map(parseFloat); return (p.length === 2 && !isNaN(p[0]) && !isNaN(p[1])) ? p : d; }

  function red(cv) {
    if (!cv.getContext) return;
    var cx = cv.getContext('2d'), DPR = Math.min(2, window.devicePixelRatio || 1), W = 0, H = 0, N = [], E = [], pulsos = [];
    var fijo = getComputedStyle(cv).position === 'fixed';
    var centro = par(cv.dataset.centro, [0.5, 0.46]), radio = par(cv.dataset.radio, [0.44, 0.40]);

    function medir() {
      var r = fijo ? { width: innerWidth, height: innerHeight } : cv.parentElement.getBoundingClientRect();
      return [Math.max(1, r.width), Math.max(1, r.height)];
    }
    function iniciar() {
      var m = medir(), movil = m[0] < 760;
      W = cv.width = m[0] * DPR; H = cv.height = m[1] * DPR; N = [];
      var c0x = W * (movil ? 0.5 : centro[0]), c0y = H * centro[1], rx = W * (movil ? 0.46 : radio[0]), ry = H * radio[1];
      NOMBRES.forEach(function (t, i) {
        var a = i / NOMBRES.length * Math.PI * 2 + 0.4, f = 0.82 + 0.18 * ((i * 37) % 10) / 10;
        N.push({ t: t, x: c0x + Math.cos(a) * rx * f, y: c0y + Math.sin(a) * ry * f,
          vx: (Math.random() - 0.5) * 0.14 * DPR, vy: (Math.random() - 0.5) * 0.14 * DPR, big: true });
      });
      for (var i = 0, n = movil ? 30 : 60; i < n; i++)
        N.push({ t: '', x: Math.random() * W, y: Math.random() * H,
          vx: (Math.random() - 0.5) * 0.22 * DPR, vy: (Math.random() - 0.5) * 0.22 * DPR, big: false });
      var id = function (t) { for (var k = 0; k < N.length; k++) if (N[k].t === t) return k; return -1; };
      E = [];
      for (var r = 0; r < RUTA.length - 1; r++) E.push([id(RUTA[r]), id(RUTA[r + 1]), true]);
      EXTRA.forEach(function (p) { E.push([id(p[0]), id(p[1]), false]); });
    }
    function dibujar() {
      cx.clearRect(0, 0, W, H);
      var lim = 150 * DPR, i, j;
      if (!QUIETO) for (i = 0; i < N.length; i++) {
        var a = N[i]; a.x += a.vx; a.y += a.vy;
        if (a.x < 0 || a.x > W) a.vx *= -1;
        if (a.y < 0 || a.y > H) a.vy *= -1;
      }
      for (i = 0; i < N.length; i++) for (j = i + 1; j < N.length; j++) {
        var d = Math.hypot(N[i].x - N[j].x, N[i].y - N[j].y);
        if (d < lim) {
          cx.strokeStyle = 'rgba(55,211,143,' + (0.18 * (1 - d / lim)) + ')'; cx.lineWidth = DPR;
          cx.beginPath(); cx.moveTo(N[i].x, N[i].y); cx.lineTo(N[j].x, N[j].y); cx.stroke();
        }
      }
      E.forEach(function (e) {
        cx.strokeStyle = e[2] ? 'rgba(55,211,143,.5)' : 'rgba(55,211,143,.2)'; cx.lineWidth = (e[2] ? 1.5 : 1) * DPR;
        cx.beginPath(); cx.moveTo(N[e[0]].x, N[e[0]].y); cx.lineTo(N[e[1]].x, N[e[1]].y); cx.stroke();
      });
      var tramos = E.filter(function (e) { return e[2]; });
      pulsos = pulsos.filter(function (p) { return p.k < tramos.length; });
      pulsos.forEach(function (p) {
        var e = tramos[p.k], A = N[e[0]], B = N[e[1]], x = A.x + (B.x - A.x) * p.t, y = A.y + (B.y - A.y) * p.t;
        var g = cx.createRadialGradient(x, y, 0, x, y, 14 * DPR);
        g.addColorStop(0, 'rgba(170,255,215,.95)'); g.addColorStop(1, 'rgba(55,211,143,0)');
        cx.fillStyle = g; cx.beginPath(); cx.arc(x, y, 14 * DPR, 0, 7); cx.fill();
        p.t += 0.012; if (p.t >= 1) { p.t = 0; p.k++; }
      });
      N.forEach(function (n) {
        if (!n.big) { cx.fillStyle = 'rgba(55,211,143,.35)'; cx.beginPath(); cx.arc(n.x, n.y, 1.6 * DPR, 0, 7); cx.fill(); return; }
        var r = RUTA.indexOf(n.t) >= 0;
        cx.fillStyle = r ? '#37d38f' : 'rgba(55,211,143,.7)';
        cx.beginPath(); cx.arc(n.x, n.y, (r ? 4.5 : 3.2) * DPR, 0, 7); cx.fill();
        cx.font = (r ? 12.5 : 11) * DPR + 'px Inter,"Plus Jakarta Sans",-apple-system,"Segoe UI",sans-serif';
        cx.fillStyle = r ? 'rgba(234,245,239,.85)' : 'rgba(159,181,170,.7)';
        cx.fillText(n.t, n.x + 8 * DPR, n.y + 4 * DPR);
      });
    }
    function bucle() { if (!document.hidden) dibujar(); requestAnimationFrame(bucle); }
    iniciar();
    addEventListener('resize', function () { iniciar(); if (QUIETO) dibujar(); });
    if (QUIETO) { dibujar(); return; }
    pulsos.push({ k: 0, t: 0 });
    setInterval(function () { if (!document.hidden) pulsos.push({ k: 0, t: 0 }); }, 2600);
    bucle();
  }

  function arrancar() { Array.prototype.forEach.call(document.querySelectorAll('canvas[data-red-fondo]'), red); }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', arrancar); else arrancar();
})();
