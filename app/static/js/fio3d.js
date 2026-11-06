/* Mascote 3D da Gamificação (Three.js) no estilo do mascote do Claude: criatura laranja arredondada, olhos pretos, bracinhos e perninhas,
   de camisa verde com a logo da Conexão e prancheta anotando. Fio3D.init(canvas, urlLogo) -> true/false (false = sem WebGL: usa o desenho 2D). setTriste(bool), falar(bool). */
(function () {
  var T = window.THREE; if (!T) return;
  var estado = { triste: false, falando: false };
  function mat(cor, r, c) { return new T.MeshPhysicalMaterial({ color: cor, roughness: r === undefined ? 0.55 : r, metalness: 0, clearcoat: c || 0.15, clearcoatRoughness: 0.5 }); }
  function V(x, y, z) { return new T.Vector3(x, y, z); }
  function caixa(w, h, d, raio, cor, r) {            // caixa de cantos arredondados (extrusao com chanfro)
    var rr = Math.min(raio, w / 2 - 0.01, h / 2 - 0.01), sh = new T.Shape(), x = -w / 2, y = -h / 2;
    sh.moveTo(x + rr, y); sh.lineTo(x + w - rr, y); sh.quadraticCurveTo(x + w, y, x + w, y + rr); sh.lineTo(x + w, y + h - rr); sh.quadraticCurveTo(x + w, y + h, x + w - rr, y + h);
    sh.lineTo(x + rr, y + h); sh.quadraticCurveTo(x, y + h, x, y + h - rr); sh.lineTo(x, y + rr); sh.quadraticCurveTo(x, y, x + rr, y);
    var bv = Math.min(raio * 0.8, d / 2 - 0.01), geo = new T.ExtrudeGeometry(sh, { depth: d - 2 * bv, bevelEnabled: true, bevelThickness: bv, bevelSize: bv * 0.9, bevelSegments: 6, curveSegments: 10 });
    geo.translate(0, 0, -(d - 2 * bv) / 2); var m = new T.Mesh(geo, mat(cor, r)); return m;
  }
  function init(canvas, urlLogo) {
    var renderer;
    try { renderer = new T.WebGLRenderer({ canvas: canvas, antialias: true, alpha: true }); } catch (e) { return false; }
    if (!renderer.getContext()) return false;
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    var W = canvas.clientWidth || 240, H = canvas.clientHeight || 300; renderer.setSize(W, H, false);
    var scene = new T.Scene(), cam = new T.PerspectiveCamera(28, W / H, 0.1, 60); cam.position.set(0.8, 1.6, 12.8); cam.lookAt(0.1, 1.3, 0);
    scene.add(new T.HemisphereLight(0xffffff, 0xb9c6e4, 0.85));
    var sol = new T.DirectionalLight(0xffffff, 1.0); sol.position.set(3.5, 6, 6); scene.add(sol);
    var rim = new T.DirectionalLight(0xbfdbfe, 0.5); rim.position.set(-4, 3, -4); scene.add(rim);
    var C = { laranja: 0xd97757, laranjaEsc: 0xb85a3c, verde: 0x17984a, verdeEsc: 0x0f7a3a, preto: 0x141010, madeira: 0xa4501a };
    var g = new T.Group(); scene.add(g);
    var cs = document.createElement('canvas'); cs.width = cs.height = 128; var cx = cs.getContext('2d'), gr = cx.createRadialGradient(64, 64, 4, 64, 64, 62); gr.addColorStop(0, 'rgba(15,23,42,.45)'); gr.addColorStop(1, 'rgba(15,23,42,0)'); cx.fillStyle = gr; cx.fillRect(0, 0, 128, 128);
    var sombra = new T.Mesh(new T.PlaneGeometry(4.2, 2), new T.MeshBasicMaterial({ map: new T.CanvasTexture(cs), transparent: true, depthWrite: false })); sombra.rotation.x = -Math.PI / 2; sombra.position.y = 0.01; scene.add(sombra);
    var corpo = new T.Group(); g.add(corpo);
    // pernas (4, curtas, como o mascote)
    var pernas = [-0.85, -0.3, 0.3, 0.85].map(function (x, i) { var p = caixa(0.34, 0.62, 0.34, 0.1, C.laranjaEsc, 0.6); p.position.set(x, 0.31, i % 2 ? -0.1 : 0.1); corpo.add(p); return p; });
    // corpo laranja
    var tronco = new T.Group(); tronco.position.y = 1.55; corpo.add(tronco);
    var bloco = caixa(2.7, 1.7, 1.3, 0.38, C.laranja, 0.5); tronco.add(bloco);
    // camisa verde (faixa de baixo) com gola
    var camisa = caixa(2.78, 0.7, 1.38, 0.3, C.verde, 0.7); camisa.position.set(0, -0.5, 0); tronco.add(camisa);
    var gola = caixa(1.0, 0.2, 1.42, 0.08, C.verdeEsc, 0.8); gola.position.set(0, -0.12, 0); tronco.add(gola);
    // logo da Conexao
    var logoTex = new T.TextureLoader().load(urlLogo); logoTex.anisotropy = 4;
    var cracha = caixa(1.34, 0.46, 0.05, 0.05, 0xffffff, 0.5); cracha.position.set(0, -0.46, 0.72); tronco.add(cracha);
    var logo = new T.Mesh(new T.PlaneGeometry(1.24, 0.24), new T.MeshBasicMaterial({ map: logoTex, transparent: true })); logo.position.set(0, -0.46, 0.76); tronco.add(logo);
    // olhos (retangulos pretos arredondados) + brilho
    var olhos = [-0.55, 0.55].map(function (x) { var og = new T.Group(); og.position.set(x, 0.38, 0.67); var e = caixa(0.26, 0.5, 0.1, 0.1, C.preto, 0.2); og.add(e); var b = new T.Mesh(new T.SphereGeometry(0.035, 10, 8), new T.MeshBasicMaterial({ color: 0xffffff })); b.position.set(0.05, 0.14, 0.1); og.add(b); tronco.add(og); return og; });
    var lagrima = new T.Mesh(new T.SphereGeometry(0.07, 12, 10), mat(0x7dd3fc, 0.1, 1)); lagrima.scale.set(1, 1.5, 0.8); lagrima.visible = false; tronco.add(lagrima);
    // bracinhos
    var bracoD = caixa(0.34, 0.34, 0.34, 0.12, C.laranja, 0.5), bracoE = caixa(0.34, 0.34, 0.34, 0.12, C.laranja, 0.5); corpo.add(bracoD, bracoE);
    var ligD = new T.Mesh(new T.CylinderGeometry(0.15, 0.15, 1, 16), mat(C.laranja, 0.5)), ligE = new T.Mesh(new T.CylinderGeometry(0.15, 0.15, 1, 16), mat(C.laranja, 0.5)); corpo.add(ligD, ligE);
    var caneta = new T.Group(); var cc = new T.Mesh(new T.CylinderGeometry(0.03, 0.03, 0.5, 10), mat(0x2563eb, 0.3, 0.6)); caneta.add(cc); var pt = new T.Mesh(new T.ConeGeometry(0.03, 0.09, 10), mat(0x111827)); pt.position.y = -0.29; caneta.add(pt); corpo.add(caneta);
    // prancheta
    var pr = new T.Group(); pr.position.set(-1.05, 1.0, 0.95); pr.rotation.set(-0.15, 0.3, 0.06); corpo.add(pr);
    var tabua = caixa(0.9, 1.15, 0.08, 0.06, C.madeira, 0.6); pr.add(tabua);
    var papC = document.createElement('canvas'); papC.width = 128; papC.height = 160; var pg = papC.getContext('2d'), papT = new T.CanvasTexture(papC);
    var papel = new T.Mesh(new T.PlaneGeometry(0.74, 0.94), new T.MeshBasicMaterial({ map: papT })); papel.position.z = 0.07; pr.add(papel);
    var clipe = caixa(0.34, 0.15, 0.1, 0.04, 0x94a3b8, 0.3); clipe.position.set(0, 0.58, 0.07); pr.add(clipe);
    function desenhaPapel(t) {
      pg.fillStyle = '#fff'; pg.fillRect(0, 0, 128, 160); pg.strokeStyle = '#1d4ed8'; pg.lineWidth = 5; pg.lineCap = 'round';
      var prog = (t * 0.6) % 4;
      for (var i = 0; i < 4; i++) { var p = Math.max(0, Math.min(prog - i, 1)); if (p <= 0) continue; var y = 30 + i * 28, comp = (i === 3 ? 60 : 92 - i * 8) * p; pg.beginPath(); pg.moveTo(18, y); pg.lineTo(18 + comp, y); pg.stroke(); }
      if (prog > 3.6) { pg.strokeStyle = '#16a34a'; pg.lineWidth = 7; pg.beginPath(); pg.moveTo(82, 118); pg.lineTo(92, 130); pg.lineTo(112, 106); pg.stroke(); }
      papT.needsUpdate = true;
    }
    function liga(a, b, cil) { var d = new T.Vector3().subVectors(b, a); cil.position.copy(a).addScaledVector(d, 0.5); cil.quaternion.setFromUnitVectors(V(0, 1, 0), d.clone().normalize()); cil.scale.set(1, d.length(), 1); }
    var alvo = { x: 0, y: 0 }; window.addEventListener('mousemove', function (e) { alvo.y = ((e.clientX / window.innerWidth) - 0.5) * 0.5; alvo.x = ((e.clientY / window.innerHeight) - 0.5) * 0.2; });
    var t0 = performance.now(), proxPisca = 2.2, piscaAte = 0, rodando = true, acenar = 0;
    function quadro() {
      if (!rodando) return; requestAnimationFrame(quadro);
      if (!document.body.contains(canvas)) { rodando = false; return; }
      var t = (performance.now() - t0) / 1000, tri = estado.triste;
      acenar += ((estado.falando && !tri ? 1 : 0) - acenar) * 0.08;
      var pulo = tri ? 0 : Math.max(0, Math.sin(t * 3.2)) * 0.06 * (estado.falando ? 2 : 0.5);
      corpo.position.y = pulo; corpo.rotation.y += ((alvo.y) - corpo.rotation.y) * 0.06; corpo.rotation.x += (alvo.x - corpo.rotation.x) * 0.06;
      tronco.position.y = 1.55 - (tri ? 0.08 : 0) + Math.sin(t * 1.9) * 0.02; tronco.rotation.z = (tri ? 0.05 : 0) + Math.sin(t * 1.1) * 0.015; tronco.scale.set(1, 1 + Math.sin(t * 1.9) * 0.012, 1);
      pernas.forEach(function (p, i) { p.position.y = 0.31 + (i % 2 ? Math.max(0, Math.sin(t * 3.2)) : 0) * 0.04 * (estado.falando ? 1 : 0); });
      if (t > proxPisca) { piscaAte = t + 0.12; proxPisca = t + 2.2 + Math.random() * 2.8; }
      var fechado = t < piscaAte;
      olhos.forEach(function (o, i) { o.scale.y = fechado ? 0.12 : (tri ? 0.75 : 1); o.rotation.z = tri ? (i ? 0.3 : -0.3) : 0; o.position.y = tri ? 0.32 : 0.38; });
      lagrima.visible = tri; if (tri) { var q = (t * 0.7) % 1; lagrima.position.set(-0.55, 0.05 - q * 0.45, 0.72); }
      // braco direito (lado +x): escreve ou acena
      var ombro = V(1.45, 1.35, 0.1);
      var escreve = V(-0.35 + Math.sin(t * 3.2) * 0.12, 0.95 + Math.cos(t * 4.1) * 0.06, 1.05), ac = V(2.0 + Math.sin(t * 7) * 0.15, 2.5, 0.2), cai = V(1.75, 0.95, 0.15);
      var mao = tri ? cai : (acenar > 0.5 ? ac : escreve);
      liga(ombro, mao, ligD); bracoD.position.copy(mao);
      caneta.position.set(mao.x + 0.05, mao.y - 0.12, mao.z + 0.15); caneta.rotation.set(0.35, 0, acenar > 0.5 && !tri ? -0.2 : 0.6);
      // braco esquerdo segura a prancheta
      var ombroE = V(-1.45, 1.35, 0.1), maoE = V(-1.15, 0.85 + Math.sin(t * 1.9) * 0.015, 1.0);
      liga(ombroE, maoE, ligE); bracoE.position.copy(maoE);
      pr.position.y = 1.0 + Math.sin(t * 1.9) * 0.015; desenhaPapel(t);
      sombra.scale.set(1 - pulo * 2, 1, 1);
      renderer.render(scene, cam);
    }
    quadro();
    return true;
  }
  window.Fio3D = { init: init, setTriste: function (v) { estado.triste = !!v; }, falar: function (v) { estado.falando = !!v; } };
})();
