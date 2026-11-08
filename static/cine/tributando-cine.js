// Tributando · landing cinematográfica (PRUEBA /cine, 7-nov-2026).
// Una sola escena Three.js que avanza con el capítulo continuo c (0…6) que calcula la página:
//  0 pila de facturas · 1 se desarman en datos (XML) y forman el estado financiero
//  2 celular con WhatsApp · 3 declaraciones con sello «Presentada» · 4 módulos que encajan · 5 logo.
// Todo es de EJEMPLO (sin clientes reales). En celular baja la calidad (menos partículas, sin antialias).
import * as THREE from "https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.module.min.js";

const CFG = window.CINE_CFG || { modulos: [] };
const raiz = document.documentElement;
const movil = matchMedia("(max-width: 860px)").matches || /Android|iPhone|iPad|Mobile/i.test(navigator.userAgent);
const lento = movil && (navigator.hardwareConcurrency || 4) <= 4;

const clamp = (x, a = 0, b = 1) => Math.min(b, Math.max(a, x));
const sm = (a, b, x) => { x = clamp((x - a) / (b - a)); return x * x * (3 - 2 * x); };
const lin = (a, b, x) => clamp((x - a) / (b - a));
const mix = (a, b, t) => a + (b - a) * t;
const back = (x) => { const c1 = 1.70158, c3 = c1 + 1; return 1 + c3 * Math.pow(x - 1, 3) + c1 * Math.pow(x - 1, 2); };
function rnd(seed) { let s = seed >>> 0; return () => ((s = (s * 1664525 + 1013904223) >>> 0) / 4294967296); }
const R = rnd(20261107);

if (raiz.classList.contains("estatico")) throw new Error("modo estático");

// ---------------------------------------------------------------- renderer
const canvas = document.getElementById("gl");
let renderer;
try {
  renderer = new THREE.WebGLRenderer({ canvas, antialias: !movil, powerPreference: "high-performance" });
} catch (e) {
  raiz.classList.add("sin3d");
  throw e;
}
renderer.setPixelRatio(Math.min(devicePixelRatio || 1, lento ? 1.25 : movil ? 1.6 : 2));
renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 1.08;
const maxAniso = Math.min(8, renderer.capabilities.getMaxAnisotropy());

const scene = new THREE.Scene();
const BG = new THREE.Color(0x070a12);
scene.background = BG;
scene.fog = new THREE.Fog(0x070a12, 12, 40);
const camera = new THREE.PerspectiveCamera(38, 1, 0.1, 120);

// Entorno para reflejos (sin addons): un cuarto con gradiente y paneles de luz.
(function entorno() {
  const env = new THREE.Scene();
  const cv = document.createElement("canvas"); cv.width = 16; cv.height = 256;
  const g = cv.getContext("2d"), gr = g.createLinearGradient(0, 0, 0, 256);
  gr.addColorStop(0, "#3a4258"); gr.addColorStop(0.45, "#141a28"); gr.addColorStop(0.55, "#2b2416"); gr.addColorStop(1, "#05070c");
  g.fillStyle = gr; g.fillRect(0, 0, 16, 256);
  const t = new THREE.CanvasTexture(cv); t.colorSpace = THREE.SRGBColorSpace;
  env.add(new THREE.Mesh(new THREE.SphereGeometry(20, 32, 16), new THREE.MeshBasicMaterial({ map: t, side: THREE.BackSide })));
  const panel = (c, i, x, y, z, w, h) => {
    const m = new THREE.Mesh(new THREE.PlaneGeometry(w, h), new THREE.MeshBasicMaterial({ color: new THREE.Color(c).multiplyScalar(i), side: THREE.DoubleSide }));
    m.position.set(x, y, z); m.lookAt(0, 0, 0); env.add(m);
  };
  panel("#ffffff", 3.2, 0, 9, 6, 14, 4);
  panel("#e6c483", 2.4, -12, 2, 4, 5, 9);
  panel("#9fe8c4", 1.6, 12, 0, 2, 5, 8);
  const pm = new THREE.PMREMGenerator(renderer);
  scene.environment = pm.fromScene(env, 0.04).texture;
  pm.dispose();
})();

scene.add(new THREE.HemisphereLight(0xc9d4ff, 0x0b0d14, 0.55));
const sol = new THREE.DirectionalLight(0xfff1dc, 1.6); sol.position.set(4, 7, 9); scene.add(sol);
const luzOro = new THREE.PointLight(0xe6c483, 30, 26, 2); luzOro.position.set(-5, 3, 5); scene.add(luzOro);
const luzVerde = new THREE.PointLight(0x37d38f, 24, 24, 2); luzVerde.position.set(5, -2, 4); scene.add(luzVerde);

// Fondo: resplandor dorado arriba, como la home.
(function fondo() {
  const cv = document.createElement("canvas"); cv.width = cv.height = 256;
  const g = cv.getContext("2d"), gr = g.createRadialGradient(128, 70, 0, 128, 70, 150);
  gr.addColorStop(0, "rgba(230,196,131,.30)"); gr.addColorStop(0.5, "rgba(230,196,131,.07)"); gr.addColorStop(1, "rgba(230,196,131,0)");
  g.fillStyle = gr; g.fillRect(0, 0, 256, 256);
  const t = new THREE.CanvasTexture(cv); t.colorSpace = THREE.SRGBColorSpace;
  const m = new THREE.Mesh(new THREE.PlaneGeometry(90, 60), new THREE.MeshBasicMaterial({ map: t, transparent: true, depthWrite: false, fog: false }));
  m.position.set(0, 6, -30); scene.add(m);
})();

// ---------------------------------------------------------------- utilidades de lienzo
function lienzo(w, h) { const c = document.createElement("canvas"); c.width = w; c.height = h; return [c, c.getContext("2d")]; }
function textura(c) { const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace; t.anisotropy = maxAniso; return t; }
function redondo(g, x, y, w, h, r) { g.beginPath(); g.moveTo(x + r, y); g.arcTo(x + w, y, x + w, y + h, r); g.arcTo(x + w, y + h, x, y + h, r); g.arcTo(x, y + h, x, y, r); g.arcTo(x, y, x + w, y, r); g.closePath(); }
const FF = "'Plus Jakarta Sans', -apple-system, 'Segoe UI', Roboto, sans-serif";
const pesos = (n) => "$" + Math.round(n).toLocaleString("es-CO").replace(/,/g, ".");
function formaRedonda(w, h, r) {
  const s = new THREE.Shape(), x = -w / 2, y = -h / 2;
  s.moveTo(x + r, y); s.lineTo(x + w - r, y); s.quadraticCurveTo(x + w, y, x + w, y + r);
  s.lineTo(x + w, y + h - r); s.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
  s.lineTo(x + r, y + h); s.quadraticCurveTo(x, y + h, x, y + h - r);
  s.lineTo(x, y + r); s.quadraticCurveTo(x, y, x + r, y); return s;
}

// ---------------------------------------------------------------- 0 · facturas de papel
const TIPOS = [
  ["FACTURA ELECTRÓNICA DE VENTA", "FE"], ["DOCUMENTO SOPORTE", "DS"], ["NOTA CRÉDITO", "NC"],
  ["TIQUETE POS ELECTRÓNICO", "POS"], ["FACTURA ELECTRÓNICA DE VENTA", "FV"], ["CUENTA DE COBRO", "CC"]];
function texPapel(k) {
  const r = rnd(77 + k * 13), [c, g] = lienzo(512, 680);
  g.fillStyle = "#f6f3ec"; g.fillRect(0, 0, 512, 680);
  g.fillStyle = "#e9e4d8"; g.fillRect(0, 0, 512, 8);
  g.fillStyle = "#1b2333"; g.font = `800 23px ${FF}`; g.fillText(TIPOS[k][0], 32, 58);
  g.font = `600 17px ${FF}`; g.fillStyle = "#59606b";
  g.fillText("Empresa de ejemplo S.A.S.", 32, 88); g.fillText("NIT 900.000.000-0", 32, 110);
  g.fillText(`No. ${TIPOS[k][1]}-${1000 + Math.floor(r() * 8999)}`, 32, 132);
  g.fillStyle = "#1b2333";                                    // «QR» de adorno
  for (let i = 0; i < 9; i++) for (let j = 0; j < 9; j++) if (r() > 0.5 || i % 8 === 0 || j % 8 === 0) g.fillRect(392 + i * 10, 40 + j * 10, 9, 9);
  g.fillStyle = "#ebe6da"; g.fillRect(32, 160, 448, 30);
  g.fillStyle = "#59606b"; g.font = `700 14px ${FF}`; g.fillText("DESCRIPCIÓN", 44, 180); g.fillText("CANT.", 300, 180); g.fillText("VALOR", 400, 180);
  let sub = 0;
  for (let i = 0; i < 7; i++) {
    const y = 214 + i * 40, v = (2 + Math.floor(r() * 40)) * 25000;
    sub += v;
    g.fillStyle = "#c9c2b2"; g.fillRect(44, y - 9, 120 + r() * 110, 9);
    g.fillStyle = "#1b2333"; g.font = `600 15px ${FF}`; g.fillText(String(1 + Math.floor(r() * 9)), 312, y);
    g.fillText(pesos(v), 388, y);
    g.fillStyle = "#e4dfd3"; g.fillRect(32, y + 14, 448, 1);
  }
  const iva = sub * 0.19;
  g.font = `600 16px ${FF}`; g.fillStyle = "#59606b";
  g.fillText("Subtotal", 280, 530); g.fillText("IVA 19 %", 280, 556);
  g.fillStyle = "#1b2333"; g.fillText(pesos(sub), 388, 530); g.fillText(pesos(iva), 388, 556);
  g.font = `800 20px ${FF}`; g.fillText("TOTAL", 280, 590); g.fillText(pesos(sub + iva), 380, 590);
  g.font = `500 11px monospace`; g.fillStyle = "#8a8f99";
  let cufe = ""; for (let i = 0; i < 60; i++) cufe += "0123456789abcdef"[Math.floor(r() * 16)];
  g.fillText("CUFE: " + cufe, 32, 640);
  return textura(c);
}
const texPapeles = TIPOS.map((_, k) => texPapel(k));
const PW = 1.5, PH = 2.0;
const geoPapel = new THREE.PlaneGeometry(PW, PH, 10, 1);
{ const p = geoPapel.attributes.position; for (let i = 0; i < p.count; i++) p.setZ(i, 0.06 * Math.sin((p.getX(i) / PW + 0.5) * Math.PI)); geoPapel.computeVertexNormals(); }
const papeles = new THREE.Group(); scene.add(papeles);
const NP = 12;
for (let i = 0; i < NP; i++) {
  const m = new THREE.Mesh(geoPapel, new THREE.MeshStandardMaterial({ map: texPapeles[i % texPapeles.length], roughness: 0.85, metalness: 0, side: THREE.DoubleSide, transparent: true }));
  const d = new THREE.Vector3(R() - 0.5, R() - 0.3, R() * 0.9 + 0.2).normalize();
  m.userData = {
    base: new THREE.Vector3((R() - 0.5) * 0.7, -1.6 + i * 0.3, (R() - 0.5) * 0.3),
    rot: new THREE.Euler(-0.95 + (R() - 0.5) * 0.25, (R() - 0.5) * 0.5, (R() - 0.5) * 0.7),
    dir: d, dist: 3 + R() * 3, giro: new THREE.Vector3((R() - 0.5) * 6, (R() - 0.5) * 6, (R() - 0.5) * 4), fase: R() * 6.28,
  };
  papeles.add(m);
}

// ---------------------------------------------------------------- 1 · estado financiero 3D
const FILAS = [
  ["ACTIVO", "", 1], ["1105", "Caja", 2450000], ["1110", "Bancos", 38920000], ["1305", "Clientes", 24180000],
  ["1435", "Inventarios", 31600000], ["1524", "Equipo de oficina", 8900000], ["", "Total activo", 106050000, 2],
  ["PASIVO", "", 1], ["2205", "Proveedores", 18340000], ["2365", "Retención en la fuente", 1120000],
  ["2408", "IVA por pagar", 4870000], ["", "Total pasivo", 24330000, 2],
  ["PATRIMONIO", "", 1], ["3115", "Aportes sociales", 50000000], ["3605", "Utilidad del ejercicio", 31720000],
  ["", "Total patrimonio", 81720000, 2]];
const LW = 3.9, LH = 5.0;
function texLibro() {
  const [c, g] = lienzo(780, 1000);
  g.fillStyle = "rgba(10,16,30,.92)"; redondo(g, 0, 0, 780, 1000, 34); g.fill();
  g.strokeStyle = "rgba(230,196,131,.55)"; g.lineWidth = 3; redondo(g, 2, 2, 776, 996, 32); g.stroke();
  g.fillStyle = "#e6c483"; g.font = `800 15px ${FF}`; g.fillText("ESTADO DE SITUACIÓN FINANCIERA", 44, 66);
  g.fillStyle = "#eef1f6"; g.font = `800 34px ${FF}`; g.fillText("Empresa de ejemplo S.A.S.", 44, 110);
  g.fillStyle = "#838c9c"; g.font = `600 17px ${FF}`; g.fillText("Causado solo, desde el XML de la DIAN", 44, 140);
  FILAS.forEach((f, i) => {
    const y = 200 + i * 49;
    if (f[2] === 1) { g.fillStyle = "#e6c483"; g.font = `800 18px ${FF}`; g.fillText(f[0], 44, y); return; }
    if (f[3] === 2) {
      g.fillStyle = "rgba(55,211,143,.12)"; g.fillRect(30, y - 30, 720, 44);
      g.fillStyle = "#37d38f"; g.font = `800 22px ${FF}`; g.fillText(f[1], 44, y);
      g.textAlign = "right"; g.fillText(pesos(f[2]), 736, y); g.textAlign = "left"; return;
    }
    g.fillStyle = "#838c9c"; g.font = `600 18px monospace`; g.fillText(f[0], 44, y);
    g.fillStyle = "#eef1f6"; g.font = `600 21px ${FF}`; g.fillText(f[1], 120, y);
    g.textAlign = "right"; g.fillText(pesos(f[2]), 736, y); g.textAlign = "left";
    g.fillStyle = "rgba(255,255,255,.07)"; g.fillRect(44, y + 16, 692, 1);
  });
  return textura(c);
}
const libro = new THREE.Mesh(new THREE.PlaneGeometry(LW, LH), new THREE.MeshBasicMaterial({ map: texLibro(), transparent: true, depthWrite: false, toneMapped: false }));
scene.add(libro);
const filaY = (i) => LH / 2 - (200 + i * 49) / 1000 * LH + 0.04;   // y del renglón i en el panel

// Fragmentos del XML que vuelan mientras los papeles se vuelven datos.
const XML = ['<cbc:TaxAmount currencyID="COP">161500.00</cbc:TaxAmount>', "<cac:InvoiceLine>", '<cbc:Percent>19.00</cbc:Percent>',
  "<cbc:UUID schemeName=\"CUFE-SHA384\">7c1f…</cbc:UUID>", "<cbc:PayableAmount>1011500.00</cbc:PayableAmount>",
  "<cac:AccountingSupplierParty>", "<cbc:IssueDate>2026-10-28</cbc:IssueDate>", "<cac:WithholdingTaxTotal>",
  "<cbc:LineExtensionAmount>850000.00</cbc:LineExtensionAmount>", "<cbc:InvoiceTypeCode>01</cbc:InvoiceTypeCode>"];
const xmls = XML.map((s, i) => {
  const [c, g] = lienzo(1024, 64);
  g.font = `600 30px monospace`; g.fillStyle = i % 3 === 0 ? "#e6c483" : i % 3 === 1 ? "#37d38f" : "#cfd6e4";
  g.fillText(s, 8, 44);
  const w = Math.min(1024, g.measureText(s).width + 16);
  const sp = new THREE.Sprite(new THREE.SpriteMaterial({ map: textura(c), transparent: true, depthWrite: false, fog: false }));
  sp.scale.set(2.6, 2.6 / 16, 1); sp.center.set(0.5 * w / 1024, 0.5);
  sp.userData = { a: R() * 6.28, r: 1.6 + R() * 1.6, y: (R() - 0.5) * 4.2, v: 0.25 + R() * 0.3, d: R() * 0.12 };
  scene.add(sp); return sp;
});

// ---------------------------------------------------------------- partículas (el hilo de toda la historia)
const N = lento ? 1400 : movil ? 2200 : 4200;
const pos = new Float32Array(N * 3), col = new Float32Array(N * 3);
const P = {
  papel: new Int16Array(N), u: new Float32Array(N), v: new Float32Array(N), del: new Float32Array(N),
  B: new Float32Array(N * 3), C: new Float32Array(N * 3), D: new Float32Array(N * 3), E: new Float32Array(N * 3),
  cBase: new Float32Array(N * 3), cLogo: new Float32Array(N * 3), ruido: new Float32Array(N * 3),
};
const paleta = [new THREE.Color("#37d38f"), new THREE.Color("#e6c483"), new THREE.Color("#eef1f6"), new THREE.Color("#7fe6b5")];
const filasDato = FILAS.map((f, i) => i).filter((i) => FILAS[i][2] !== 1);
for (let i = 0; i < N; i++) {
  P.papel[i] = i % NP; P.u[i] = R() - 0.5; P.v[i] = R() - 0.5; P.del[i] = R() * 0.16;
  const a = i * 2.39996, r = 1.3 + R() * 1.5;
  P.B[i * 3] = r * Math.cos(a); P.B[i * 3 + 1] = (R() - 0.5) * 4.6; P.B[i * 3 + 2] = r * Math.sin(a);
  const fi = filasDato[i % filasDato.length];
  P.C[i * 3] = -LW / 2 + 0.25 + R() * (LW - 0.5); P.C[i * 3 + 1] = filaY(fi) + (R() - 0.5) * 0.05; P.C[i * 3 + 2] = 0.03;
  P.D[i * 3] = (R() - 0.5) * 22; P.D[i * 3 + 1] = (R() - 0.5) * 16; P.D[i * 3 + 2] = -14 + R() * 13;
  const k = paleta[i % 7 === 0 ? 1 : i % 5 === 0 ? 2 : i % 3 === 0 ? 3 : 0];
  P.cBase[i * 3] = k.r; P.cBase[i * 3 + 1] = k.g; P.cBase[i * 3 + 2] = k.b;
  P.ruido[i * 3] = R() - 0.5; P.ruido[i * 3 + 1] = R() - 0.5; P.ruido[i * 3 + 2] = R() - 0.5;
  P.E[i * 3] = P.D[i * 3]; P.E[i * 3 + 1] = P.D[i * 3 + 1]; P.E[i * 3 + 2] = P.D[i * 3 + 2];
  P.cLogo[i * 3] = k.r; P.cLogo[i * 3 + 1] = k.g; P.cLogo[i * 3 + 2] = k.b;
}
col.set(P.cBase);
const geoP = new THREE.BufferGeometry();
geoP.setAttribute("position", new THREE.BufferAttribute(pos, 3).setUsage(THREE.DynamicDrawUsage));
geoP.setAttribute("color", new THREE.BufferAttribute(col, 3).setUsage(THREE.DynamicDrawUsage));
const chispa = (() => {
  const [c, g] = lienzo(64, 64), gr = g.createRadialGradient(32, 32, 0, 32, 32, 32);
  gr.addColorStop(0, "rgba(255,255,255,1)"); gr.addColorStop(0.35, "rgba(255,255,255,.8)"); gr.addColorStop(1, "rgba(255,255,255,0)");
  g.fillStyle = gr; g.fillRect(0, 0, 64, 64); return textura(c);
})();
const matP = new THREE.PointsMaterial({ size: 0.06, map: chispa, vertexColors: true, transparent: true, depthWrite: false, blending: THREE.AdditiveBlending, opacity: 0 });
const puntos = new THREE.Points(geoP, matP); puntos.frustumCulled = false; scene.add(puntos);

// ---------------------------------------------------------------- 2 · celular con WhatsApp
const TW = 2.25, TH = 4.6;
const tel = new THREE.Group(); scene.add(tel);
{
  const cuerpo = new THREE.Mesh(new THREE.ExtrudeGeometry(formaRedonda(TW + 0.16, TH + 0.16, 0.34), { depth: 0.16, bevelEnabled: true, bevelThickness: 0.04, bevelSize: 0.04, bevelSegments: 3, curveSegments: 10 }),
    new THREE.MeshStandardMaterial({ color: 0x1a1f2b, metalness: 0.85, roughness: 0.28 }));
  cuerpo.position.z = -0.2; tel.add(cuerpo);
}
const [cvTel, gTel] = lienzo(540, 1104);
const texTel = textura(cvTel);
const pantalla = new THREE.Mesh(new THREE.ShapeGeometry(formaRedonda(TW, TH, 0.28)), new THREE.MeshBasicMaterial({ map: texTel, toneMapped: false }));
{ const uv = pantalla.geometry.attributes.uv, p = pantalla.geometry.attributes.position; for (let i = 0; i < uv.count; i++) uv.setXY(i, p.getX(i) / TW + 0.5, p.getY(i) / TH + 0.5); }
pantalla.position.z = 0.005; tel.add(pantalla);
const CHAT = [
  ["yo", "doc", "factura_ferreteria.pdf"],
  ["yo", "txt", "Le mando la factura de la ferretería 🙌"],
  ["ia", "txt", "Recibida ✅ Ferretería de ejemplo S.A.S."],
  ["ia", "txt", "Causada en compras:\nBase $850.000\nIVA 19 % $161.500\nRetención 2,5 % $21.250"],
  ["yo", "txt", "¿Y cuánto IVA llevo este bimestre?"],
  ["ia", "txt", "Le dejo el borrador del 300 listo para revisar 📊"],
];
function partir(g, txt, max) {
  const out = [];
  txt.split("\n").forEach((par) => {
    let l = "";
    par.split(" ").forEach((w) => { const p = l ? l + " " + w : w; if (g.measureText(p).width > max && l) { out.push(l); l = w; } else l = p; });
    out.push(l);
  });
  return out;
}
function burbuja(g, m, y) {
  const yo = m[0] === "yo";
  g.font = `500 29px ${FF}`;
  const lineas = m[1] === "doc" ? ["📄 " + m[2], "PDF · 1 página"] : partir(g, m[2], 380);
  const w = Math.min(430, Math.max(...lineas.map((l) => g.measureText(l).width)) + 44), h = lineas.length * 38 + 34;
  const x = yo ? 540 - 22 - w : 22;
  g.fillStyle = yo ? "#005c4b" : "#1f2c34"; redondo(g, x, y, w, h, 18); g.fill();
  lineas.forEach((l, i) => { g.fillStyle = i && m[1] === "doc" ? "#9fb3ad" : "#e9edef"; g.font = (i === 0 && m[1] === "doc" ? "700 " : "500 ") + (i && m[1] === "doc" ? "22px " : "29px ") + FF; g.fillText(l, x + 22, y + 46 + i * 38); });
  g.fillStyle = "#8696a0"; g.font = `500 15px ${FF}`; g.fillText(yo ? "9:41 ✓✓" : "9:41", x + w - (yo ? 72 : 46), y + h - 9);
  return h + 16;
}
let ultimoChat = "";
function dibujarChat(n, escribe, t) {
  const firma = n + "|" + (escribe ? Math.floor(t * 4) % 3 : "-");
  if (firma === ultimoChat) return; ultimoChat = firma;
  const g = gTel;
  g.fillStyle = "#0b141a"; g.fillRect(0, 0, 540, 1104);
  g.fillStyle = "rgba(255,255,255,.025)";
  for (let i = 0; i < 40; i++) g.fillRect((i * 97) % 540, (i * 173) % 1104, 18, 18);
  g.fillStyle = "#1f2c34"; g.fillRect(0, 0, 540, 150);
  g.fillStyle = "#37d38f"; g.beginPath(); g.arc(70, 100, 30, 0, 7); g.fill();
  g.fillStyle = "#04130b"; g.font = `800 28px ${FF}`; g.textAlign = "center"; g.fillText("J", 70, 110); g.textAlign = "left";
  g.fillStyle = "#e9edef"; g.font = `700 30px ${FF}`; g.fillText("Jarvis · Tributando", 116, 94);
  g.fillStyle = "#8696a0"; g.font = `500 21px ${FF}`; g.fillText(escribe ? "escribiendo…" : "Contador Auxiliar IA · en línea", 116, 122);
  g.fillStyle = "#e9edef"; g.font = `600 19px ${FF}`; g.fillText("9:41", 40, 40);
  let y = 180;
  for (let i = 0; i < n; i++) y += burbuja(g, CHAT[i], y);
  if (escribe) {
    g.fillStyle = "#1f2c34"; redondo(g, 22, y, 110, 54, 18); g.fill();
    for (let k = 0; k < 3; k++) { g.fillStyle = k === Math.floor(t * 4) % 3 ? "#e9edef" : "#5d6b73"; g.beginPath(); g.arc(52 + k * 25, y + 27, 7, 0, 7); g.fill(); }
  }
  g.fillStyle = "#1f2c34"; redondo(g, 18, 1010, 420, 70, 35); g.fill();
  g.fillStyle = "#8696a0"; g.font = `500 22px ${FF}`; g.fillText("Mensaje", 50, 1053);
  g.fillStyle = "#00a884"; g.beginPath(); g.arc(486, 1045, 35, 0, 7); g.fill();
  texTel.needsUpdate = true;
}
dibujarChat(0, false, 0);

// ---------------------------------------------------------------- 3 · declaraciones + sello
const FORMS = [["Formulario 300", "Impuesto sobre las ventas · IVA", "300"], ["Formulario 350", "Retención en la fuente", "350"], ["Formulario 110", "Renta y complementarios", "110"]];
const FW = 2.1, FH = 2.8, CAS = 24;
const formas = FORMS.map((f, k) => {
  const [c, g] = lienzo(600, 800), tx = textura(c), r = rnd(500 + k);
  const vals = Array.from({ length: CAS }, () => (5 + Math.floor(r() * 400)) * 13000);
  const m = new THREE.Mesh(new THREE.PlaneGeometry(FW, FH), new THREE.MeshStandardMaterial({ map: tx, roughness: 0.7, transparent: true, side: THREE.DoubleSide }));
  m.userData = { c, g, tx, vals, lleno: -1, k };
  scene.add(m); return m;
});
function dibujarForma(m, lleno) {
  const q = Math.round(lleno * CAS); if (q === m.userData.lleno) return; m.userData.lleno = q;
  const { g, tx, vals, k } = m.userData, f = FORMS[k];
  g.fillStyle = "#f7f6f2"; g.fillRect(0, 0, 600, 800);
  g.fillStyle = "#0f1626"; g.fillRect(0, 0, 600, 118);
  g.fillStyle = "#37d38f"; g.fillRect(0, 118, 600, 6);
  g.fillStyle = "#e6c483"; g.font = `800 17px ${FF}`; g.fillText("BORRADOR DESDE SUS LIBROS", 30, 42);
  g.fillStyle = "#ffffff"; g.font = `800 38px ${FF}`; g.fillText(f[0], 30, 86);
  g.fillStyle = "#59606b"; g.font = `700 20px ${FF}`; g.fillText(f[1], 30, 160);
  for (let i = 0; i < CAS; i++) {
    const cx = 30 + (i % 3) * 184, cy = 186 + Math.floor(i / 3) * 70;
    g.strokeStyle = "#d4cfc3"; g.lineWidth = 2; g.strokeRect(cx, cy, 172, 56);
    g.fillStyle = "#8a8f99"; g.font = `700 13px ${FF}`; g.fillText(String(27 + i * (k + 2)), cx + 8, cy + 18);
    if (i < q) { g.fillStyle = "#1b2333"; g.font = `700 19px ${FF}`; g.textAlign = "right"; g.fillText(pesos(vals[i]).replace("$", ""), cx + 162, cy + 45); g.textAlign = "left"; }
  }
  tx.needsUpdate = true;
}
formas.forEach((m) => dibujarForma(m, 0));
const texSello = (() => {
  const [c, g] = lienzo(640, 240);
  g.strokeStyle = "#37d38f"; g.lineWidth = 14; redondo(g, 12, 12, 616, 216, 30); g.stroke();
  g.lineWidth = 4; redondo(g, 32, 32, 576, 176, 20); g.stroke();
  g.fillStyle = "#37d38f"; g.font = `800 86px ${FF}`; g.textAlign = "center"; g.fillText("PRESENTADA", 320, 152);
  g.font = `800 22px ${FF}`; g.fillText("✓ A TIEMPO · SIN SANCIÓN", 320, 196);
  return textura(c);
})();
const sellos = formas.map(() => {
  const s = new THREE.Mesh(new THREE.PlaneGeometry(1.8, 0.675), new THREE.MeshBasicMaterial({ map: texSello, transparent: true, depthWrite: false, toneMapped: false }));
  scene.add(s); return s;
});
// Casillas que vuelan y se pegan a cada formulario.
const NC = CAS * 3;
const casillas = new THREE.InstancedMesh(new THREE.BoxGeometry(0.5, 0.17, 0.04), new THREE.MeshStandardMaterial({ color: 0xeef1f6, emissive: 0x37d38f, emissiveIntensity: 0.35, roughness: 0.4 }), NC);
casillas.frustumCulled = false; scene.add(casillas);
const casIni = Array.from({ length: NC }, () => new THREE.Vector3((R() - 0.5) * 9, (R() - 0.5) * 9, 2 + R() * 4));
const casRot = Array.from({ length: NC }, () => new THREE.Euler(R() * 6, R() * 6, R() * 6));
const casDel = Array.from({ length: NC }, () => R() * 0.18);

// ---------------------------------------------------------------- 4 · módulos que encajan (capas)
const MW = 3.1, MD = 2.0, MH = 0.3;
const geoCapa = new THREE.ExtrudeGeometry(formaRedonda(MW, MD, 0.22), { depth: MH, bevelEnabled: true, bevelThickness: 0.03, bevelSize: 0.03, bevelSegments: 2, curveSegments: 8 });
geoCapa.rotateX(-Math.PI / 2); geoCapa.translate(0, -MH / 2, 0);
const capas = (CFG.modulos || []).map((mod, i) => {
  const g0 = new THREE.Group();
  const cuerpo = new THREE.Mesh(geoCapa, new THREE.MeshStandardMaterial({ color: 0x141b2b, metalness: 0.55, roughness: 0.32, emissive: new THREE.Color(mod.c), emissiveIntensity: 0.08 }));
  g0.add(cuerpo);
  const [c, g] = lienzo(620, 400);
  g.fillStyle = "#101828"; redondo(g, 0, 0, 620, 400, 44); g.fill();
  g.strokeStyle = mod.c; g.lineWidth = 6; redondo(g, 6, 6, 608, 388, 40); g.stroke();
  g.fillStyle = mod.c; redondo(g, 40, 54, 70, 70, 18); g.fill();
  g.fillStyle = "#070a12"; g.font = `800 40px ${FF}`; g.textAlign = "center"; g.fillText(String(i + 1), 75, 104); g.textAlign = "left";
  g.fillStyle = "#ffffff"; g.font = `800 ${mod.n.length > 12 ? 50 : 62}px ${FF}`; g.fillText(mod.n, 40, 220);
  g.fillStyle = "#b7bfcc"; g.font = `600 30px ${FF}`; g.fillText(mod.s, 40, 280);
  g.fillStyle = mod.c; g.fillRect(40, 320, 120, 8);
  const tapa = new THREE.Mesh(new THREE.PlaneGeometry(MW - 0.04, MD - 0.04), new THREE.MeshBasicMaterial({ map: textura(c), toneMapped: false, transparent: true }));
  tapa.rotation.x = -Math.PI / 2; tapa.position.y = MH / 2 + 0.035; g0.add(tapa);
  const borde = new THREE.Mesh(new THREE.BoxGeometry(MW - 0.3, 0.035, 0.035), new THREE.MeshBasicMaterial({ color: new THREE.Color(mod.c), toneMapped: false }));
  borde.position.set(0, 0, MD / 2 + 0.035); g0.add(borde);
  g0.userData = { lado: i % 2 ? 1 : -1, giro: (R() - 0.5) * 0.9, mats: [cuerpo.material, tapa.material, borde.material] };
  return g0;
});
const pila = new THREE.Group(); capas.forEach((c) => pila.add(c)); scene.add(pila);

// ---------------------------------------------------------------- 5 · logo de partículas
const insignia = new THREE.Mesh(new THREE.CircleGeometry(1, 64), new THREE.MeshBasicMaterial({ transparent: true, opacity: 0, toneMapped: false, depthWrite: false }));
scene.add(insignia);
let logoListo = false, logoAncho = 4.4;
new THREE.TextureLoader().load(CFG.logo || "/static/img/logo-tributando.png", (t) => {
  t.colorSpace = THREE.SRGBColorSpace; insignia.material.map = t; insignia.material.needsUpdate = true;
  const img = t.image, S = 240, [c, g] = lienzo(S, S);
  g.drawImage(img, 0, 0, S, S);
  const d = g.getImageData(0, 0, S, S).data, cand = [];
  for (let y = 0; y < S; y++) for (let x = 0; x < S; x++) {
    const o = (y * S + x) * 4, l = 0.3 * d[o] + 0.59 * d[o + 1] + 0.11 * d[o + 2];
    if (d[o + 3] > 200 && l > 105) cand.push([x, y, d[o] / 255, d[o + 1] / 255, d[o + 2] / 255]);
  }
  if (!cand.length) return;
  const r = rnd(9);
  for (let i = 0; i < N; i++) {
    const p = cand[Math.floor(r() * cand.length)];
    P.E[i * 3] = ((p[0] + r() - 0.5) / S - 0.5); P.E[i * 3 + 1] = -((p[1] + r() - 0.5) / S - 0.5); P.E[i * 3 + 2] = (r() - 0.5) * 0.02;
    const cc = new THREE.Color(p[2], p[3], p[4]).convertSRGBToLinear();
    P.cLogo[i * 3] = cc.r * 1.4; P.cLogo[i * 3 + 1] = cc.g * 1.4; P.cLogo[i * 3 + 2] = cc.b * 1.4;
  }
  logoListo = true;
});

// ---------------------------------------------------------------- disposición (celular / computador)
let W = 1, H = 1, ancho = false, dist = 12;
function medir() {
  W = innerWidth; H = innerHeight; ancho = W > 860;
  renderer.setSize(W, H, false);
  camera.aspect = W / H;
  const a = camera.aspect, k = 2 * Math.tan(THREE.MathUtils.degToRad(camera.fov / 2));
  dist = ancho ? Math.max(10.5, 6.2 / (k * a * 0.56)) : Math.max(14.5, 4.7 / (k * a));   // celulares bajitos: mismo alto útil
  logoAncho = ancho ? 4.3 : 4.3;
  matP.size = ancho ? 0.045 : 0.065;
  camera.updateProjectionMatrix();
}
medir();
addEventListener("resize", () => { if (Math.abs(innerWidth - W) > 2 || Math.abs(innerHeight - H) > 140) medir(); });

// ---------------------------------------------------------------- un cuadro de la película
const v3 = new THREE.Vector3(), q = new THREE.Quaternion(), e3 = new THREE.Euler(), m4 = new THREE.Matrix4(), s3 = new THREE.Vector3();
let primero = true;
function render(c, t) {
  // Cámara: deriva suave y encuadre según capítulo (texto arriba en celular, a la izquierda en PC).
  const respira = Math.sin(t * 0.25) * 0.12;
  camera.position.set(Math.sin(t * 0.13) * 0.25, 0.2 + respira * 0.5, dist * (1 - 0.06 * sm(1.6, 2, c) + 0.06 * sm(2, 2.3, c)));
  camera.lookAt(0, 0, 0);
  if (ancho) camera.setViewOffset(W, H, -W * 0.21, 0, W, H);
  else camera.setViewOffset(W, H, 0, -H * mix(0.13, -0.13, sm(4.85, 5.3, c)), W, H);

  // 0–1 · papeles
  const exp = sm(0.95, 1.5, c), fadeP = 1 - sm(1.28, 1.52, c);
  papeles.visible = fadeP > 0.001;
  if (papeles.visible) {
    papeles.rotation.y = Math.sin(t * 0.2) * 0.15 + c * 0.35;
    papeles.position.y = -0.2 + c * 0.25;
    const abre = 1 + 0.55 * sm(0.15, 1, c);
    papeles.children.forEach((m, i) => {
      const u = m.userData, b = u.base;
      m.position.set(b.x + u.dir.x * u.dist * exp, b.y * abre + Math.sin(t * 0.9 + u.fase) * 0.06 + u.dir.y * u.dist * exp, b.z + u.dir.z * u.dist * exp);
      m.rotation.set(u.rot.x + Math.sin(t * 0.6 + u.fase) * 0.04 + u.giro.x * exp, u.rot.y + u.giro.y * exp, u.rot.z + u.giro.z * exp);
      m.material.opacity = fadeP;
    });
  }

  // 1–2 · libro
  const libIn = sm(1.72, 1.98, c), libOut = sm(2.05, 2.35, c);
  libro.visible = libIn > 0.001 && libOut < 0.999;
  libro.material.opacity = libIn * (1 - libOut);
  libro.position.set(0, 0.05 + libOut * 2.5, -libOut * 6);
  libro.rotation.set(-0.12 + Math.sin(t * 0.4) * 0.03, Math.sin(t * 0.3) * 0.12 - 0.15 * (1 - libIn), 0);
  libro.scale.setScalar(0.92 + 0.08 * libIn);
  libro.updateMatrixWorld();

  // XML
  const xIn = sm(1.38, 1.55, c), xOut = 1 - sm(1.8, 1.98, c);
  xmls.forEach((sp) => {
    const u = sp.userData, o = Math.min(sm(1.38 + u.d, 1.55 + u.d, c), xOut);
    sp.visible = o > 0.01; if (!sp.visible) return;
    const a = u.a + t * u.v + c * 2;
    sp.position.set(Math.cos(a) * u.r, u.y + Math.sin(t * 0.5 + u.a) * 0.2, Math.sin(a) * u.r * 0.6 + 0.8);
    sp.material.opacity = o * 0.95;
  });

  // Partículas: papel → nube XML → renglones del libro → polvo → logo
  const actP = c > 1.2;
  if (actP) {
    if (fadeP > 0) scene.updateMatrixWorld();
    const giro = t * 0.35 + c * 1.5, cg = Math.cos(giro), sg = Math.sin(giro);
    const lw = libro.matrixWorld.elements, ls = logoAncho;
    const logoY = 0.1, recog = sm(4.84, 5.5, c);
    for (let i = 0; i < N; i++) {
      const i3 = i * 3, d = P.del[i];
      const s1 = sm(1.26 + d, 1.5 + d, c), s2 = sm(1.6 + d, 1.88 + d, c), s3_ = sm(2.02 + d, 2.4 + d, c), s4 = sm(4.84 + d, 5.3 + d, c);
      let x, y, z;
      if (s1 < 1) {
        const m = papeles.children[P.papel[i]];
        v3.set(P.u[i] * PW, P.v[i] * PH, 0.02).applyMatrix4(m.matrixWorld);
        x = v3.x; y = v3.y; z = v3.z;
      }
      if (s1 > 0 && s2 < 1) {
        const bx = P.B[i3] * cg - P.B[i3 + 2] * sg, bz = P.B[i3] * sg + P.B[i3 + 2] * cg, by = P.B[i3 + 1];
        if (s1 < 1) { x = mix(x, bx, s1); y = mix(y, by, s1); z = mix(z, bz, s1); } else { x = bx; y = by; z = bz; }
      }
      if (s2 > 0 && s3_ < 1) {
        const cx = P.C[i3], cy = P.C[i3 + 1], cz = P.C[i3 + 2];
        const wx = lw[0] * cx + lw[4] * cy + lw[8] * cz + lw[12], wy = lw[1] * cx + lw[5] * cy + lw[9] * cz + lw[13], wz = lw[2] * cx + lw[6] * cy + lw[10] * cz + lw[14];
        if (s2 < 1) { x = mix(x, wx, s2); y = mix(y, wy, s2); z = mix(z, wz, s2); } else { x = wx; y = wy; z = wz; }
      }
      if (s3_ > 0) {
        const dx = P.D[i3] + Math.sin(t * 0.2 + i) * 0.3, dy = P.D[i3 + 1] + Math.cos(t * 0.17 + i * 0.5) * 0.3, dz = P.D[i3 + 2];
        if (s3_ < 1) { x = mix(x, dx, s3_); y = mix(y, dy, s3_); z = mix(z, dz, s3_); } else { x = dx; y = dy; z = dz; }
      }
      if (s4 > 0) {
        const ex = P.E[i3] * ls, ey = P.E[i3 + 1] * ls + logoY, ez = P.E[i3 + 2] + 0.05;
        x = mix(x, ex, s4); y = mix(y, ey, s4); z = mix(z, ez, s4);
      }
      const turb = Math.sin(Math.PI * s1) * 0.5 + Math.sin(Math.PI * s2) * 0.35 + Math.sin(Math.PI * s4) * 0.9;
      pos[i3] = x + P.ruido[i3] * turb; pos[i3 + 1] = y + P.ruido[i3 + 1] * turb; pos[i3 + 2] = z + P.ruido[i3 + 2] * turb;
      if (recog > 0 && logoListo) {
        col[i3] = mix(P.cBase[i3], P.cLogo[i3], s4); col[i3 + 1] = mix(P.cBase[i3 + 1], P.cLogo[i3 + 1], s4); col[i3 + 2] = mix(P.cBase[i3 + 2], P.cLogo[i3 + 2], s4);
      }
    }
    geoP.attributes.position.needsUpdate = true;
    if (recog > 0) geoP.attributes.color.needsUpdate = true;
  }
  puntos.visible = actP;
  matP.opacity = sm(1.2, 1.32, c) * mix(1, 0.4, sm(2.1, 2.5, c)) + 0.6 * sm(4.84, 5.25, c) * (1 - 0.35 * sm(5.6, 5.9, c));

  // 2–3 · celular
  const telIn = sm(2.0, 2.32, c), telOut = sm(2.86, 3.1, c);
  tel.visible = telIn > 0.001 && telOut < 0.999;
  if (tel.visible) {
    tel.position.set(-telOut * 5, (ancho ? 0 : -1.45) - 7 * (1 - telIn) + Math.sin(t * 0.8) * 0.05 + telOut * 1.5, telIn * (ancho ? 0.6 : 0.9));
    tel.scale.setScalar(ancho ? 1 : 0.86);
    tel.rotation.set(-0.08 + 0.5 * (1 - telIn), (1 - telIn) * 2.2 - 0.18 + Math.sin(t * 0.4) * 0.06 - telOut * 1.4, (1 - telIn) * 0.3);
    const n = Math.floor(lin(2.3, 2.84, c) * (CHAT.length + 0.999));
    const sig = CHAT[Math.min(n, CHAT.length - 1)];
    const escribe = n < CHAT.length && sig && sig[0] === "ia" && lin(2.3, 2.84, c) * (CHAT.length + 0.999) - n > 0.35;
    dibujarChat(Math.min(n, CHAT.length), escribe, t);
  }

  // 3–4 · declaraciones
  const fIn = sm(3.0, 3.25, c), fOut = sm(3.86, 4.0, c);
  const visF = fIn > 0.001 && fOut < 0.999;
  formas.forEach((m, k) => {
    m.visible = visF; sellos[k].visible = false;
    if (!visF) return;
    const px = ancho ? (k - 1) * 1.3 : (k - 1) * 0.8, py = ancho ? (1 - k) * 0.5 : (1 - k) * 0.95 - 1.05, pz = k * 0.35;
    const ent = sm(3.0 + k * 0.05, 3.25 + k * 0.05, c);
    m.position.set(px + (1 - ent) * (k - 1) * 3, py - (1 - ent) * 4 - fOut * 5, pz - fOut * 3);
    m.rotation.set(-0.05 - (1 - ent) * 1.2, -0.14 + Math.sin(t * 0.5 + k) * 0.03, 0.04 * (k - 1));
    m.scale.setScalar(ancho ? 1 : 0.88);
    m.material.opacity = ent * (1 - fOut);
    dibujarForma(m, sm(3.22 + k * 0.08, 3.55 + k * 0.08, c));
    const sl = sellos[k], tS = 3.6 + k * 0.08, golpe = lin(tS, tS + 0.05, c);
    sl.visible = golpe > 0 && fOut < 0.999;
    if (sl.visible) {
      m.updateMatrixWorld();
      sl.position.set(0.3, 0.62, 0.04).applyMatrix4(m.matrixWorld);
      sl.quaternion.copy(m.quaternion); sl.rotateZ(0.22 - k * 0.05);
      sl.scale.setScalar(mix(2.6, 1, back(golpe) * golpe + (1 - golpe) * 0) * (1 + 0.02 * Math.sin(t * 3)));
      sl.material.opacity = Math.min(1, golpe * 2) * (1 - fOut);
    }
  });
  casillas.visible = visF && c < 3.75;
  if (casillas.visible) {
    for (let i = 0; i < NC; i++) {
      const k = Math.floor(i / CAS), j = i % CAS, m = formas[k];
      const a = sm(3.04 + casDel[i] + k * 0.05, 3.32 + casDel[i] + k * 0.05, c);
      m.updateMatrixWorld();
      v3.set(-FW / 2 + (30 + (j % 3) * 184 + 86) / 600 * FW, FH / 2 - (186 + Math.floor(j / 3) * 70 + 28) / 800 * FH, 0.03).applyMatrix4(m.matrixWorld);
      v3.lerpVectors(casIni[i], v3, a);
      e3.set(casRot[i].x * (1 - a) + m.rotation.x * a, casRot[i].y * (1 - a) + m.rotation.y * a, casRot[i].z * (1 - a) + m.rotation.z * a);
      const llegada = sm(3.22 + k * 0.08 + (j / CAS) * 0.33, 3.3 + k * 0.08 + (j / CAS) * 0.33, c);
      const s = (a > 0.001 ? 1 : 0) * (1 - llegada) * (FW / 1.9 / 1.9);
      s3.set(s, s, s);
      casillas.setMatrixAt(i, m4.compose(v3, q.setFromEuler(e3), s3));
    }
    casillas.instanceMatrix.needsUpdate = true;
  }

  // 4–5 · módulos en capas
  const mIn = sm(3.9, 4.08, c), mOut = sm(4.8, 5.0, c);
  pila.visible = capas.length && mIn > 0.001 && mOut < 0.999;
  if (pila.visible) {
    const n = capas.length;
    pila.position.set(0, (ancho ? -0.15 : -1.25) - mOut * 1.2, -mOut * 3);
    pila.rotation.set(0.5 - 0.12 * sm(4.3, 4.85, c), -0.6 + (c - 4) * 0.9 + Math.sin(t * 0.3) * 0.05, 0);
    pila.scale.setScalar((ancho ? 1 : 0.95) * (1 - 0.4 * mOut));
    capas.forEach((g0, i) => {
      const u = g0.userData, j = i - (n - 1) / 2;
      const junta = back(sm(4.3 + i * 0.06, 4.55 + i * 0.06, c));
      const sep = ancho ? 0.95 : 0.85;
      g0.position.set(u.lado * 0.75 * (1 - junta) * (1 - mIn * 0.3), j * mix(sep, MH + 0.07, junta) + (1 - mIn) * j * 2, 0);
      g0.rotation.y = u.giro * (1 - junta);
      const o = mIn * (1 - mOut);
      u.mats.forEach((mt) => { mt.transparent = o < 1; mt.opacity = o; });
    });
  }

  // 5 · insignia detrás del logo de partículas
  const ins = sm(5.3, 5.6, c);
  insignia.visible = ins > 0.001;
  insignia.material.opacity = ins * 0.95;
  insignia.position.set(0, 0.1, -0.05);
  insignia.scale.setScalar(logoAncho * 0.5 * (0.96 + 0.04 * ins));
  puntos.rotation.y = 0;

  luzOro.intensity = 30 + 10 * Math.sin(t * 0.7);
  renderer.render(scene, camera);
  if (primero) { primero = false; const p = document.getElementById("poster"); if (p) p.classList.add("fuera"); }
}

window.__cine3d = { render };
if (window.__cineCaptura) window.__cineCaptura(0);
