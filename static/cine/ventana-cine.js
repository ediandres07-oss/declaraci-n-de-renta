// Tributando Ventanería · ventana que se desarma con el scroll (PRUEBA /ventaneria/cine, 7-nov-2026).
// Ventana corrediza de 2 hojas (1 móvil), Serie 20 del catálogo PROPIO de ejemplo (refs TV###).
// c (0…6): 0 armada · 1 marco y hojas se separan · 2 cada pieza con su nombre · 3 vuelta de cámara
//          4 se vuelve a armar y la hoja móvil corre · 5 queda arriba del cotizador.
import * as THREE from "https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.module.min.js";

const raiz = document.documentElement;
if (raiz.classList.contains("estatico")) throw new Error("modo estático");
const movil = matchMedia("(max-width: 860px)").matches || /Android|iPhone|iPad|Mobile/i.test(navigator.userAgent);
const clamp = (x, a = 0, b = 1) => Math.min(b, Math.max(a, x));
const sm = (a, b, x) => { x = clamp((x - a) / (b - a)); return x * x * (3 - 2 * x); };
const mix = (a, b, t) => a + (b - a) * t;

const canvas = document.getElementById("gl");
let renderer;
try { renderer = new THREE.WebGLRenderer({ canvas, antialias: !movil, powerPreference: "high-performance" }); }
catch (e) { raiz.classList.add("sin3d"); throw e; }
renderer.setPixelRatio(Math.min(devicePixelRatio || 1, movil ? 1.6 : 2));
renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 1.3;
renderer.shadowMap.enabled = !movil;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x0d1119);
scene.fog = new THREE.Fog(0x0d1119, 14, 42);
const camera = new THREE.PerspectiveCamera(36, 1, 0.1, 120);

// Entorno de taller para que el aluminio brille (sin addons).
(function entorno() {
  const env = new THREE.Scene();
  const cv = document.createElement("canvas"); cv.width = 16; cv.height = 256;
  const g = cv.getContext("2d"), gr = g.createLinearGradient(0, 0, 0, 256);
  gr.addColorStop(0, "#8a91a6"); gr.addColorStop(0.42, "#2a3142"); gr.addColorStop(0.55, "#2e2a24"); gr.addColorStop(1, "#07090d");
  g.fillStyle = gr; g.fillRect(0, 0, 16, 256);
  const t = new THREE.CanvasTexture(cv); t.colorSpace = THREE.SRGBColorSpace;
  env.add(new THREE.Mesh(new THREE.SphereGeometry(20, 32, 16), new THREE.MeshBasicMaterial({ map: t, side: THREE.BackSide })));
  const panel = (c, i, x, y, z, w, h) => {
    const m = new THREE.Mesh(new THREE.PlaneGeometry(w, h), new THREE.MeshBasicMaterial({ color: new THREE.Color(c).multiplyScalar(i), side: THREE.DoubleSide }));
    m.position.set(x, y, z); m.lookAt(0, 0, 0); env.add(m);
  };
  panel("#ffffff", 4, 0, 10, 7, 16, 3); panel("#ffe9c9", 1.6, -12, 3, 6, 4, 12); panel("#cfe3ff", 2, 12, 1, 4, 4, 10); panel("#ffffff", 1.5, 0, 0, 16, 20, 2);
  const pm = new THREE.PMREMGenerator(renderer);
  scene.environment = pm.fromScene(env, 0.03).texture; pm.dispose();
})();
scene.add(new THREE.HemisphereLight(0xdfe6ff, 0x1a1d24, 0.9));
const sol = new THREE.DirectionalLight(0xfff0d8, 2.2); sol.position.set(5, 8, 9); sol.castShadow = !movil;
sol.shadow.mapSize.set(1024, 1024); Object.assign(sol.shadow.camera, { left: -6, right: 6, top: 6, bottom: -6, near: 1, far: 30 });
scene.add(sol);
const luzOro = new THREE.PointLight(0xd4a24c, 16, 30, 2); luzOro.position.set(-6, 2, 5); scene.add(luzOro);
// Piso reflejado suave y resplandor dorado al fondo.
(function fondo() {
  const cv = document.createElement("canvas"); cv.width = cv.height = 256;
  const g = cv.getContext("2d"), gr = g.createRadialGradient(128, 128, 0, 128, 128, 128);
  gr.addColorStop(0, "rgba(212,162,76,.28)"); gr.addColorStop(0.5, "rgba(212,162,76,.07)"); gr.addColorStop(1, "rgba(212,162,76,0)");
  g.fillStyle = gr; g.fillRect(0, 0, 256, 256);
  const t = new THREE.CanvasTexture(cv); t.colorSpace = THREE.SRGBColorSpace;
  const m = new THREE.Mesh(new THREE.PlaneGeometry(60, 60), new THREE.MeshBasicMaterial({ map: t, transparent: true, depthWrite: false, fog: false }));
  m.position.set(0, 2, -22); scene.add(m);
})();
const piso = new THREE.Mesh(new THREE.CircleGeometry(9, 48), new THREE.ShadowMaterial({ opacity: 0.35 }));
piso.rotation.x = -Math.PI / 2; piso.position.y = -2.2; piso.receiveShadow = true; scene.add(piso);

// ---------------------------------------------------------------- materiales
const ACABADOS = {
  Natural: { color: 0xc9cdd3, metalness: 0.92, roughness: 0.3 },
  Blanco: { color: 0xf2f2ee, metalness: 0.15, roughness: 0.45 },
  Negro: { color: 0x26292f, metalness: 0.7, roughness: 0.38 },
  Bronce: { color: 0x7a5530, metalness: 0.85, roughness: 0.34 },
};
const alu = new THREE.MeshStandardMaterial({ ...ACABADOS.Natural, envMapIntensity: 1.2 });
const vidrioM = new THREE.MeshPhysicalMaterial({ color: 0xa8d3e6, metalness: 0, roughness: 0.04, transparent: true, opacity: 0.3, envMapIntensity: 2.2, clearcoat: 1, side: THREE.DoubleSide, depthWrite: false });
const goma = new THREE.MeshStandardMaterial({ color: 0x111215, roughness: 0.85 });
const felpaM = new THREE.MeshStandardMaterial({ color: 0x4a4d55, roughness: 1 });
const nylon = new THREE.MeshStandardMaterial({ color: 0xe8e3d6, roughness: 0.5 });
const acero = new THREE.MeshStandardMaterial({ color: 0x9aa0a8, metalness: 1, roughness: 0.25 });

const caja = (w, h, d, mat) => { const m = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), mat); m.castShadow = m.receiveShadow = !movil; return m; };
function pieza(nodo, base, offs) { nodo.userData = { base: base.clone(), offs }; nodo.position.copy(base); return nodo; }

// ---------------------------------------------------------------- ventana (1 u = 50 cm): 150 × 120 cm
const W = 3.0, H = 2.4, F = 0.1, DZ = 0.22;
const ventana = new THREE.Group(); scene.add(ventana);
const piezas = [];                                  // todo lo que se mueve: {nodo, offs:[e1,e2]}
const add = (padre, nodo, base, e1 = [0, 0, 0], e2 = [0, 0, 0]) => {
  pieza(nodo, base, [new THREE.Vector3(...e1), new THREE.Vector3(...e2)]); padre.add(nodo); piezas.push(nodo); return nodo;
};
// Marco
const cabezal = add(ventana, caja(W, F, DZ, alu), new THREE.Vector3(0, H / 2 - F / 2, 0), [0, 0.95, -0.7]);
const sillar = add(ventana, new THREE.Group(), new THREE.Vector3(0, -H / 2 + F / 2, 0), [0, -0.95, -0.7]);
sillar.add(caja(W, F, DZ, alu));
[-0.05, 0.05].forEach((z) => { const r = caja(W - 2 * F, 0.035, 0.012, alu); r.position.set(0, F / 2 + 0.017, z); sillar.add(r); });
const jambaI = add(ventana, caja(F, H - 2 * F, DZ, alu), new THREE.Vector3(-W / 2 + F / 2, 0, 0), [-1.0, 0, -0.7]);
const jambaD = add(ventana, caja(F, H - 2 * F, DZ, alu), new THREE.Vector3(W / 2 - F / 2, 0, 0), [1.0, 0, -0.7]);

// Hojas
const Wi = W - 2 * F, Hi = H - 2 * F, ws = Wi / 2 + 0.05, hs = Hi - 0.03, SW = 0.075, SD = 0.06;
function hoja(movil_, x, z) {
  const g = new THREE.Group();
  const lado = movil_ ? 1 : -1;                                   // lado del traslape (hacia la jamba)
  const r = {};
  r.sup = add(g, caja(ws, SW, SD, alu), new THREE.Vector3(0, hs / 2 - SW / 2, 0), [0, 0, 0], [0, 0.5, 0]);
  r.inf = add(g, caja(ws, SW, SD, alu), new THREE.Vector3(0, -hs / 2 + SW / 2, 0), [0, 0, 0], [0, -0.5, 0]);
  r.tras = add(g, caja(SW, hs - 2 * SW, SD, alu), new THREE.Vector3(lado * (ws / 2 - SW / 2), 0, 0), [0, 0, 0], [lado * 0.5, 0, 0]);
  r.eng = add(g, new THREE.Group(), new THREE.Vector3(-lado * (ws / 2 - SW / 2), 0, 0), [0, 0, 0], [-lado * 0.42, 0, 0]);
  r.eng.add(caja(SW, hs - 2 * SW, SD, alu));
  const gancho = caja(0.02, hs - 2 * SW, 0.05, alu); gancho.position.set(-lado * 0.045, 0, movil_ ? -0.03 : 0.03); r.eng.add(gancho);
  const gw = ws - 2 * SW + 0.02, gh = hs - 2 * SW + 0.02;
  r.vidrio = add(g, new THREE.Mesh(new THREE.BoxGeometry(gw, gh, 0.012), vidrioM), new THREE.Vector3(0, 0, 0), [0, 0, 0], [0, 0, 0.75]);
  // brillo diagonal del vidrio
  const brillo = new THREE.Mesh(new THREE.PlaneGeometry(0.22, gh * 1.05), new THREE.MeshBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.07, depthWrite: false }));
  brillo.rotation.z = 0.5; brillo.position.z = 0.008; r.vidrio.add(brillo);
  const emp = new THREE.Group();
  [[gw, 0.02, 0, gh / 2], [gw, 0.02, 0, -gh / 2], [0.02, gh, gw / 2, 0], [0.02, gh, -gw / 2, 0]].forEach(([a, b, px, py]) => {
    const e = caja(a, b, 0.026, goma); e.position.set(px, py, 0); emp.add(e);
  });
  r.emp = add(g, emp, new THREE.Vector3(0, 0, 0), [0, 0, 0], [0, 0, 0.38]);
  r.felpa = add(g, caja(0.014, hs - 2 * SW, 0.014, felpaM), new THREE.Vector3(lado * (ws / 2 - SW / 2), 0, movil_ ? -SD / 2 - 0.008 : SD / 2 + 0.008), [0, 0, 0], [lado * 0.82, 0, 0.25]);
  if (movil_) {
    const rod = new THREE.Group();
    [-1, 1].forEach((s) => {
      const casco = caja(0.24, 0.075, 0.05, acero); casco.position.set(s * (ws / 2 - 0.3), 0, 0); rod.add(casco);
      const rueda = new THREE.Mesh(new THREE.CylinderGeometry(0.06, 0.06, 0.035, 28), nylon);
      rueda.rotation.x = Math.PI / 2; rueda.position.set(s * (ws / 2 - 0.3), -0.05, 0); rod.add(rueda);
    });
    r.rod = add(g, rod, new THREE.Vector3(0, -hs / 2 + 0.01, 0), [0, 0, 0], [0, -0.75, 0.3]);
    const cierre = new THREE.Group();
    const cuerpoC = caja(0.035, 0.18, 0.03, acero); cierre.add(cuerpoC);
    const palanca = caja(0.03, 0.09, 0.025, goma); palanca.position.set(0, 0.03, 0.025); cierre.add(palanca);
    r.cierre = add(g, cierre, new THREE.Vector3(-lado * (ws / 2 - SW / 2), 0.0, SD / 2 + 0.02), [0, 0, 0], [-lado * 0.8, 0.15, 0.5]);
  }
  g.position.set(x, 0, z);
  return { g, r };
}
const fija = hoja(false, -Wi / 4 + 0.012, -0.045);
const movilH = hoja(true, Wi / 4 - 0.012, 0.045);
add(ventana, fija.g, fija.g.position.clone(), [-0.45, 0, 0.9]);
add(ventana, movilH.g, movilH.g.position.clone(), [0.45, 0, 1.9]);

// ---------------------------------------------------------------- etiquetas
const ETQ = [
  { n: "Marco", s: "Cabezal TV201 · Sillar TV202 · Jambas TV203", nodo: cabezal, at: [0.6, 0.06, 0], e: 1 },
  { n: "Hoja fija", s: "Corre por el riel de atrás", nodo: fija.r.sup, at: [-0.3, 0.05, 0], e: 1, solo1: true },
  { n: "Hoja móvil", s: "Corre por el riel de adelante", nodo: movilH.r.sup, at: [0.3, 0.05, 0], e: 1, solo1: true },
  { n: "Perfiles de la hoja", s: "Horizontal TV204 · Enganche TV205 · Traslape TV206", nodo: movilH.r.tras, at: [0, 0.6, 0], e: 2 },
  { n: "Vidrio templado", s: "8 mm claro", nodo: movilH.r.vidrio, at: [0.1, 0.3, 0], e: 2 },
  { n: "Rodachinas", s: "2 por hoja móvil", nodo: movilH.r.rod, at: [0.45, -0.03, 0], e: 2 },
  { n: "Felpa", s: "Sella aire y polvo", nodo: fija.r.felpa, at: [0, -0.5, 0], e: 2 },
  { n: "Cierre", s: "Cierre de embutir", nodo: movilH.r.cierre, at: [0, 0.08, 0], e: 2 },
  { n: "Empaques", s: "Empaque vinilo", nodo: fija.r.emp, at: [-0.45, -0.95, 0], e: 2 },
];
const capa = document.getElementById("etiq");
ETQ.forEach((t, i) => {
  const d = document.createElement("div"); d.className = "et";
  d.innerHTML = `<i></i><span>${t.n}<small>${t.s}</small></span>`;
  capa.appendChild(d); t.el = d; t.del = i * 0.05; t.v = new THREE.Vector3();
});

// ---------------------------------------------------------------- disposición
let VW = 1, VH = 1, ancho = false, dist = 10;
function medir() {
  VW = innerWidth; VH = innerHeight; ancho = VW > 860;
  renderer.setSize(VW, VH, false);
  camera.aspect = VW / VH;
  const k = 2 * Math.tan(THREE.MathUtils.degToRad(camera.fov / 2));
  dist = ancho ? Math.max(10, 6.4 / (k * camera.aspect * 0.55)) : Math.max(14.8, 4.6 / (k * camera.aspect));
  camera.updateProjectionMatrix();
}
medir();
addEventListener("resize", () => { if (Math.abs(innerWidth - VW) > 2 || Math.abs(innerHeight - VH) > 140) medir(); });

// ---------------------------------------------------------------- cuadro
const v3 = new THREE.Vector3();
let primero = true;
function render(c, t) {
  const e1 = sm(0.95, 1.6, c), e2 = sm(1.85, 2.55, c), arma = sm(3.85, 4.45, c);
  const k1 = e1 * (1 - arma), k2 = e2 * (1 - sm(3.85, 4.2, c));
  piezas.forEach((p) => {
    const u = p.userData;
    p.position.copy(u.base).addScaledVector(u.offs[0], k1).addScaledVector(u.offs[1], k2);
  });
  // La hoja móvil corre (abre y cierra) ya armada.
  const corre = sm(4.5, 4.75, c) - sm(4.82, 5.0, c);
  movilH.g.position.x += -1.25 * corre;

  // Encuadre: texto arriba (celular) o a la izquierda (PC); al final, la ventana sobre el cotizador.
  const fin = sm(4.9, 5.2, c);
  const orbita = sm(2.9, 3.85, c);
  ventana.rotation.set(0.06 + 0.1 * k2, -0.42 + 0.85 * orbita - 0.5 * arma * (1 - fin) + Math.sin(t * 0.3) * 0.04 + (ancho ? 0 : 0.1) * fin, 0);
  ventana.position.set(0, Math.sin(t * 0.6) * 0.03, 0);
  const esc = ancho ? 1 : mix(1, 0.8, fin);
  ventana.scale.setScalar(esc);
  camera.position.set(Math.sin(t * 0.12) * 0.2, 0.55 + 0.4 * k2, dist * ((ancho ? 0.95 : 0.8) + 0.18 * k1 + 0.12 * k2 + 0.12 * fin));
  camera.lookAt(0, 0, 0);
  if (ancho) camera.setViewOffset(VW, VH, mix(-VW * 0.2, VW * 0.2, fin), 0, VW, VH);
  else camera.setViewOffset(VW, VH, 0, mix(-VH * 0.14, VH * 0.27, fin), VW, VH);
  piso.position.y = -H / 2 - 0.05 - 0.9 * k1 - 0.6 * k2;

  renderer.render(scene, camera);

  // Etiquetas proyectadas a la pantalla
  ventana.updateMatrixWorld();
  const vivas = [];
  ETQ.forEach((tq) => {
    let o = tq.e === 1 ? sm(1.25, 1.55, c) : sm(2.15 + tq.del, 2.45 + tq.del, c);
    if (tq.solo1) o *= 1 - sm(2.0, 2.3, c);
    o *= 1 - sm(3.75, 3.95, c);
    if (!tq.nodo) o = 0;
    tq.el.style.opacity = o.toFixed(3);
    if (o <= 0.01) return;
    tq.v.set(...tq.at).applyMatrix4(tq.nodo.matrixWorld).project(camera);
    const x = (tq.v.x * 0.5 + 0.5) * VW, y = (-tq.v.y * 0.5 + 0.5) * VH;
    const izq = x > VW * (ancho ? 0.72 : 0.6);
    tq.el.classList.toggle("izq", izq);
    if (!tq.w || t - (tq.tw || 0) > 1) { tq.w = tq.el.offsetWidth || 160; tq.h = tq.el.offsetHeight || 34; tq.tw = t; }
    tq.lx = clamp(izq ? x - tq.w + 5 : x - 5, 6, VW - tq.w - 6); tq.ly = y - tq.h / 2;
    vivas.push(tq);
  });
  // Que no se monten: de arriba abajo, cada una baja lo que choque con las anteriores.
  vivas.sort((a, b) => a.ly - b.ly);
  for (let i = 1; i < vivas.length; i++) for (let j = 0; j < i; j++) {
    const a = vivas[j], b = vivas[i];
    if (b.lx < a.lx + a.w && a.lx < b.lx + b.w && b.ly < a.ly + a.h + 4) b.ly = a.ly + a.h + 4;
  }
  vivas.forEach((tq) => { tq.el.style.transform = `translate(${tq.lx.toFixed(1)}px, ${tq.ly.toFixed(1)}px)`; });
  if (primero) { primero = false; const p = document.getElementById("poster"); if (p) p.classList.add("fuera"); }
}

window.__vent3d = {
  render,
  acabado(n) { const a = ACABADOS[n] || ACABADOS.Natural; alu.color.setHex(a.color); alu.metalness = a.metalness; alu.roughness = a.roughness; },
};
if (window.__ventActualizar) window.__ventActualizar();
if (window.__ventCaptura) window.__ventCaptura(0);
