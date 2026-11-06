/* Personagem ilustrado da Gamificação: uma colaboradora da Conexão (polo verde com a logo, jeans), desenhada em SVG com sombreado,
   que gesticula e fala (boca, sobrancelhas, mão aberta), pisca e acompanha o mouse; triste quando o setor é errado.
   Fio3D.init(canvas, urlLogo) -> true (troca o <canvas> pelo desenho). setTriste(bool), falar(bool). */
(function () {
  var estado = { triste: false, falando: false };
  function svgTxt(logo) {
    return '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 240 300" role="img" aria-label="Colaboradora da Conexão, mascote da Gamificação">' +
    '<defs>' +
    '<radialGradient id="fgBg" cx="50%" cy="45%" r="55%"><stop offset="0" stop-color="#e3f4e8"/><stop offset="1" stop-color="#cfe8f5"/></radialGradient>' +
    '<linearGradient id="fgPele" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#f8d3b0"/><stop offset="1" stop-color="#e8b088"/></linearGradient>' +
    '<linearGradient id="fgPoloG" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#27b35e"/><stop offset="1" stop-color="#128443"/></linearGradient>' +
    '<linearGradient id="fgCabelo" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#a5582b"/><stop offset="1" stop-color="#5e2a14"/></linearGradient>' +
    '<linearGradient id="fgJeans" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#4a78b8"/><stop offset="1" stop-color="#2f5a99"/></linearGradient>' +
    '<filter id="fgBranco" color-interpolation-filters="sRGB"><feColorMatrix type="matrix" values="0 0 0 0 1  0 0 0 0 1  0 0 0 0 1  0 0 0 1 0"/></filter></defs>' +
    '<circle cx="120" cy="150" r="108" fill="url(#fgBg)"/>' +
    '<ellipse cx="120" cy="296" rx="92" ry="7" fill="#0f172a" opacity=".12"/>' +
    // cabelo de tras
    '<path d="M80 92C74 48 98 26 123 26C150 26 168 50 160 98C165 128 165 152 154 176L88 176C77 154 76 124 80 92Z" fill="url(#fgCabelo)"/>' +
    // pescoco
    '<path d="M107 118H133V160C133 168 107 168 107 160Z" fill="#e2a982"/>' +
    // tronco (polo)
    '<path d="M62 300C60 232 66 184 98 162L142 162C174 184 180 232 178 300Z" fill="url(#fgPoloG)"/>' +
    '' +
    '<path d="M66 176C76 164 92 162 100 162L140 162C150 162 164 164 174 176L170 200L70 200Z" fill="#1fa755"/><path d="M61 282L179 282L179 300L61 300Z" fill="url(#fgJeans)"/><path d="M61 282L179 282" stroke="#1c1410" stroke-width="5"/><rect x="113" y="278" width="14" height="8" rx="2" fill="#d4af37"/>' +
    // gola
    '<path d="M104 158L120 186L97 176Z" fill="#0f7a3a"/><path d="M136 158L120 186L143 176Z" fill="#0d6d34"/><path d="M120 184V214" stroke="#0f7a3a" stroke-width="2.5"/><circle cx="120" cy="196" r="1.8" fill="#e9fbe9"/><circle cx="120" cy="207" r="1.8" fill="#e9fbe9"/>' +
    // logo no peito
    '<image href="' + logo + '" x="106" y="208" width="42" height="8.1" filter="url(#fgBranco)" preserveAspectRatio="xMidYMid meet"/>' +
    // cabeca
    '<g id="fgCab">' +
    '<ellipse cx="86" cy="100" rx="6" ry="10" fill="#e8b088"/><ellipse cx="154" cy="100" rx="6" ry="10" fill="#e8b088"/><circle cx="154" cy="110" r="2.6" fill="#d4af37"/><circle cx="86" cy="110" r="2.6" fill="#d4af37"/>' +
    '<path d="M88 88C88 62 104 50 120 50C136 50 152 62 152 88C152 114 138 136 120 136C102 136 88 114 88 88Z" fill="url(#fgPele)"/>' +
    '<ellipse cx="102" cy="112" rx="9" ry="5.5" fill="#f08a7a" opacity=".35"/><ellipse cx="138" cy="112" rx="9" ry="5.5" fill="#f08a7a" opacity=".35"/>' +
    // olhos
    '<g id="fgOlhoE" transform="translate(105 94)"><ellipse rx="9" ry="6.5" fill="#fff"/><g class="fgIris"><circle r="4.4" fill="#4f7a45"/><circle r="2.3" fill="#10200f"/><circle cx="1.6" cy="-1.6" r="1.1" fill="#fff"/></g><path d="M-10 -1C-6 -8 6 -8 10 -1" stroke="#3a1c10" stroke-width="2.2" fill="none" stroke-linecap="round"/></g>' +
    '<g id="fgOlhoD" transform="translate(135 94)"><ellipse rx="9" ry="6.5" fill="#fff"/><g class="fgIris"><circle r="4.4" fill="#4f7a45"/><circle r="2.3" fill="#10200f"/><circle cx="1.6" cy="-1.6" r="1.1" fill="#fff"/></g><path d="M-10 -1C-6 -8 6 -8 10 -1" stroke="#3a1c10" stroke-width="2.2" fill="none" stroke-linecap="round"/></g>' +
    '<path id="fgSobE" d="M95 82Q105 76 115 81" stroke="#6b3418" stroke-width="3" fill="none" stroke-linecap="round"/><path id="fgSobD" d="M125 81Q135 76 145 82" stroke="#6b3418" stroke-width="3" fill="none" stroke-linecap="round"/>' +
    '<path d="M120 98C118 106 116 110 119 112C121 113 124 112 124 111" stroke="#c98d68" stroke-width="2" fill="none" stroke-linecap="round"/>' +
    '<path id="fgBoca" d="M106 120Q120 124 134 120Q120 126 106 120Z" fill="#b94a46" stroke="#b94a46" stroke-width="2.2" stroke-linejoin="round"/>' +
    '<path id="fgDentes" d="M110 121.5Q120 123.5 130 121.5L128 124Q120 126 112 124Z" fill="#fff" opacity="0"/>' +
    '<ellipse id="fgLagr" cx="100" cy="106" rx="2.4" ry="3.6" fill="#7dd3fc" opacity="0"/>' +
    // franja
    '<path d="M85 88C84 56 106 42 126 44C146 46 156 64 155 90C150 72 138 62 120 62C104 63 92 74 85 88Z" fill="url(#fgCabelo)"/>' +
    '<path d="M100 52C112 46 130 46 142 54" stroke="#c6783d" stroke-width="2.5" fill="none" opacity=".6" stroke-linecap="round"/>' +
    '</g>' +
    // madeixas na frente dos ombros
    '<path d="M82 120C74 146 76 168 90 184C84 160 88 140 94 128Z" fill="url(#fgCabelo)"/><path d="M158 120C166 146 164 168 150 184C156 160 152 140 146 128Z" fill="url(#fgCabelo)"/>' +
    // braco que gesticula (esquerda da tela): antebraco e mao atras, manga por cima
    '<g id="fgBracoE"><path d="M64 196L48 236" stroke="#e8b088" stroke-width="17" stroke-linecap="round"/><g id="fgAnt" transform="translate(50 236)"><path d="M0 0L30 -28" stroke="#e8b088" stroke-width="17" stroke-linecap="round"/><path d="M-2 -4L28 -32" stroke="#f6d2b2" stroke-width="5" stroke-linecap="round" opacity=".7"/>' +
    '<g transform="translate(38 -37) rotate(-52)"><ellipse rx="12" ry="9" fill="#f0bf98"/><path d="M8 -4L25 -9" stroke="#f0bf98" stroke-width="6" stroke-linecap="round"/><path d="M10 0L28 -1" stroke="#f0bf98" stroke-width="6" stroke-linecap="round"/><path d="M10 4L27 8" stroke="#f0bf98" stroke-width="5.8" stroke-linecap="round"/><path d="M7 8L21 15" stroke="#f0bf98" stroke-width="5.2" stroke-linecap="round"/><path d="M-5 -6L-13 -14" stroke="#f0bf98" stroke-width="6.5" stroke-linecap="round"/></g></g>' +
    '<path d="M78 174L68 200" stroke="#0e7a3b" stroke-width="32" stroke-linecap="round"/><path d="M78 174L68 200" stroke="#1fa755" stroke-width="26" stroke-linecap="round"/></g>' +
    // braco da prancheta (direita da tela)
    '<path d="M176 196L190 234" stroke="#e8b088" stroke-width="17" stroke-linecap="round"/><g transform="translate(0 6)"><g transform="rotate(-6 146 262)"><rect x="118" y="226" width="54" height="68" rx="4" fill="#a4501a"/><rect x="123" y="234" width="44" height="55" rx="2" fill="#fff"/><rect x="135" y="223" width="20" height="8" rx="2" fill="#94a3b8"/><path d="M128 246H158M128 254H154M128 262H160M128 270H148" stroke="#1d4ed8" stroke-width="2.2" stroke-linecap="round"/><path d="M152 280L156 284L164 274" stroke="#16a34a" stroke-width="2.6" fill="none" stroke-linecap="round"/></g>' +
    '<path d="M190 234L170 262" stroke="#e8b088" stroke-width="16" stroke-linecap="round"/><ellipse cx="168" cy="266" rx="10" ry="8" fill="#f0bf98" transform="rotate(-30 168 266)"/><path d="M162 262L158 250" stroke="#f0bf98" stroke-width="5.5" stroke-linecap="round"/><path d="M167 260L166 248" stroke="#f0bf98" stroke-width="5.5" stroke-linecap="round"/></g>' +
    '<path d="M162 174L172 200" stroke="#0e7a3b" stroke-width="32" stroke-linecap="round"/><path d="M162 174L172 200" stroke="#1fa755" stroke-width="26" stroke-linecap="round"/>' +
    '</svg>';
  }
  function init(canvas, urlLogo) {
    var d = document.createElement('div'); d.innerHTML = svgTxt(urlLogo || ''); var svg = d.firstChild;
    svg.setAttribute('class', canvas.className); svg.id = canvas.id; canvas.parentNode.replaceChild(svg, canvas);
    var $ = function (id) { return svg.querySelector('#' + id); };
    var cab = $('fgCab'), olhoE = $('fgOlhoE'), olhoD = $('fgOlhoD'), sobE = $('fgSobE'), sobD = $('fgSobD'), boca = $('fgBoca'), dentes = $('fgDentes'), lagr = $('fgLagr'), bracoE = $('fgBracoE'), ant = $('fgAnt');
    var alvo = { x: 0, y: 0 }; window.addEventListener('mousemove', function (e) { alvo.x = ((e.clientX / window.innerWidth) - 0.5) * 2; alvo.y = ((e.clientY / window.innerHeight) - 0.5) * 2; });
    var t0 = performance.now(), proxPisca = 2.2, piscaAte = 0, fala = 0, cx = 0, cy = 0, rodando = true;
    function quadro() {
      if (!rodando) return; requestAnimationFrame(quadro);
      if (!document.body.contains(svg)) { rodando = false; return; }
      var t = (performance.now() - t0) / 1000, tri = estado.triste, f = estado.falando && !tri ? 1 : 0;
      fala += (f - fala) * 0.12; cx += (alvo.x * 3 - cx) * 0.06; cy += (alvo.y * 2 - cy) * 0.06;
      var bal = Math.sin(t * 1.6) * 1.2, inclina = (tri ? 5 : 0) + Math.sin(t * 1.1) * 1.6 + Math.sin(t * 3.1) * fala * 1.5;
      cab.setAttribute('transform', 'translate(' + (cx + bal * 0.4).toFixed(2) + ' ' + (cy + (tri ? 4 : 0) + Math.sin(t * 1.9) * 0.8).toFixed(2) + ') rotate(' + (inclina + cx * 0.5).toFixed(2) + ' 120 132)');
      if (t > proxPisca) { piscaAte = t + 0.13; proxPisca = t + 2 + Math.random() * 3; }
      var sy = t < piscaAte ? 0.1 : (tri ? 0.8 : 1); [olhoE, olhoD].forEach(function (o, i) { o.setAttribute('transform', 'translate(' + (i ? 135 : 105) + ' 94) scale(1 ' + sy + ')'); });
      var look = cx * 0.25; Array.prototype.forEach.call(svg.querySelectorAll('.fgIris'), function (g) { g.setAttribute('transform', 'translate(' + look.toFixed(2) + ' ' + (cy * 0.3 + (tri ? 1.2 : 0)).toFixed(2) + ')'); });
      var up = fala * (1.5 + Math.sin(t * 5) * 1.5);                                  // sobrancelhas sobem ao enfatizar
      if (tri) { sobE.setAttribute('d', 'M95 83Q105 82 115 76'); sobD.setAttribute('d', 'M125 76Q135 82 145 83'); }
      else { sobE.setAttribute('d', 'M95 ' + (82 - up) + 'Q105 ' + (76 - up) + ' 115 ' + (81 - up)); sobD.setAttribute('d', 'M125 ' + (81 - up) + 'Q135 ' + (76 - up) + ' 145 ' + (82 - up)); }
      var o = fala * Math.max(0, 0.25 + Math.sin(t * 13) * 0.45 + Math.sin(t * 7.3) * 0.3);   // abre/fecha a boca ao falar
      if (tri) { boca.setAttribute('d', 'M108 126Q120 117 132 126Q120 121 108 126Z'); dentes.setAttribute('opacity', 0); }
      else { boca.setAttribute('d', 'M106 120Q120 ' + (124 - o * 2) + ' 134 120Q120 ' + (127 + o * 12) + ' 106 120Z'); dentes.setAttribute('opacity', o > 0.25 ? 0.95 : 0); }
      lagr.setAttribute('opacity', tri ? 0.9 : 0); if (tri) lagr.setAttribute('cy', 106 + ((t * 0.7) % 1) * 18);
      // braco que gesticula: mao aberta oscila ao falar; cai quando triste
      var gesto = tri ? 14 : (Math.sin(t * 1.4) * 2 + fala * (Math.sin(t * 3.6) * 12 - 6));
      ant.setAttribute('transform', 'translate(50 236) rotate(' + gesto.toFixed(2) + ')');
      bracoE.setAttribute('transform', 'translate(' + 0 + ' ' + (fala * Math.sin(t * 3.6) * -3).toFixed(2) + ')');
    }
    quadro();
    return true;
  }
  window.Fio3D = { init: init, setTriste: function (v) { estado.triste = !!v; }, falar: function (v) { estado.falando = !!v; } };
})();
