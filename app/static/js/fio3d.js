/* Fio, o mascote 3D da Gamificação (Three.js). Procedural: sem arquivos de modelo. Window.Fio3D.init(canvas, urlLogo) -> true/false (false = sem WebGL: a tela usa o desenho 2D). */
(function () {
  var T = window.THREE; if (!T) return;
  var estado = { triste: false, falando: false };
  function mat(cor, o) { o = o || {}; return new T.MeshPhysicalMaterial({ color: cor, roughness: o.r === undefined ? 0.4 : o.r, metalness: o.m || 0, clearcoat: o.c || 0, clearcoatRoughness: 0.25 }); }
  function membro(mesh, a, b) {            // posiciona uma capsula (eixo Y) entre os pontos a e b
    var d = new T.Vector3().subVectors(b, a), len = d.length();
    mesh.position.copy(a).addScaledVector(d, 0.5);
    mesh.quaternion.setFromUnitVectors(new T.Vector3(0, 1, 0), d.clone().normalize());
    mesh.scale.set(1, Math.max(len - 0.34, 0.05) / 0.5, 1);
  }
  function init(canvas, urlLogo) {
    var renderer;
    try { renderer = new T.WebGLRenderer({ canvas: canvas, antialias: true, alpha: true }); } catch (e) { return false; }
    if (!renderer.getContext()) return false;
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2)); 
    var W = canvas.clientWidth || 200, H = canvas.clientHeight || 250; renderer.setSize(W, H, false);
    var scene = new T.Scene(), cam = new T.PerspectiveCamera(30, W / H, 0.1, 50); cam.position.set(0.15, 2.0, 9.4); cam.lookAt(0, 1.85, 0);
    scene.add(new T.HemisphereLight(0xffffff, 0x8aa4d6, 0.6));
    var sol = new T.DirectionalLight(0xffffff, 1.0); sol.position.set(3.5, 6, 5); scene.add(sol);
    var rim = new T.DirectionalLight(0x7dd3fc, 0.55); rim.position.set(-4, 3, -4); scene.add(rim);

    var cor = { azul: 0x2f86f0, verde: 0x16a34a, jeans: 0x3b66b2, pele: 0xfde0a8, escuro: 0x111827, amarelo: 0xfacc15, branco: 0xffffff, madeira: 0xa4501a };
    var fio = new T.Group(); scene.add(fio);
    // sombra no chao
    var cs = document.createElement('canvas'); cs.width = cs.height = 128; var g = cs.getContext('2d'), gr = g.createRadialGradient(64, 64, 4, 64, 64, 62); gr.addColorStop(0, 'rgba(15,23,42,.45)'); gr.addColorStop(1, 'rgba(15,23,42,0)'); g.fillStyle = gr; g.fillRect(0, 0, 128, 128);
    var sombra = new T.Mesh(new T.PlaneGeometry(3.2, 1.6), new T.MeshBasicMaterial({ map: new T.CanvasTexture(cs), transparent: true, depthWrite: false })); sombra.rotation.x = -Math.PI / 2; sombra.position.y = 0.005; scene.add(sombra);
    var corpo = new T.Group(); fio.add(corpo);
    // pernas (jeans) e sapatos
    [-1, 1].forEach(function (s) {
      var perna = new T.Mesh(new T.CapsuleGeometry(0.25, 0.55, 8, 16), mat(cor.jeans, { r: 0.75 })); perna.position.set(s * 0.27, 0.5, 0); corpo.add(perna);
      var costura = new T.Mesh(new T.CylinderGeometry(0.255, 0.255, 0.02, 24), mat(0x9db8e6, { r: 0.9 })); costura.position.set(s * 0.27, 0.9, 0); corpo.add(costura);
      var sapato = new T.Mesh(new T.CapsuleGeometry(0.2, 0.28, 8, 16), mat(cor.escuro, { r: 0.45, c: 0.4 })); sapato.rotation.x = Math.PI / 2; sapato.position.set(s * 0.27, 0.13, 0.15); sapato.scale.set(1, 1, 0.8); corpo.add(sapato);
    });
    var quadril = new T.Mesh(new T.SphereGeometry(0.62, 32, 24), mat(cor.jeans, { r: 0.75 })); quadril.scale.set(1, 0.5, 0.72); quadril.position.y = 0.98; corpo.add(quadril);
    // camisa verde
    var tronco = new T.Mesh(new T.CapsuleGeometry(0.58, 0.55, 12, 24), mat(cor.verde, { r: 0.6, c: 0.15 })); tronco.scale.set(1, 1, 0.78); tronco.position.y = 1.55; corpo.add(tronco);
    var gola = new T.Mesh(new T.TorusGeometry(0.3, 0.07, 12, 32), mat(0xbbf7d0, { r: 0.6 })); gola.rotation.x = Math.PI / 2; gola.position.set(0, 2.08, 0.04); gola.scale.set(1, 1, 0.8); corpo.add(gola);
    // cracha com a logo
    var logoTex = new T.TextureLoader().load(urlLogo); logoTex.anisotropy = 4;
    var cracha = new T.Mesh(new T.PlaneGeometry(0.98, 0.36), new T.MeshStandardMaterial({ color: 0xffffff, roughness: 0.5 })); cracha.position.set(0, 1.42, 0.462); corpo.add(cracha);
    var logo = new T.Mesh(new T.PlaneGeometry(0.9, 0.174), new T.MeshBasicMaterial({ map: logoTex, transparent: true })); logo.position.set(0, 1.42, 0.466); corpo.add(logo);
    var bordaC = new T.Mesh(new T.PlaneGeometry(1.02, 0.4), new T.MeshBasicMaterial({ color: 0x0f172a })); bordaC.position.set(0, 1.42, 0.459); corpo.add(bordaC);
    // cabeca
    var cab = new T.Group(); cab.position.set(0, 2.72, 0); corpo.add(cab);
    var crani = new T.Mesh(new T.SphereGeometry(0.82, 48, 36), mat(cor.azul, { r: 0.25, c: 0.7 })); crani.scale.set(1.02, 0.94, 0.94); cab.add(crani);
    var olhos = [];
    [-1, 1].forEach(function (s) {
      var og = new T.Group(); og.position.set(s * 0.32, 0.08, 0.66);
      var branco = new T.Mesh(new T.SphereGeometry(0.235, 32, 24), mat(cor.branco, { r: 0.2, c: 0.8 })); branco.scale.set(1, 1.1, 0.7); og.add(branco);
      var pupila = new T.Mesh(new T.SphereGeometry(0.115, 24, 18), mat(0x0b1220, { r: 0.2, c: 1 })); pupila.position.set(0, -0.01, 0.13); og.add(pupila);
      var brilho = new T.Mesh(new T.SphereGeometry(0.035, 12, 10), new T.MeshBasicMaterial({ color: 0xffffff })); brilho.position.set(0.04, 0.04, 0.23); og.add(brilho);
      cab.add(og); olhos.push(og);
    });
    var sobr = [-1, 1].map(function (s) { var b = new T.Mesh(new T.CapsuleGeometry(0.035, 0.22, 4, 8), mat(0x0b1220)); b.rotation.z = Math.PI / 2; b.position.set(s * 0.32, 0.43, 0.7); cab.add(b); return b; });
    var boca = new T.Mesh(new T.TorusGeometry(0.27, 0.04, 10, 28, Math.PI), mat(0x0b1220, { r: 0.4 })); boca.position.set(0, -0.2, 0.74); boca.rotation.z = Math.PI; cab.add(boca);
    var lagrima = new T.Mesh(new T.SphereGeometry(0.06, 12, 10), mat(0x7dd3fc, { r: 0.1, c: 1 })); lagrima.scale.set(1, 1.4, 0.8); lagrima.visible = false; cab.add(lagrima);
    var ant = new T.Mesh(new T.CylinderGeometry(0.03, 0.03, 0.4, 10), mat(0x0f172a)); ant.position.set(0, 1.06, 0); cab.add(ant);
    var bola = new T.Mesh(new T.SphereGeometry(0.1, 20, 16), mat(0xf43f5e, { r: 0.25, c: 0.8 })); bola.position.set(0, 1.3, 0); cab.add(bola);
    // capacete amarelo
    var capac = new T.Mesh(new T.SphereGeometry(0.62, 40, 20, 0, Math.PI * 2, 0, Math.PI / 2), mat(cor.amarelo, { r: 0.35, c: 0.5 })); capac.position.set(0, 0.62, 0); capac.scale.set(1.05, 0.7, 1); cab.add(capac);
    var aba = new T.Mesh(new T.CylinderGeometry(0.92, 0.92, 0.07, 48), mat(0xfbbf24, { r: 0.4, c: 0.4 })); aba.position.set(0, 0.62, 0.02); cab.add(aba);
    // bracos (manga verde) e maos
    var ombroD = new T.Vector3(0.62, 1.95, 0), ombroE = new T.Vector3(-0.62, 1.95, 0);
    var bracoD = new T.Mesh(new T.CapsuleGeometry(0.17, 0.5, 8, 14), mat(cor.verde, { r: 0.6 })), bracoE = new T.Mesh(new T.CapsuleGeometry(0.17, 0.5, 8, 14), mat(cor.verde, { r: 0.6 }));
    var maoD = new T.Mesh(new T.SphereGeometry(0.2, 20, 16), mat(cor.pele, { r: 0.5 })), maoE = new T.Mesh(new T.SphereGeometry(0.2, 20, 16), mat(cor.pele, { r: 0.5 }));
    corpo.add(bracoD, bracoE, maoD, maoE);
    var caneta = new T.Group(); var cc = new T.Mesh(new T.CylinderGeometry(0.035, 0.035, 0.55, 10), mat(0x2563eb, { r: 0.3, c: 0.6 })); caneta.add(cc); var pt = new T.Mesh(new T.ConeGeometry(0.035, 0.1, 10), mat(0x111827)); pt.position.y = -0.32; caneta.add(pt); corpo.add(caneta);
    // prancheta com anotacoes animadas
    var pr = new T.Group(); pr.position.set(-1.05, 1.5, 0.62); pr.rotation.set(-0.08, 0.5, 0.06); corpo.add(pr);
    var tabua = new T.Mesh(new T.BoxGeometry(0.98, 1.25, 0.08), mat(cor.madeira, { r: 0.6 })); pr.add(tabua);
    var papC = document.createElement('canvas'); papC.width = 128; papC.height = 160; var pg = papC.getContext('2d'), papT = new T.CanvasTexture(papC); 
    var papel = new T.Mesh(new T.PlaneGeometry(0.8, 1.02), new T.MeshBasicMaterial({ map: papT })); papel.position.z = 0.045; pr.add(papel);
    var clipe = new T.Mesh(new T.BoxGeometry(0.36, 0.17, 0.12), mat(0x94a3b8, { r: 0.3, m: 0.7 })); clipe.position.set(0, 0.62, 0.05); pr.add(clipe);
    function desenhaPapel(t) {
      pg.fillStyle = '#fff'; pg.fillRect(0, 0, 128, 160); pg.strokeStyle = '#1d4ed8'; pg.lineWidth = 5; pg.lineCap = 'round';
      var prog = (t * 0.6) % 4;
      for (var i = 0; i < 4; i++) { var p = Math.max(0, Math.min(prog - i, 1)); if (p <= 0) continue; var y = 30 + i * 28, comp = (i === 3 ? 60 : 92 - i * 8) * p; pg.beginPath(); pg.moveTo(18, y); pg.lineTo(18 + comp, y); pg.stroke(); }
      if (prog > 3.6) { pg.strokeStyle = '#16a34a'; pg.lineWidth = 7; pg.beginPath(); pg.moveTo(82, 118); pg.lineTo(92, 130); pg.lineTo(112, 106); pg.stroke(); }
      papT.needsUpdate = true;
    }
    // interacao: cabeca acompanha o mouse
    var alvo = { x: 0, y: 0 }; window.addEventListener('mousemove', function (e) { alvo.y = ((e.clientX / window.innerWidth) - 0.5) * 0.7; alvo.x = ((e.clientY / window.innerHeight) - 0.5) * 0.3; });
    var t0 = performance.now(), proxPisca = 2.5, piscaAte = 0, rodando = true;
    function quadro() {
      if (!rodando) return; requestAnimationFrame(quadro);
      if (!document.body.contains(canvas)) { rodando = false; return; }
      var t = (performance.now() - t0) / 1000, tri = estado.triste;
      corpo.position.y = Math.sin(t * (tri ? 1.2 : 2.2)) * (tri ? 0.015 : 0.045);
      cab.rotation.y += ((alvo.y * 0.8) - cab.rotation.y) * 0.08; cab.rotation.x += (((tri ? 0.18 : 0) + alvo.x) - cab.rotation.x) * 0.08; cab.rotation.z = Math.sin(t * 1.3) * 0.03 + (tri ? 0.06 : 0);
      // piscar
      if (t > proxPisca) { piscaAte = t + 0.13; proxPisca = t + 2.5 + Math.random() * 2.5; }
      var fechado = t < piscaAte; olhos.forEach(function (o) { o.scale.y = fechado ? 0.08 : 1; });
      // humor
      boca.rotation.z = tri ? 0 : Math.PI; boca.position.y = tri ? -0.36 : -0.2;
      var abre = estado.falando ? 0.6 + Math.abs(Math.sin(t * 14)) * 0.6 : 1; boca.scale.set(1, tri ? 1 : abre, 1);
      sobr[0].rotation.z = Math.PI / 2 + (tri ? -0.45 : 0); sobr[1].rotation.z = Math.PI / 2 + (tri ? 0.45 : 0); sobr.forEach(function (b) { b.position.y = tri ? 0.5 : 0.43; });
      lagrima.visible = tri; if (tri) { var q = (t * 0.8) % 1; lagrima.position.set(-0.4, 0.1 - q * 0.45, 0.78); lagrima.material.opacity = 1; }
      bola.position.y = 1.3 + Math.sin(t * 5) * 0.02;
      // bracos
      var ang = tri ? -0.2 : 2.35 + Math.sin(t * 6.5) * 0.28;                                   // radianos a partir do eixo -Y: acena para cima; triste: braco caido
      var maoDpos = new T.Vector3(ombroD.x + Math.sin(ang) * 0.95 * (tri ? 0.4 : 1), ombroD.y + Math.cos(ang) * 0.95 * (tri ? 0.5 : 1) * -1 * -1, 0.05);
      if (!tri) maoDpos.set(ombroD.x + 0.55 + Math.sin(t * 6.5) * 0.18, ombroD.y + 0.62, 0.1); else maoDpos.set(ombroD.x + 0.25, ombroD.y - 0.85, 0.12);
      membro(bracoD, ombroD, maoDpos); maoD.position.copy(maoDpos);
      caneta.position.set(maoDpos.x + 0.1, maoDpos.y + 0.12, 0.12); caneta.rotation.z = -0.5;
      var maoEpos = new T.Vector3(-1.05, 1.18, 0.6); maoEpos.y += Math.sin(t * 6) * (tri ? 0 : 0.015); membro(bracoE, ombroE, maoEpos); maoE.position.copy(maoEpos);
      pr.position.y = 1.5 + Math.sin(t * 2.2) * 0.02; desenhaPapel(t);
      sombra.scale.set(1 + Math.sin(t * 2.2) * 0.03, 1, 1);
      renderer.render(scene, cam);
    }
    quadro();
    return true;
  }
  window.Fio3D = { init: init, setTriste: function (v) { estado.triste = !!v; }, falar: function (v) { estado.falando = !!v; } };
})();
