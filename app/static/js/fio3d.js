/* Mascote 3D da Gamificação (Three.js): um colaborador da Conexão com proporção humana — rosto, cabelo, polo verde com a logo, calça jeans, botina, prancheta e caneta.
   Window.Fio3D.init(canvas, urlLogo) -> true/false (false = sem WebGL: a tela usa o desenho 2D). setTriste(bool), falar(bool). */
(function () {
  var T = window.THREE; if (!T) return;
  var estado = { triste: false, falando: false };
  function mat(cor, r, c) { return new T.MeshPhysicalMaterial({ color: cor, roughness: r === undefined ? 0.7 : r, metalness: 0, clearcoat: c || 0, clearcoatRoughness: 0.4 }); }
  function V(x, y, z) { return new T.Vector3(x, y, z); }
  function seg(mesh, a, b, raio) {                 // capsula (eixo Y, comprimento 1 no modelo-base) entre a e b
    var d = new T.Vector3().subVectors(b, a), len = d.length();
    mesh.position.copy(a).addScaledVector(d, 0.5); mesh.quaternion.setFromUnitVectors(V(0, 1, 0), d.clone().normalize()); mesh.scale.set(1, Math.max(len, 0.02), 1);
  }
  function cap(raio, cor, r) { var gg = new T.CylinderGeometry(raio, raio * 0.86, 1, 22, 1); return new T.Mesh(gg, mat(cor, r)); }   // membro cônico (eixo Y, comprimento 1)
  function init(canvas, urlLogo) {
    var renderer;
    try { renderer = new T.WebGLRenderer({ canvas: canvas, antialias: true, alpha: true }); } catch (e) { return false; }
    if (!renderer.getContext()) return false;
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    var W = canvas.clientWidth || 240, H = canvas.clientHeight || 300; renderer.setSize(W, H, false);
    var scene = new T.Scene(), cam = new T.PerspectiveCamera(27, W / H, 0.1, 60); cam.position.set(0.5, 2.3, 12.6); cam.lookAt(0, 2.05, 0);
    scene.add(new T.HemisphereLight(0xffffff, 0x9fb3d9, 0.75));
    var sol = new T.DirectionalLight(0xffffff, 1.05); sol.position.set(3.5, 6, 6); scene.add(sol);
    var rim = new T.DirectionalLight(0x93c5fd, 0.5); rim.position.set(-4, 3, -4); scene.add(rim);
    var C = { pele: 0xe0aa83, peleSombra: 0xc98f68, cabelo: 0x2f2118, polo: 0x17984a, poloEscuro: 0x0f7a3a, jeans: 0x34609f, jeansClaro: 0x6f95cc, bota: 0x2a1e16, branco: 0xffffff, madeira: 0xa4501a };
    var g = new T.Group(); scene.add(g);
    // sombra no chao
    var cs = document.createElement('canvas'); cs.width = cs.height = 128; var cx = cs.getContext('2d'), gr = cx.createRadialGradient(64, 64, 4, 64, 64, 62); gr.addColorStop(0, 'rgba(15,23,42,.45)'); gr.addColorStop(1, 'rgba(15,23,42,0)'); cx.fillStyle = gr; cx.fillRect(0, 0, 128, 128);
    var sombra = new T.Mesh(new T.PlaneGeometry(3.6, 1.8), new T.MeshBasicMaterial({ map: new T.CanvasTexture(cs), transparent: true, depthWrite: false })); sombra.rotation.x = -Math.PI / 2; sombra.position.y = 0.01; scene.add(sombra);
    var corpo = new T.Group(); g.add(corpo);
    // ---- pernas (jeans), botinas
    [-1, 1].forEach(function (s) {
      var coxa = cap(0.25, C.jeans, 0.85), canela = cap(0.215, C.jeans, 0.85); corpo.add(coxa, canela);
      seg(coxa, V(s * 0.26, 1.95, 0), V(s * 0.27, 1.05, 0.03), 0.25); seg(canela, V(s * 0.27, 1.05, 0.03), V(s * 0.27, 0.3, 0), 0.215);
      var joelho = new T.Mesh(new T.SphereGeometry(0.225, 20, 16), mat(C.jeans, 0.85)); joelho.position.set(s * 0.27, 1.05, 0.03); corpo.add(joelho);
      var coxaTopo = new T.Mesh(new T.SphereGeometry(0.25, 20, 16), mat(C.jeans, 0.85)); coxaTopo.position.set(s * 0.26, 1.95, 0); corpo.add(coxaTopo);
      var bota = new T.Mesh(new T.SphereGeometry(0.24, 24, 18), mat(C.bota, 0.5, 0.3)); bota.scale.set(0.95, 0.62, 1.55); bota.position.set(s * 0.27, 0.2, 0.1); corpo.add(bota);
      var cano = new T.Mesh(new T.CylinderGeometry(0.2, 0.215, 0.14, 20), mat(C.bota, 0.5, 0.3)); cano.position.set(s * 0.27, 0.3, 0); corpo.add(cano);
      var sola = new T.Mesh(new T.BoxGeometry(0.42, 0.05, 0.8), mat(0x111111, 0.9)); sola.position.set(s * 0.27, 0.025, 0.1); corpo.add(sola);
    });
    var quadril = new T.Mesh(new T.SphereGeometry(0.56, 32, 22), mat(C.jeans, 0.85)); quadril.scale.set(1.02, 0.5, 0.7); quadril.position.set(0, 2.0, 0); corpo.add(quadril);
    var cinto = new T.Mesh(new T.CylinderGeometry(0.545, 0.545, 0.1, 36), mat(0x1c1410, 0.5)); cinto.position.y = 2.15; cinto.scale.z = 0.7; corpo.add(cinto);
    var fivela = new T.Mesh(new T.BoxGeometry(0.16, 0.1, 0.03), mat(0xc9a227, 0.3, 0.6)); fivela.position.set(0, 2.15, 0.39); corpo.add(fivela);
    // ---- tronco: polo verde
    var tronco = new T.Mesh(new T.CapsuleGeometry(0.5, 0.72, 12, 28), mat(C.polo, 0.75)); tronco.scale.set(1.0, 1, 0.68); tronco.position.y = 2.62; corpo.add(tronco);
    var ombros = new T.Mesh(new T.CapsuleGeometry(0.24, 0.82, 10, 18), mat(C.polo, 0.75)); ombros.rotation.z = Math.PI / 2; ombros.position.set(0, 3.03, 0); ombros.scale.set(1, 1, 0.9); corpo.add(ombros);
    var gola = new T.Mesh(new T.TorusGeometry(0.2, 0.06, 10, 28), mat(C.poloEscuro, 0.8)); gola.rotation.x = Math.PI / 2 - 0.25; gola.position.set(0, 3.2, 0.06); gola.scale.set(1.1, 1, 0.9); corpo.add(gola);
    var abertura = new T.Mesh(new T.BoxGeometry(0.05, 0.3, 0.02), mat(C.poloEscuro, 0.8)); abertura.position.set(0, 3.05, 0.34); corpo.add(abertura);
    // logo da Conexao no peito
    var logoTex = new T.TextureLoader().load(urlLogo); logoTex.anisotropy = 4;
    var cracha = new T.Mesh(new T.PlaneGeometry(0.78, 0.26), new T.MeshStandardMaterial({ color: 0xffffff, roughness: 0.6 })); cracha.position.set(0.1, 2.78, 0.352); corpo.add(cracha);
    var logo = new T.Mesh(new T.PlaneGeometry(0.72, 0.139), new T.MeshBasicMaterial({ map: logoTex, transparent: true })); logo.position.set(0.1, 2.78, 0.356); corpo.add(logo);
    // pescoco e cabeca
    var pescoco = new T.Mesh(new T.CylinderGeometry(0.16, 0.19, 0.3, 20), mat(C.peleSombra, 0.6)); pescoco.position.set(0, 3.28, 0); corpo.add(pescoco);
    var cab = new T.Group(); cab.position.set(0, 3.72, 0.02); corpo.add(cab);
    var cranio = new T.Mesh(new T.SphereGeometry(0.43, 48, 36), mat(C.pele, 0.55)); cranio.scale.set(0.92, 1.08, 0.97); cab.add(cranio);
    var queixo = new T.Mesh(new T.SphereGeometry(0.3, 32, 24), mat(C.pele, 0.55)); queixo.scale.set(0.95, 0.8, 0.9); queixo.position.set(0, -0.26, 0.1); cab.add(queixo);
    [-1, 1].forEach(function (s) { var o = new T.Mesh(new T.SphereGeometry(0.085, 14, 12), mat(C.pele, 0.6)); o.scale.set(0.55, 1, 0.8); o.position.set(s * 0.4, 0, 0); cab.add(o); });
    var nariz = new T.Mesh(new T.SphereGeometry(0.07, 16, 12), mat(C.peleSombra, 0.55)); nariz.scale.set(0.9, 1.15, 1); nariz.position.set(0, -0.05, 0.43); cab.add(nariz);
    var olhos = [], iris = [];
    [-1, 1].forEach(function (s) {
      var og = new T.Group(); og.position.set(s * 0.165, 0.07, 0.37);
      var br = new T.Mesh(new T.SphereGeometry(0.085, 24, 18), mat(0xfdfdfd, 0.25, 0.6)); br.scale.set(1, 0.9, 0.55); og.add(br);
      var ir = new T.Mesh(new T.SphereGeometry(0.05, 20, 16), mat(0x5a3a22, 0.3, 0.8)); ir.position.set(0, 0, 0.035); ir.scale.set(1, 1, 0.5); og.add(ir); iris.push(ir);
      var pu = new T.Mesh(new T.SphereGeometry(0.025, 14, 12), mat(0x050505, 0.2)); pu.position.set(0, 0, 0.055); og.add(pu);
      var bri = new T.Mesh(new T.SphereGeometry(0.012, 8, 8), new T.MeshBasicMaterial({ color: 0xffffff })); bri.position.set(0.02, 0.02, 0.065); og.add(bri);
      cab.add(og); olhos.push(og);
    });
    var sobr = [-1, 1].map(function (s) { var b = new T.Mesh(new T.CapsuleGeometry(0.014, 0.12, 4, 8), mat(0x4a3526, 0.8)); b.rotation.z = Math.PI / 2; b.position.set(s * 0.165, 0.2, 0.385); cab.add(b); return b; });
    var boca = new T.Mesh(new T.TorusGeometry(0.13, 0.017, 8, 24, Math.PI), mat(0x8a3a30, 0.5)); boca.position.set(0, -0.2, 0.4); boca.rotation.z = Math.PI; cab.add(boca);
    var dentes = new T.Mesh(new T.CircleGeometry(0.115, 24, 0, Math.PI), mat(0xffffff, 0.3)); dentes.rotation.z = Math.PI; dentes.position.set(0, -0.2, 0.396); dentes.scale.set(1, 0.55, 1); cab.add(dentes);
    var lagrima = new T.Mesh(new T.SphereGeometry(0.03, 12, 10), mat(0x7dd3fc, 0.1, 1)); lagrima.scale.set(1, 1.5, 0.8); lagrima.visible = false; cab.add(lagrima);
    // cabelo curto
    var cabelo = new T.Mesh(new T.SphereGeometry(0.455, 40, 24, 0, Math.PI * 2, 0, Math.PI * 0.56), mat(C.cabelo, 0.8)); cabelo.scale.set(0.95, 1.1, 1.0); cabelo.position.set(0, 0.03, -0.02); cabelo.rotation.x = -0.18; cab.add(cabelo);
    var topete = new T.Mesh(new T.SphereGeometry(0.2, 20, 16), mat(C.cabelo, 0.8)); topete.scale.set(1.5, 0.7, 0.9); topete.position.set(0.05, 0.4, 0.2); cab.add(topete);
    // ---- bracos
    var ombroD = V(0.66, 3.0, 0), ombroE = V(-0.66, 3.0, 0);
    var mangaD = cap(0.165, C.polo, 0.75), mangaE = cap(0.165, C.polo, 0.75), antD = cap(0.115, C.pele, 0.55), antE = cap(0.115, C.pele, 0.55), bracoD = cap(0.125, C.pele, 0.55), bracoE = cap(0.125, C.pele, 0.55);
    var maoD = new T.Mesh(new T.SphereGeometry(0.135, 20, 16), mat(C.pele, 0.55)), maoE = new T.Mesh(new T.SphereGeometry(0.135, 20, 16), mat(C.pele, 0.55));
    var cotD = new T.Mesh(new T.SphereGeometry(0.12, 14, 12), mat(C.pele, 0.55)), cotE = new T.Mesh(new T.SphereGeometry(0.12, 14, 12), mat(C.pele, 0.55));
    var ombD = new T.Mesh(new T.SphereGeometry(0.2, 18, 14), mat(C.polo, 0.75)), ombE = ombD.clone(); ombD.position.copy(ombroD); ombE.position.copy(ombroE);
    corpo.add(mangaD, mangaE, antD, antE, bracoD, bracoE, maoD, maoE, cotD, cotE, ombD, ombE);
    var caneta = new T.Group(); var cc = new T.Mesh(new T.CylinderGeometry(0.022, 0.022, 0.4, 10), mat(0x2563eb, 0.3, 0.6)); caneta.add(cc); var pt = new T.Mesh(new T.ConeGeometry(0.022, 0.07, 10), mat(0x111827)); pt.position.y = -0.235; caneta.add(pt); corpo.add(caneta);
    // ---- prancheta
    var pr = new T.Group(); pr.position.set(-0.18, 1.88, 0.62); pr.rotation.set(-0.1, 0.2, 0.03); corpo.add(pr);
    var tabua = new T.Mesh(new T.BoxGeometry(0.7, 0.9, 0.06), mat(C.madeira, 0.6)); pr.add(tabua);
    var papC = document.createElement('canvas'); papC.width = 128; papC.height = 160; var pg = papC.getContext('2d'), papT = new T.CanvasTexture(papC);
    var papel = new T.Mesh(new T.PlaneGeometry(0.58, 0.72), new T.MeshBasicMaterial({ map: papT })); papel.position.z = 0.035; pr.add(papel);
    var clipe = new T.Mesh(new T.BoxGeometry(0.28, 0.13, 0.1), mat(0x94a3b8, 0.3, 0)); clipe.position.set(0, 0.44, 0.04); pr.add(clipe);
    function desenhaPapel(t) {
      pg.fillStyle = '#fff'; pg.fillRect(0, 0, 128, 160); pg.strokeStyle = '#1d4ed8'; pg.lineWidth = 5; pg.lineCap = 'round';
      var prog = (t * 0.6) % 4;
      for (var i = 0; i < 4; i++) { var p = Math.max(0, Math.min(prog - i, 1)); if (p <= 0) continue; var y = 30 + i * 28, comp = (i === 3 ? 60 : 92 - i * 8) * p; pg.beginPath(); pg.moveTo(18, y); pg.lineTo(18 + comp, y); pg.stroke(); }
      if (prog > 3.6) { pg.strokeStyle = '#16a34a'; pg.lineWidth = 7; pg.beginPath(); pg.moveTo(82, 118); pg.lineTo(92, 130); pg.lineTo(112, 106); pg.stroke(); }
      papT.needsUpdate = true;
    }
    var alvo = { x: 0, y: 0 }; window.addEventListener('mousemove', function (e) { alvo.y = ((e.clientX / window.innerWidth) - 0.5) * 0.8; alvo.x = ((e.clientY / window.innerHeight) - 0.5) * 0.35; });
    var t0 = performance.now(), proxPisca = 2.2, piscaAte = 0, rodando = true, acenar = 0;
    function braco(ombro, cotovelo, mao, manga, antebraco, braco, cot, mDown) {
      var m1 = new T.Vector3().lerpVectors(ombro, cotovelo, 0.5);
      seg(manga, ombro, m1, 0.165); seg(braco, m1, cotovelo, 0.125); seg(antebraco, cotovelo, mao, 0.105); cot.position.copy(cotovelo);
    }
    function quadro() {
      if (!rodando) return; requestAnimationFrame(quadro);
      if (!document.body.contains(canvas)) { rodando = false; return; }
      var t = (performance.now() - t0) / 1000, tri = estado.triste;
      acenar += ((estado.falando && !tri ? 1 : 0) - acenar) * 0.08;                    // fala = acena; senao, anota
      corpo.position.y = Math.sin(t * (tri ? 1.1 : 1.9)) * (tri ? 0.012 : 0.03); corpo.rotation.z = Math.sin(t * 1.1) * 0.012;
      cab.rotation.y += ((alvo.y * 0.9) - cab.rotation.y) * 0.07; cab.rotation.x += (((tri ? 0.2 : 0.03) + alvo.x) - cab.rotation.x) * 0.07; cab.rotation.z = Math.sin(t * 1.3) * 0.02 + (tri ? 0.07 : 0);
      if (t > proxPisca) { piscaAte = t + 0.12; proxPisca = t + 2.2 + Math.random() * 2.8; }
      var fechado = t < piscaAte; olhos.forEach(function (o) { o.scale.y = fechado ? 0.1 : 1; }); iris.forEach(function (i) { i.position.x = (cab.rotation.y) * 0.05; });
      boca.rotation.z = tri ? 0 : Math.PI; boca.position.y = tri ? -0.26 : -0.2; dentes.visible = !tri;
      var abre = estado.falando && !tri ? 0.7 + Math.abs(Math.sin(t * 13)) * 0.9 : 1; boca.scale.set(1, tri ? 1 : abre, 1); dentes.scale.set(1, 0.55 * (tri ? 0 : abre), 1);
      sobr[0].rotation.z = Math.PI / 2 + (tri ? -0.35 : 0.04); sobr[1].rotation.z = Math.PI / 2 + (tri ? 0.35 : -0.04); sobr.forEach(function (b) { b.position.y = tri ? 0.22 : 0.2; });
      lagrima.visible = tri; if (tri) { var q = (t * 0.7) % 1; lagrima.position.set(-0.19, 0.0 - q * 0.3, 0.4); }
      // braco direito (da pessoa): escreve na prancheta ou acena
      var escreve = V(-0.05 + Math.sin(t * 3.2) * 0.1, 1.9 + Math.cos(t * 4.1) * 0.05, 0.72), ac = V(0.98 + Math.sin(t * 7) * 0.13, 3.72, 0.2), cai = V(0.55, 2.0, 0.12);
      var maoDpos = (tri ? cai : (acenar > 0.5 ? ac : escreve)).clone(); maoDpos.lerp(escreve, 0); 
      var cotD2 = V(tri ? 0.82 : (acenar > 0.5 ? 0.98 : 0.8), tri ? 2.3 : (acenar > 0.5 ? 3.05 : 2.35), tri ? 0.02 : 0.3);
      braco(ombroD, cotD2, maoDpos, mangaD, antD, bracoD, cotD); maoD.position.copy(maoDpos);
      caneta.position.set(maoDpos.x + 0.02, maoDpos.y - 0.04, maoDpos.z + 0.08); caneta.rotation.set(0.3, 0, acenar > 0.5 && !tri ? -0.2 : 0.55);
      // braco esquerdo segura a prancheta
      var maoEpos = V(-0.5, 1.7 + Math.sin(t * 1.9) * 0.015, 0.72), cotE2 = V(-0.85, 2.3, 0.2); if (tri) { maoEpos = V(-0.5, 1.75, 0.7); }
      braco(ombroE, cotE2, maoEpos, mangaE, antE, bracoE, cotE); maoE.position.copy(maoEpos);
      pr.position.y = 1.88 + Math.sin(t * 1.9) * 0.015; desenhaPapel(t);
      sombra.scale.set(1 + Math.sin(t * 1.9) * 0.02, 1, 1);
      renderer.render(scene, cam);
    }
    quadro();
    return true;
  }
  window.Fio3D = { init: init, setTriste: function (v) { estado.triste = !!v; }, falar: function (v) { estado.falando = !!v; } };
})();
