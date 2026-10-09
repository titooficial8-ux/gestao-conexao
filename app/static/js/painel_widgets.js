/* Widgets de painel (KPIs, graficos Chart.js, tabelas, detalhe) usados pelo BI Comercial.
   Extraido do painel Comercial ME. Uso: var ui = criarPainelUI({locale, txt, getFiltros, aoFiltrar, corpo}); */
window.criarPainelUI = function (cfg) {
  var LOCALE = cfg.locale, TXT = cfg.txt;
  var GRAFICOS = [];
  var filtrosDef = [];
  var $ = function (id) { return document.getElementById(id); };
    var PALETA = ['#2f7fe0', '#1f9d55', '#e0972f', '#e5484d', '#7a5af8', '#0e9fb3', '#d6409f', '#8892a6', '#5b8c00', '#b45309'];

    function el(tag, cls, txt) {
      var e = document.createElement(tag);
      if (cls) e.className = cls;
      if (txt !== undefined && txt !== null) e.textContent = txt;
      return e;
    }

    function nf(v, o) { return Number(v).toLocaleString(LOCALE, o || { maximumFractionDigits: 0 }); }

    function fmt(v, f, curto) {
      if (v === null || v === undefined || v === '') return '—';
      var n = Number(v);
      if (isNaN(n)) return String(v);
      var abs = Math.abs(n), s;
      switch (f) {
        case 'usd': case 'usd2': case 'brl': case 'brl2':
          var pre = f.slice(0, 3) === 'usd' ? 'US$ ' : 'R$ ';
          var dec = f.length === 4 ? 2 : 0;
          if (curto && abs >= 1e6) s = nf(n / 1e6, { maximumFractionDigits: 2 }) + ' mi';
          else if (curto && abs >= 1e4) s = nf(n / 1e3, { maximumFractionDigits: 0 }) + ' mil';
          else s = nf(n, { minimumFractionDigits: dec, maximumFractionDigits: dec });
          return pre + s;
        case 'm':
          if (curto && abs >= 1e4) return nf(n / 1e3, { maximumFractionDigits: 1 }) + ' mil m';
          return nf(n) + ' m';
        case 'pct': return nf(n, { maximumFractionDigits: 1 }) + '%';
        case 'dias': return nf(n) + ' ' + TXT.dias;
        default: return nf(n, { maximumFractionDigits: 1 });
      }
    }

    function valorTxt(g, ds, v, curto) {
      var f = (ds && ds._fmt) || g.fmt;
      if (g.cambio && g._dual && f === 'usd') return fmt(v, 'usd', curto) + ' · ' + fmt(v * g.cambio, 'brl', curto);
      return fmt(v, f, curto);
    }

    function pluginValores(g) {
      return { id: 'valores', afterDatasetsDraw: function (chart) {
        var ctx = chart.ctx, tipo = chart.config.type, hb = chart.options.indexAxis === 'y';
        var nSeries = chart.data.datasets.length, nPts = chart.data.labels.length, total = nSeries * nPts;
        var empilhado = !!(chart.options.scales && chart.options.scales.x && chart.options.scales.x.stacked && !hb);
        var denso = tipo !== 'doughnut' && (total > 18 || (nSeries > 1 && nPts > 4 && tipo === 'bar' && !hb));
        ctx.save(); ctx.textBaseline = 'middle';
        chart.data.datasets.forEach(function (ds, di) {
          var meta = chart.getDatasetMeta(di); if (meta.hidden) return;
          var maxI = -1, maxV = -Infinity;
          if (tipo === 'line' && denso) ds.data.forEach(function (v, i) { if (v > maxV) { maxV = v; maxI = i; } });
          meta.data.forEach(function (el, i) {
            var v = ds.data[i]; if (v === null || v === undefined || v === 0) return;
            var txt = valorTxt(g, ds, v, true);
            if (tipo === 'doughnut') {
              var tot = ds.data.reduce(function (a, b) { return a + (b || 0); }, 0);
              if (!tot || v / tot < 0.07) return;
              var c = el.tooltipPosition(); ctx.font = '700 11.5px sans-serif'; ctx.fillStyle = '#fff'; ctx.textAlign = 'center';
              ctx.fillText(Math.round(v / tot * 100) + '%', c.x, c.y); return;
            }
            if (empilhado) {
              var h = Math.abs(el.base - el.y); if (h < 15) return;
              ctx.font = '700 10.5px sans-serif'; ctx.fillStyle = '#fff'; ctx.textAlign = 'center';
              ctx.fillText(txt, el.x, (el.y + el.base) / 2); return;
            }
            if (tipo === 'line') {
              if (denso && i !== maxI && i !== nPts - 1) return;
              var pos = el.tooltipPosition(); ctx.font = '700 11px sans-serif'; ctx.fillStyle = '#10263F'; ctx.textAlign = 'center';
              ctx.fillText(txt, Math.min(pos.x, chart.chartArea.right - 18), pos.y - 12); return;
            }
            ctx.fillStyle = '#10263F';
            if (hb) { ctx.font = '700 11.5px sans-serif'; ctx.textAlign = 'left'; ctx.fillText(txt, (v >= 0 ? el.x : el.base) + (v >= 0 ? 6 : -6 - ctx.measureText(txt).width), el.y); }
            else if (denso) { ctx.font = '700 10.5px sans-serif'; ctx.textAlign = 'left'; ctx.save(); ctx.translate(el.x, el.y - 6); ctx.rotate(-Math.PI / 2); ctx.fillText(txt, 0, 0); ctx.restore(); }
            else { ctx.font = '700 11.5px sans-serif'; ctx.textAlign = 'center'; ctx.fillText(txt, el.x, (v >= 0 ? el.y - 10 : el.y + 12)); }
          });
        });
        ctx.restore();
      } };
    }

    function cartaoKpi(k) {
      var c = el('div', 'cme-kpi' + (k.tom ? ' ' + k.tom : '') + ' cme-clicavel');
      c.addEventListener('click', function () { var t = document.querySelector('#cmeCorpo .cme-tcard'); if (t) t.scrollIntoView({ behavior: 'smooth', block: 'start' }); });
      c.appendChild(el('span', 'cme-kpi-rotulo', k.rotulo));
      c.appendChild(el('strong', null, fmt(k.valor, k.fmt, true)));
      if (k.alt) c.appendChild(el('div', 'cme-kpi-alt', '≈ ' + fmt(k.alt.valor, k.alt.fmt, true)));
      var rod = el('div', 'cme-kpi-rodape');
      if (k.delta !== null && k.delta !== undefined) {
        rod.appendChild(el('span', 'cme-delta ' + (k.delta >= 0 ? 'up' : 'down'), (k.delta >= 0 ? '▲ +' : '▼ ') + nf(k.delta, { maximumFractionDigits: 1 }) + '%'));
      }
      if (k.nota) rod.appendChild(el('small', null, k.nota));
      if (rod.childNodes.length) c.appendChild(rod);
      return c;
    }

    function cartaoGrafico(g, lista) {
      var c = el('div', 'table-card cme-grafico' + (g.largura === 'inteira' ? ' inteira' : ''));
      c.appendChild(el('h3', null, g.titulo));
      var caixa = el('div', 'cme-canvas' + (g.tipo === 'hbar' ? ' alto' : '')); var cv = document.createElement('canvas'); caixa.appendChild(cv); c.appendChild(caixa);
      if (g.nota) c.appendChild(el('small', 'cme-meta', g.nota));
      var vazio = !g.labels.length || !g.series.some(function (s) { return s.dados.some(function (v) { return v; }); });
      if (vazio) { caixa.textContent = ''; caixa.appendChild(el('p', 'ticket-empty', TXT.sem_dados)); return c; }
      var rosca = g.tipo === 'doughnut', hbar = g.tipo === 'hbar';
      var serieR = false;
      if (g.cambio && g.fmt === 'usd') {
        if ((g.tipo === 'bar' || g.tipo === 'line') && g.series.length === 1 && !g.empilhado) {
          serieR = true;
          g.series = [g.series[0], { nome: 'R$ (câmbio ' + nf(g.cambio, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + ')', dados: g.series[0].dados.map(function (v) { return v === null ? null : Math.round(v * g.cambio); }), _fmt: 'brl', eixo: 'y1' }];
          g.series[0]._fmt = 'usd'; g._dual = false;
        } else g._dual = true;
      }
      var tipo = rosca ? 'doughnut' : (g.tipo === 'line' ? 'line' : 'bar');
      var datasets = g.series.map(function (s, i) {
        var cor = (g.series.length === 1 && g.cores) ? g.cores : PALETA[i % PALETA.length];
        if (rosca) cor = g.cores || PALETA;
        var d = { label: s.nome, data: s.dados, backgroundColor: cor, borderColor: tipo === 'line' ? PALETA[i % PALETA.length] : cor, _fmt: s._fmt };
        if (s.eixo) d.yAxisID = s.eixo;
        if (tipo === 'line') { d.tension = 0.25; d.pointRadius = 3; d.backgroundColor = PALETA[i % PALETA.length]; d.spanGaps = true; }
        if (tipo === 'bar') d.borderRadius = 4;
        if (rosca) d.borderWidth = 1;
        return d;
      });
      var op = {
        responsive: true, maintainAspectRatio: false,
        layout: { padding: { top: (g.tipo === 'bar' && (g.series.length * g.labels.length > 18 || (g.series.length > 1 && g.labels.length > 4)) && !g.empilhado) ? 62 : 22, right: hbar ? (g._dual ? 170 : 78) : (serieR ? 12 : 12) } },
        plugins: {
          legend: { display: rosca || g.series.length > 1 || serieR, position: rosca ? 'right' : 'bottom', labels: { boxWidth: 12, generateLabels: rosca ? function (ch) { var d = ch.data.datasets[0]; return ch.data.labels.map(function (l, i) { return { text: l + ' — ' + valorTxt(g, d, d.data[i], true), fillStyle: Array.isArray(d.backgroundColor) ? d.backgroundColor[i] : d.backgroundColor, strokeStyle: '#fff', index: i, hidden: false }; }); } : undefined } },
          tooltip: { callbacks: { label: function (ctx) {
            var v = rosca ? ctx.parsed : (hbar ? ctx.parsed.x : ctx.parsed.y);
            var pre = (g.series.length > 1 || rosca) ? (ctx.dataset.label && !rosca ? ctx.dataset.label + ': ' : (rosca ? ctx.label + ': ' : '')) : '';
            return pre + valorTxt(g, ctx.dataset, v);
          } } }
        }
      };
      var alvo = function (i) {
        var lab = g.labels[i], achado = null;
        filtrosDef.forEach(function (f) { if (!achado && f.id !== 'ano' && f.id !== 'origem') f.opcoes.forEach(function (o) { if (o.v && o.t === lab) achado = { f: f.id, v: o.v }; }); });
        return achado;
      };
      if (!rosca || true) {
        op.onClick = function (ev, els) {
          if (!els.length) return; var a = alvo(els[0].index);
          if (a) { cfg.aoFiltrar(a.f, a.v); window.scrollTo({ top: 0, behavior: 'smooth' }); }
          else abrirDetalhe(g.titulo + ' — ' + g.labels[els[0].index], g.series.map(function (sr) { return [sr.nome, valorTxt(g, sr, sr.dados[els[0].index])]; }));
        };
        op.onHover = function (ev, els) { ev.native.target.style.cursor = els.length ? 'pointer' : 'default'; };
      }
      if (!rosca) {
        var eixoV = { beginAtZero: true, ticks: { callback: function (v) { return fmt(v, g.fmt, true); } }, stacked: !!g.empilhado };
        var eixoC = { stacked: !!g.empilhado, ticks: { autoSkip: false, maxRotation: 50 }, grid: { display: false } };
        if (hbar) { op.indexAxis = 'y'; op.scales = { x: eixoV, y: { ticks: { autoSkip: false }, grid: { display: false }, stacked: !!g.empilhado } }; }
        else if (serieR) {
          eixoV.title = { display: true, text: 'US$' };
          op.scales = { x: eixoC, y: eixoV, y1: { position: 'right', beginAtZero: true, grid: { drawOnChartArea: false }, title: { display: true, text: 'R$' }, ticks: { callback: function (v) { return fmt(v, 'brl', true); } } } };
          op.layout.padding.right = 12;
        }
        else op.scales = { x: eixoC, y: eixoV };
      }
      (lista || GRAFICOS).push(new Chart(cv, { type: tipo, data: { labels: g.labels, datasets: datasets }, options: op, plugins: [pluginValores(g)] }));
      return c;
    }

    function celula(td, v, tipo, cambio) {
      if (tipo === 'badge' && v && typeof v === 'object') { td.appendChild(el('span', 'cme-badge ' + v[1], v[0])); return; }
      if (v === null || v === undefined || v === '') { td.textContent = '—'; return; }
      switch (tipo) {
        case 'usd': case 'usd2': case 'brl': case 'brl2': case 'm': case 'int': case 'num': case 'pct': case 'dias':
          td.textContent = typeof v === 'number' ? fmt(v, tipo === 'num' ? 'int' : tipo) : v; td.className = 'cme-num';
          if (cambio && typeof v === 'number' && (tipo === 'usd' || tipo === 'usd2')) { td.appendChild(el('div', 'cme-celula-alt', fmt(v * cambio, tipo === 'usd' ? 'brl' : 'brl2'))); }
          break;
        case 'pct_delta':
          td.textContent = typeof v === 'number' ? (v > 0 ? '+' : '') + nf(v, { maximumFractionDigits: 1 }) + '%' : v;
          td.className = 'cme-num ' + (v > 0 ? 'cme-pos' : v < 0 ? 'cme-neg' : ''); break;
        default: td.textContent = String(v);
      }
    }

    function cartaoTabela(t) {
      var c = el('div', 'table-card cme-tcard');
      c.appendChild(el('h3', null, t.titulo));
      if (t.nota) c.appendChild(el('p', 'cme-meta', t.nota));
      if (!t.linhas.length) { c.appendChild(el('p', 'ticket-empty', t.vazio || TXT.nenhum)); return c; }
      var LIM = 12, aberto = false;
      var wrap = el('div', 'table-scroll cme-scroll'); var tab = el('table', 'data-table cme-tabela'); wrap.appendChild(tab);
      var thead = el('thead'), tr = el('tr');
      t.colunas.forEach(function (col) { var th = el('th', ['usd','usd2','brl','brl2','m','int','num','pct','pct_delta','dias'].indexOf(col.tipo) !== -1 ? 'cme-num' : null, col.nome); tr.appendChild(th); });
      thead.appendChild(tr); tab.appendChild(thead);
      var tb = el('tbody'); tab.appendChild(tb);
      function pintar() {
        tb.textContent = '';
        t.linhas.slice(0, aberto ? t.linhas.length : LIM).forEach(function (l) {
          var r = el('tr'); r.className = 'cme-clicavel';
          r.addEventListener('click', function () { abrirDetalhe(t.titulo, t.colunas.map(function (col, j) { var d = el('td'); celula(d, l[j], col.tipo, t.cambio); return [col.nome, Array.prototype.map.call(d.childNodes, function (n) { return n.textContent; }).join(' · ')]; })); });
          t.colunas.forEach(function (col, j) { var td = el('td'); celula(td, l[j], col.tipo, t.cambio); if (td.textContent.length > 28) td.title = td.textContent; r.appendChild(td); });
          tb.appendChild(r);
        });
      }
      pintar(); c.appendChild(wrap);
      if (t.linhas.length > LIM) {
        var b = el('button', 'btn-small ticket-btn-outline cme-vermais', TXT.ver_todas + ' (' + t.linhas.length + ')'); b.type = 'button';
        b.addEventListener('click', function () { aberto = !aberto; b.textContent = aberto ? TXT.ver_menos : TXT.ver_todas + ' (' + t.linhas.length + ')'; wrap.classList.toggle('aberta', aberto); pintar(); });
        c.appendChild(b);
      }
      return c;
    }

    function abrirDetalhe(titulo, pares) {
      $('cmeModalTit').textContent = titulo; var c = $('cmeModalCorpo'); c.textContent = '';
      var tb = el('table', 'data-table cme-detalhe');
      pares.forEach(function (p) { var r = el('tr'); r.appendChild(el('th', null, p[0])); r.appendChild(el('td', null, p[1] === null || p[1] === '' ? '—' : p[1])); tb.appendChild(r); });
      c.appendChild(tb); $('cmeModal').hidden = false;
    }

  return {
    el: el, nf: nf, fmt: fmt,
    cartaoKpi: cartaoKpi, cartaoGrafico: function (g) { return cartaoGrafico(g, GRAFICOS); }, cartaoTabela: cartaoTabela, abrirDetalhe: abrirDetalhe,
    limpar: function () { GRAFICOS.forEach(function (g) { g.destroy(); }); GRAFICOS = []; },
    setFiltros: function (f) { filtrosDef = f || []; }
  };
};
