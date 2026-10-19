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

  /* ---- biblioteca de arquivos (laudos, produtos tecnologicos, material institucional) ---- */
  D.tam = function (b) { return b >= 1073741824 ? D.nf(b / 1073741824, 2) + ' GB' : (b >= 1048576 ? D.nf(b / 1048576, 1) + ' MB' : D.nf(b / 1024, 0) + ' KB'); };
  D.enviar = function (url, form, prog) {
    return new Promise(function (ok, erro) {
      var x = new XMLHttpRequest(); x.open('POST', url); x.withCredentials = true;
      x.upload.onprogress = function (e) { if (e.lengthComputable && prog) prog(e.loaded / e.total, e.loaded, e.total); };
      x.onload = function () { var j = {}; try { j = JSON.parse(x.responseText); } catch (e) {} if (x.status >= 200 && x.status < 300) ok(j); else erro(new Error(j.erro || (x.status === 413 ? 'Arquivo grande demais.' : 'Falha no envio (' + x.status + ').'))); };
      x.onerror = function () { erro(new Error('Falha de conexão durante o envio.')); }; x.send(form);
    });
  };
  /* cfg: {raiz, secoes:[{id, rotulo, aceita, categorias:[..], referencia:bool, grade:bool}], eu} */
  D.biblioteca = function (cfg) {
    var raiz = cfg.raiz, atual = cfg.secoes[0], lista = [], busca = '', cat = '';
    var topo = D.el('div', 'dv-topo'), sub = D.el('div', 'dv-grupo'), barra = D.el('div', 'dv-acoes'), corpo = D.el('div'), caixa = D.el('input', 'dv-in'); caixa.placeholder = 'Buscar…'; caixa.style.minWidth = '220px';
    var cats = D.el('div', 'dv-grupo');
    if (cfg.secoes.length > 1) { cfg.secoes.forEach(function (sc) { var b = D.el('button', 'dv-chip', sc.rotulo); b.type = 'button'; b.onclick = function () { atual = sc; cat = ''; carregar(); }; sc.botao = b; sub.appendChild(b); }); raiz.appendChild(sub); }
    barra.appendChild(caixa); if (cfg.eu.dev) { var novo = D.el('button', 'dv-btn', '+ Enviar arquivo'); novo.type = 'button'; novo.onclick = enviar; barra.appendChild(novo); } else barra.appendChild(D.el('span', 'dv-leitura', 'Modo visualização'));
    raiz.appendChild(barra); barra.style.marginBottom = '12px'; raiz.appendChild(cats); raiz.appendChild(corpo);
    caixa.oninput = function () { busca = caixa.value.toLowerCase(); desenhar(); };
    function icone(a) { return a.ext === 'pdf' ? '📄' : (['mp4', 'mov', 'webm', 'm4v', 'avi', 'mkv'].indexOf(a.ext) >= 0 ? '🎬' : (['png', 'jpg', 'jpeg', 'gif', 'webp'].indexOf(a.ext) >= 0 ? '🖼' : (['zip', 'rar', '7z'].indexOf(a.ext) >= 0 ? '🗜' : '📎'))); }
    function filtrados() { return lista.filter(function (a) { return (!cat || a.categoria === cat) && (!busca || (a.titulo + ' ' + (a.descricao || '') + ' ' + (a.referencia || '') + ' ' + a.nome + ' ' + (a.categoria || '')).toLowerCase().indexOf(busca) >= 0); }); }
    function abrir(a) {
      var jan = D.janela(a.titulo, [], {}, []), m = jan.corpo, rod = m.lastChild, url = '/dev/arquivos/' + a.id + '/ver', el;
      m.style.width = 'min(1000px,100%)';
      if (a.ext === 'pdf') { el = D.el('iframe'); el.src = url; el.style.cssText = 'width:100%;height:70vh;border:1px solid #e1e9f3;border-radius:8px'; }
      else if (['mp4', 'mov', 'webm', 'm4v'].indexOf(a.ext) >= 0) { el = D.el('video'); el.src = url; el.controls = true; el.preload = 'metadata'; el.style.cssText = 'width:100%;max-height:70vh;background:#000;border-radius:8px'; }
      else if (['png', 'jpg', 'jpeg', 'gif', 'webp'].indexOf(a.ext) >= 0) { el = D.el('img'); el.src = url; el.style.cssText = 'max-width:100%;max-height:70vh;display:block;margin:auto;border-radius:8px'; }
      else { el = D.el('div', 'dv-vazio', 'Este tipo de arquivo não abre no navegador. Use "Baixar".'); }
      m.insertBefore(el, rod); if (a.descricao) { var p = D.el('p', null, a.descricao); p.style.cssText = 'font-size:13px;color:#46586e'; m.insertBefore(p, rod); }
      var bx = D.el('a', 'dv-btn', 'Baixar'); bx.href = '/dev/arquivos/' + a.id + '/baixar'; bx.style.textDecoration = 'none'; rod.insertBefore(bx, rod.firstChild);
      var v = el.tagName === 'VIDEO' ? el : null; if (v) { var f = rod.lastChild; f.addEventListener('click', function () { v.pause(); }); }
    }
    function excluir(a) { if (!confirm('Excluir "' + a.titulo + '"?')) return; D.api('/dev/arquivos/' + a.id, 'DELETE').then(carregar).catch(function (e) { alert(e.message); }); }
    function acoes(a) { var d = D.el('div'); d.style.cssText = 'display:flex;gap:6px;flex-wrap:wrap'; var ab = D.el('button', 'dv-btn', a.inline ? 'Abrir' : 'Detalhes'); ab.type = 'button'; ab.onclick = function (e) { e.stopPropagation(); abrir(a); }; d.appendChild(ab);
      var bx = D.el('a', 'dv-btn sec', 'Baixar'); bx.href = '/dev/arquivos/' + a.id + '/baixar'; bx.style.textDecoration = 'none'; bx.onclick = function (e) { e.stopPropagation(); }; d.appendChild(bx);
      if (a.pode_excluir) { var ex = D.el('button', 'dv-btn sec', 'Excluir'); ex.type = 'button'; ex.onclick = function (e) { e.stopPropagation(); excluir(a); }; d.appendChild(ex); } return d; }
    function desenhar() {
      D.limpar(cats); var cs = atual.categorias || []; if (cs.length) { [''].concat(cs).forEach(function (c) { var b = D.el('button', 'dv-chip' + (cat === c ? ' active' : ''), c || 'Todos'); b.type = 'button'; b.onclick = function () { cat = c; desenhar(); }; cats.appendChild(b); }); }
      cfg.secoes.forEach(function (sc) { if (sc.botao) sc.botao.className = 'dv-chip' + (sc === atual ? ' active' : ''); });
      D.limpar(corpo); var l = filtrados();
      if (!l.length) { var v = D.el('div', 'dv-card'); v.appendChild(D.el('div', 'dv-vazio', lista.length ? 'Nenhum arquivo encontrado para o filtro.' : 'Nenhum arquivo enviado ainda.' + (cfg.eu.dev ? ' Use "+ Enviar arquivo".' : ''))); corpo.appendChild(v); return; }
      if (atual.grade) {
        var g = D.el('div'); g.style.cssText = 'display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:12px';
        l.forEach(function (a) { var c = D.el('div', 'dv-card'); c.style.marginBottom = '0'; var t = D.el('div'); t.style.cssText = 'font-size:34px;text-align:center;background:#f3f6fb;border-radius:10px;padding:16px;margin-bottom:10px;cursor:pointer'; t.textContent = icone(a); t.onclick = function () { abrir(a); }; c.appendChild(t);
          c.appendChild(D.el('h3', null, a.titulo)); if (a.categoria) c.appendChild(D.pill(a.categoria, 'azul')); var m = D.el('div', 'dv-sub', D.tam(a.tamanho) + ' · ' + (a.enviado_por || '') + ' · ' + D.dt(a.enviado_em)); m.style.margin = '6px 0 10px'; c.appendChild(m); c.appendChild(acoes(a)); g.appendChild(c); });
        corpo.appendChild(g);
      } else {
        var card = D.el('div', 'dv-card'), t = D.el('table', 'dv-tabela'), h = D.el('thead'), r = D.el('tr'); ['', 'Título', 'Categoria'].concat(atual.referencia ? ['Artigo / cliente'] : []).concat(['Tamanho', 'Enviado por', 'Data', '']).forEach(function (x) { r.appendChild(D.el('th', null, x)); }); h.appendChild(r); t.appendChild(h); var b = D.el('tbody');
        l.forEach(function (a) { var tr = D.el('tr', 'clic'); tr.appendChild(D.el('td', null, icone(a))); var td = D.el('td'); td.appendChild(D.el('b', null, a.titulo)); if (a.descricao) { var d = D.el('div', 'dv-sub', a.descricao); td.appendChild(d); } tr.appendChild(td);
          var c = D.el('td'); if (a.categoria) c.appendChild(D.pill(a.categoria, 'azul')); tr.appendChild(c); if (atual.referencia) tr.appendChild(D.el('td', null, a.referencia || '—')); tr.appendChild(D.el('td', null, D.tam(a.tamanho))); tr.appendChild(D.el('td', null, a.enviado_por || '—')); tr.appendChild(D.el('td', null, D.dt(a.enviado_em)));
          var ac = D.el('td'); ac.appendChild(acoes(a)); tr.appendChild(ac); tr.onclick = function () { abrir(a); }; b.appendChild(tr); });
        t.appendChild(b); card.appendChild(t); corpo.appendChild(card);
      }
    }
    function carregar() { return D.api('/dev/arquivos?secao=' + atual.id).then(function (l) { lista = l; desenhar(); }); }
    function enviar() {
      var fundo = D.el('div', 'dv-fundo'), m = D.el('div', 'dv-modal'), msg = D.el('div', 'dv-aviso'); msg.style.display = 'none'; m.appendChild(D.el('h2', null, 'Enviar arquivo · ' + atual.rotulo)); m.appendChild(msg);
      function campo(r, i) { var w = D.el('div', 'dv-campo'); w.appendChild(D.el('label', null, r)); w.appendChild(i); m.appendChild(w); return i; }
      var arq = D.el('input'); arq.type = 'file'; if (atual.aceita) arq.accept = atual.aceita; campo('Arquivo' + (atual.maximo ? ' (até ' + atual.maximo + ')' : ''), arq);
      var tit = campo('Título (opcional)', D.el('input')), ct = null, rf = null;
      if (atual.categorias && atual.categorias.length) { ct = D.el('select'); atual.categorias.forEach(function (c) { var o = D.el('option', null, c); o.value = c; ct.appendChild(o); }); if (cat) ct.value = cat; campo('Categoria', ct); }
      if (atual.referencia) rf = campo('Artigo / cliente', D.el('input')); var ds = D.el('textarea'); ds.rows = 3; campo('Descrição', ds);
      var pb = D.barra(0, 'v'); pb.style.display = 'none'; pb.style.margin = '8px 0'; m.appendChild(pb); var pt = D.el('div', 'dv-sub'); m.appendChild(pt);
      var rod = D.el('div', 'dv-rodape-modal'), fe = D.el('button', 'dv-btn sec', 'Fechar'), ok = D.el('button', 'dv-btn', 'Enviar'); fe.type = ok.type = 'button'; rod.appendChild(fe); rod.appendChild(ok); m.appendChild(rod); fundo.appendChild(m); document.body.appendChild(fundo);
      var em = false; fe.onclick = function () { if (em && !confirm('O envio está em andamento. Fechar mesmo assim?')) return; document.body.removeChild(fundo); };
      ok.onclick = function () {
        if (!arq.files.length) { msg.textContent = 'Escolha um arquivo.'; msg.style.display = 'block'; return; }
        var f = new FormData(); f.append('secao', atual.id); f.append('arquivo', arq.files[0]); f.append('titulo', tit.value); f.append('descricao', ds.value); if (ct) f.append('categoria', ct.value); if (rf) f.append('referencia', rf.value);
        ok.disabled = true; em = true; msg.style.display = 'none'; pb.style.display = 'block';
        D.enviar('/dev/arquivos', f, function (p, a, b) { pb.firstChild.style.width = Math.round(p * 100) + '%'; pt.textContent = Math.round(p * 100) + '% · ' + D.tam(a) + ' de ' + D.tam(b); })
          .then(function () { em = false; document.body.removeChild(fundo); return carregar(); }).catch(function (e) { em = false; ok.disabled = false; pb.style.display = 'none'; msg.textContent = e.message; msg.style.display = 'block'; });
      };
    }
    return carregar();
  };
  return D;
})();
