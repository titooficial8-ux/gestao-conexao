/* Robozinho da Gamificação: bloco laranja com olhos retangulares, bracinhos e quatro pernas (visual de "figura de montar"),
   de camisa verde com a logo da Conexão e prancheta. Desenhado em SVG com 3 faces (frente/topo/lado), anima ao falar, pisca e fica triste.
   Fio3D.init(canvas, urlLogo) -> true (troca o <canvas> pelo desenho). setTriste(bool), falar(bool). */
(function () {
  var estado = { triste: false, falando: false };
  function svgTxt(logo) {
    var F = '#f5871f', FT = '#ffab57', FS = '#c9650f', G = '#1fa755', GT = '#4cc983', GS = '#127a3b', K = '#1c1917';
    return '<svg xmlns="http://www.w3.org/2000/svg" viewBox="-26 0 266 300" role="img" aria-label="Robozinho mascote da Gamificação">' +
    '<defs><filter id="fgBranco" color-interpolation-filters="sRGB"><feColorMatrix type="matrix" values="0 0 0 0 1  0 0 0 0 1  0 0 0 0 1  0 0 0 1 0"/></filter></defs>' +
    '<ellipse cx="118" cy="252" rx="96" ry="11" fill="#0f172a" opacity=".16"/>' +
    '<g id="fgTudo">' +
    // pernas (4): frente + lado das da direita
    [[60, 0], [84, 0], [124, 1], [148, 1]].map(function (p) { var x = p[0]; return '<rect x="' + x + '" y="200" width="19" height="46" fill="' + F + '"/>' + (p[1] ? '<polygon points="' + (x + 19) + ',200 ' + (x + 27) + ',195 ' + (x + 27) + ',241 ' + (x + 19) + ',246" fill="' + FS + '"/>' : '') + '<rect x="' + x + '" y="236" width="19" height="10" fill="#000" opacity=".12"/>'; }).join('') +
    // lado direito e topo do corpo
    '<polygon points="175,100 197,84 197,189 175,205" fill="' + FS + '"/>' +
    '<polygon points="45,100 67,84 197,84 175,100" fill="' + FT + '"/>' +
    // frente
    '<rect x="45" y="100" width="130" height="105" fill="' + F + '"/>' +
    // camisa verde (faixa inferior): frente, lado, e gola em cima
    '<rect x="45" y="158" width="130" height="47" fill="' + G + '"/><polygon points="175,158 197,142 197,189 175,205" fill="' + GS + '"/><rect x="45" y="158" width="130" height="5" fill="' + GT + '"/>' +
    '<polygon points="98,158 122,158 110,172" fill="' + GS + '"/>' +
    '<image href="' + logo + '" x="64" y="177" width="92" height="17.8" filter="url(#fgBranco)" preserveAspectRatio="xMidYMid meet"/>' +
    // bone verde (bloco) com aba
    '<polygon points="150,74 172,60 172,84 150,98" fill="' + GS + '"/><polygon points="70,74 92,60 172,60 150,74" fill="' + GT + '"/><rect x="70" y="74" width="80" height="24" fill="' + G + '"/>' +
    '<polygon points="62,96 86,90 158,90 158,96" fill="' + GS + '"/><rect x="62" y="96" width="96" height="6" fill="' + GS + '"/><rect x="62" y="96" width="96" height="2" fill="' + GT + '" opacity=".5"/>' +
    '<circle cx="110" cy="86" r="5" fill="#fff"/><path d="M110 82.5L111.1 85L113.8 85.2L111.7 86.9L112.4 89.5L110 88L107.6 89.5L108.3 86.9L106.2 85.2L108.9 85Z" fill="' + G + '"/>' +
    // olhos
    '<g id="fgOlhoE" transform="translate(79 130)"><rect x="-8" y="-17" width="16" height="34" rx="1.5" fill="' + K + '"/><rect x="-4" y="-12" width="3.5" height="6" fill="#fff" opacity=".85"/></g>' +
    '<g id="fgOlhoD" transform="translate(141 130)"><rect x="-8" y="-17" width="16" height="34" rx="1.5" fill="' + K + '"/><rect x="-4" y="-12" width="3.5" height="6" fill="#fff" opacity=".85"/></g>' +
    '<ellipse id="fgLagr" cx="79" cy="150" rx="3" ry="5" fill="#7dd3fc" opacity="0"/>' +
    '<rect id="fgBoca" x="102" y="150" width="16" height="4" fill="' + K + '" opacity="0"/>' +
    // braco que gesticula (esquerda): bloco saindo do lado
    '<g id="fgBracoE" transform="translate(45 140)"><g id="fgAnt"><polygon points="-24,-12 -2,-12 -2,-18 -24,-18" fill="' + FT + '" transform="translate(0 6) skewX(-30)" opacity="0"/><rect x="-30" y="-12" width="32" height="26" fill="' + F + '"/><polygon points="-30,-12 -23,-18 9,-18 2,-12" fill="' + FT + '"/><rect x="-30" y="-12" width="6" height="26" fill="#000" opacity=".06"/><g id="fgCtrl" transform="translate(-22 -15)"><path d="M-13 -30C-25 -30 -23 -13 -9 -13" stroke="#d99a06" stroke-width="3.4" fill="none" stroke-linecap="round"/><path d="M13 -30C25 -30 23 -13 9 -13" stroke="#d99a06" stroke-width="3.4" fill="none" stroke-linecap="round"/><path d="M-14 -36H14C14 -18 9 -9 3.5 -7H-3.5C-9 -9 -14 -18 -14 -36Z" fill="#fbbf24"/><path d="M-14 -36H-7C-7 -20 -5 -11 -3.5 -7C-9 -9 -14 -18 -14 -36Z" fill="#fde68a" opacity=".8"/><path d="M14 -36H8C8 -20 6 -11 3.5 -7C9 -9 14 -18 14 -36Z" fill="#d99a06" opacity=".7"/><rect x="-3.5" y="-7" width="7" height="8" fill="#f59e0b"/><rect x="-11" y="0" width="22" height="9" rx="2" fill="#92400e"/><rect x="-11" y="0" width="22" height="3" rx="1.5" fill="#b45309"/><path transform="translate(0 -2) scale(1.1)" d="M0 -27L2.2 -21.6L8 -21.2L3.6 -17.5L5 -11.8L0 -15L-5 -11.8L-3.6 -17.5L-8 -21.2L-2.2 -21.6Z" fill="#fff7ed" stroke="#d97706" stroke-width="1" stroke-linejoin="round"/></g><rect x="-14" y="9" width="28" height="10" rx="3" fill="#f5871f"/><rect x="-14" y="9" width="28" height="2" fill="#ffab57"/></g></g></g>' +
    // braco da prancheta (direita)
    '<g id="fgBracoD"><polygon points="175,128 197,112 217,112 195,128" fill="' + FT + '"/><rect x="175" y="128" width="42" height="26" fill="' + F + '"/><polygon points="217,128 217,154 197,168 197,142" fill="' + FS + '" opacity="0"/>' +
    '<polygon points="195,128 217,112 217,138 195,154" fill="' + FS + '"/>' +
    '<g transform="rotate(8 206 168)"><rect x="186" y="132" width="42" height="56" rx="3" fill="#a4501a"/><rect x="190" y="139" width="34" height="45" rx="1.5" fill="#fff"/><rect x="199" y="129" width="16" height="7" rx="2" fill="#94a3b8"/><path d="M194 150H218M194 157H214M194 164H219M194 171H210" stroke="#1d4ed8" stroke-width="2" stroke-linecap="round"/><path d="M211 178L214 181L220 173" stroke="#16a34a" stroke-width="2.4" fill="none" stroke-linecap="round"/></g></g>' +
    '</g></svg>';
  }
  function init(canvas, urlLogo) {
    var d = document.createElement('div'); d.innerHTML = svgTxt(urlLogo || ''); var svg = d.firstChild;
    svg.setAttribute('class', canvas.className); svg.id = canvas.id; canvas.parentNode.replaceChild(svg, canvas);
    var $ = function (id) { return svg.querySelector('#' + id); };
    var tudo = $('fgTudo'), olhoE = $('fgOlhoE'), olhoD = $('fgOlhoD'), lagr = $('fgLagr'), boca = $('fgBoca'), bracoE = $('fgBracoE'), ctrl = $('fgCtrl');
    var alvo = { x: 0 }; window.addEventListener('mousemove', function (e) { alvo.x = ((e.clientX / window.innerWidth) - 0.5) * 2; });
    var t0 = performance.now(), proxPisca = 2.2, piscaAte = 0, fala = 0, cx = 0, rodando = true;
    function quadro() {
      if (!rodando) return; requestAnimationFrame(quadro);
      if (!document.body.contains(svg)) { rodando = false; return; }
      var t = (performance.now() - t0) / 1000, tri = estado.triste, f = estado.falando && !tri ? 1 : 0;
      fala += (f - fala) * 0.12; cx += (alvo.x * 4 - cx) * 0.07;
      var pulo = tri ? 0 : -Math.abs(Math.sin(t * 5)) * 5 * fala - (1 + Math.sin(t * 2)) * 0.8;
      tudo.setAttribute('transform', 'translate(0 ' + (pulo + (tri ? 4 : 0)).toFixed(2) + ') rotate(' + ((tri ? 2 : 0) + Math.sin(t * 1.3) * 0.8 + Math.sin(t * 4) * fala).toFixed(2) + ' 110 245)');
      if (t > proxPisca) { piscaAte = t + 0.12; proxPisca = t + 2.2 + Math.random() * 3; }
      var sy = t < piscaAte ? 0.1 : (tri ? 0.8 : 1);
      olhoE.setAttribute('transform', 'translate(' + (79 + cx).toFixed(2) + ' ' + (tri ? 134 : 130) + ') rotate(' + (tri ? -14 : 0) + ') scale(1 ' + sy + ')');
      olhoD.setAttribute('transform', 'translate(' + (141 + cx).toFixed(2) + ' ' + (tri ? 134 : 130) + ') rotate(' + (tri ? 14 : 0) + ') scale(1 ' + sy + ')');
      lagr.setAttribute('opacity', tri ? 0.9 : 0); if (tri) { lagr.setAttribute('cy', 152 + ((t * 0.7) % 1) * 22); lagr.setAttribute('cx', 79 + cx); }
      var o = fala * Math.max(0, 0.2 + Math.sin(t * 13) * 0.5 + Math.sin(t * 7.3) * 0.3);       // boquinha abre/fecha ao falar
      boca.setAttribute('opacity', o > 0.12 ? 1 : 0); boca.setAttribute('height', (3 + o * 8).toFixed(1));
      var ang = tri ? 24 : (Math.sin(t * 1.4) * 2 + fala * (-16 + Math.sin(t * 6) * 9));          // bracinho acena ao falar
      bracoE.setAttribute('transform', 'translate(45 140) rotate(' + ang.toFixed(2) + ')'); ctrl.setAttribute('transform', 'translate(-22 -15) rotate(' + (-ang).toFixed(2) + ')');
    }
    quadro();
    return true;
  }
  window.Fio3D = { init: init, setTriste: function (v) { estado.triste = !!v; }, falar: function (v) { estado.falando = !!v; } };
})();
