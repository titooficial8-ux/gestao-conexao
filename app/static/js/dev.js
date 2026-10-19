/* Auxiliares das abas do Desenvolvimento */
window.DV = (function () {
  var D = {};
  D.el = function (tag, cls, txt) { var e = document.createElement(tag); if (cls) e.className = cls; if (txt !== undefined && txt !== null) e.textContent = txt; return e; };
  D.api = function (url, metodo, corpo) {
    return fetch(url, { method: metodo || 'GET', credentials: 'same-origin', headers: corpo ? { 'Content-Type': 'application/json' } : {}, body: corpo ? JSON.stringify(corpo) : undefined })
      .then(function (r) { return r.json().catch(function () { return {}; }).then(function (j) { if (!r.ok) throw new Error(j.erro || ('Erro ' + r.status)); return j; }); });
  };
  D.nf = function (v, d) { return v === null || v === undefined ? '—' : Number(v).toLocaleString('pt-BR', { minimumFractionDigits: d || 0, maximumFractionDigits: d || 0 }); };
  D.brl = function (v) { return v === null || v === undefined ? '—' : 'R$ ' + D.nf(v, 2); };
  D.dt = function (iso) { return iso ? iso.slice(8, 10) + '/' + iso.slice(5, 7) + '/' + iso.slice(2, 4) : '—'; };
  D.dh = function (iso) { return iso ? iso.slice(8, 10) + '/' + iso.slice(5, 7) + ' ' + iso.slice(11, 16) : '—'; };
  D.hoje = function () { var d = new Date(); return d.getFullYear() + '-' + ('0' + (d.getMonth() + 1)).slice(-2) + '-' + ('0' + d.getDate()).slice(-2); };
  D.iniciais = function (n) { return (n || '?').split(' ').slice(0, 2).map(function (x) { return x[0]; }).join('').toUpperCase(); };
  D.pill = function (txt, cor) { return D.el('span', 'dv-pill ' + (cor || ''), txt); };
  D.kpi = function (rot, val, sub, cor) { var k = D.el('div', 'dv-kpi'); k.style.setProperty('--c', cor || '#2f7fe0'); k.appendChild(D.el('div', 'r', rot)); k.appendChild(D.el('div', 'v', val)); if (sub) k.appendChild(D.el('div', 's', sub)); return k; };
  D.barra = function (pct, cls) { var b = D.el('div', 'dv-barra ' + (cls || '')), s = D.el('span'); s.style.width = Math.max(0, Math.min(100, pct)) + '%'; b.appendChild(s); return b; };
  D.bloco = function (t) { return D.el('div', 'dv-bloco', t); };
  D.limpar = function (e) { while (e.firstChild) e.removeChild(e.firstChild); };
  /* formulario em janela: campos = [{k, rotulo, tipo: text|textarea|date|number|select, opcoes:[[valor, rotulo]], obrigatorio, meia}] */
  D.janela = function (titulo, campos, valores, botoes) {
    var fundo = D.el('div', 'dv-fundo'), m = D.el('div', 'dv-modal'), msg = D.el('div', 'dv-aviso'), ent = {}, linha = null;
    msg.style.display = 'none'; m.appendChild(D.el('h2', null, titulo)); m.appendChild(msg);
    campos.forEach(function (c) {
      var w = D.el('div', 'dv-campo'), i;
      w.appendChild(D.el('label', null, c.rotulo + (c.obrigatorio ? ' *' : '')));
      if (c.tipo === 'textarea') { i = D.el('textarea'); i.rows = 3; }
      else if (c.tipo === 'select') { i = D.el('select'); c.opcoes.forEach(function (o) { var op = D.el('option', null, o[1]); op.value = o[0]; i.appendChild(op); }); }
      else { i = D.el('input'); i.type = c.tipo || 'text'; if (c.tipo === 'number') { i.step = 'any'; i.min = c.min; i.max = c.max; } }
      var v = (valores || {})[c.k]; if (v !== undefined && v !== null) i.value = v;
      if (c.somenteLeitura) i.disabled = true;
      ent[c.k] = i; w.appendChild(i);
      if (c.meia) { if (!linha) { linha = D.el('div', 'dv-linha2'); m.appendChild(linha); } linha.appendChild(w); if (linha.children.length === 2) linha = null; } else { linha = null; m.appendChild(w); }
    });
    var rod = D.el('div', 'dv-rodape-modal');
    function fechar() { document.body.removeChild(fundo); }
    function lerTudo() { var o = {}; Object.keys(ent).forEach(function (k) { if (!ent[k].disabled) o[k] = ent[k].value; }); return o; }
    var cancela = D.el('button', 'dv-btn sec', 'Fechar'); cancela.type = 'button'; cancela.onclick = fechar; rod.appendChild(cancela);
    (botoes || []).forEach(function (b) {
      var bt = D.el('button', 'dv-btn ' + (b.cls || ''), b.rotulo); bt.type = 'button';
      bt.onclick = function () {
        var dados = lerTudo();
        for (var i = 0; i < campos.length; i++) { if (campos[i].obrigatorio && !(dados[campos[i].k] || '').trim() && !campos[i].somenteLeitura) { msg.textContent = 'Preencha: ' + campos[i].rotulo; msg.style.display = 'block'; return; } }
        bt.disabled = true; msg.style.display = 'none';
        Promise.resolve(b.fn(dados)).then(function () { fechar(); }).catch(function (e) { bt.disabled = false; msg.textContent = e.message; msg.style.display = 'block'; });
      };
      rod.appendChild(bt);
    });
    m.appendChild(rod); fundo.appendChild(m); fundo.addEventListener('mousedown', function (e) { if (e.target === fundo) fechar(); }); document.body.appendChild(fundo);
    return { corpo: m, fechar: fechar };
  };
  /* linha do tempo (Gantt) simples: itens = [{nome, inicio, prazo, cor, titulo}] */
  D.gantt = function (itens) {
    var com = itens.filter(function (x) { return x.prazo || x.inicio; });
    var box = D.el('div', 'dv-gantt');
    if (!com.length) { box.appendChild(D.el('div', 'dv-vazio', 'Sem datas para mostrar na linha do tempo.')); return box; }
    var dia = 86400000, ms = function (s) { return new Date(s + 'T00:00:00').getTime(); };
    var min = Math.min.apply(null, com.map(function (x) { return ms(x.inicio || x.prazo); })), max = Math.max.apply(null, com.map(function (x) { return ms(x.prazo || x.inicio); }));
    var h = ms(D.hoje()); min = Math.min(min, h) - 3 * dia; max = Math.max(max, h) + 5 * dia; var tot = max - min, larg = Math.max(tot / dia * 14, 700);
    var cab = D.el('div', 'dv-g-meses'); cab.appendChild(D.el('div', null, '')); var mes = D.el('div'); mes.style.width = larg + 'px';
    var d0 = new Date(min); d0.setDate(1); d0.setMonth(d0.getMonth() + 1);
    for (var d = d0; d.getTime() < max; d = new Date(d.getFullYear(), d.getMonth() + 1, 1)) { var s = D.el('span', null, ['jan', 'fev', 'mar', 'abr', 'mai', 'jun', 'jul', 'ago', 'set', 'out', 'nov', 'dez'][d.getMonth()] + '/' + String(d.getFullYear()).slice(2)); s.style.cssText = 'position:absolute;left:' + ((d.getTime() - min) / tot * 100) + '%'; mes.appendChild(s); }
    cab.appendChild(mes); box.appendChild(cab);
    com.forEach(function (x) {
      var l = D.el('div', 'dv-g-lin'), n = D.el('div', 'dv-g-nome', x.nome); n.title = x.titulo || x.nome; l.appendChild(n);
      var t = D.el('div', 'dv-g-trilho'); t.style.width = larg + 'px'; var a = ms(x.inicio || x.prazo), b = ms(x.prazo || x.inicio) + dia;
      var br = D.el('div', 'dv-g-barra'); br.style.cssText = 'left:' + ((a - min) / tot * 100) + '%;width:' + ((b - a) / tot * 100) + '%;--c:' + (x.cor || '#2f7fe0'); br.title = x.titulo || x.nome; t.appendChild(br);
      var hj = D.el('div', 'dv-g-hoje'); hj.style.left = ((h - min) / tot * 100) + '%'; t.appendChild(hj); l.appendChild(t); box.appendChild(l);
    });
    return box;
  };
  return D;
})();
