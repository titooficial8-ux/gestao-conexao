"""Graficos mensais no mesmo estilo do BI Comercial (barras do ano, linha verde do plano, area cinza do ano anterior, rotulos e chips), para outras series
(colchao, mercado interno, mercado externo). Desenha com o proprio Chart.js e o CSS do Gestao num Chromium invisivel e devolve a foto (PNG)."""
from __future__ import annotations

import json
import os
from pathlib import Path

RAIZ = Path(__file__).parent / 'static'

HTML = """<!doctype html><html><head><meta charset="utf-8"><style>__CSS__
body{margin:0;padding:12px;background:#fff;font-family:Inter,'Segoe UI',Arial,sans-serif}.table-card.sl-grafico{margin:0;width:1380px;box-sizing:border-box}</style></head><body>
<div class="table-card sl-grafico" id="alvo"><div class="sl-g-topo"><div><h3 id="tit"></h3><p class="cme-meta" id="sub"></p></div><div class="sl-chips" id="chips"></div></div>
<div class="sl-g-canvas"><canvas id="cv"></canvas></div></div>
<script>__CHART__</script>
<script>
var C = __DATA__, MESES = ['Janeiro','Fevereiro','Março','Abril','Maio','Junho','Julho','Agosto','Setembro','Outubro','Novembro','Dezembro'];
function nf(v, d) { return v.toLocaleString('pt-BR', { minimumFractionDigits: d || 0, maximumFractionDigits: d || 0 }); }
var n = C.unidade === 'mts' ? function (v) { return nf(Math.round(v)); } : function (v) { return nf(Math.round(v / 1000)); };
var txt = C.unidade === 'mts' ? function (v) { return n(v) + ' mts'; } : function (v) { return 'R$ ' + n(v) + ' mil'; };
document.getElementById('tit').textContent = C.titulo + ' — ' + C.ano;
document.getElementById('sub').textContent = C.sub + C.ano_ant + (C.S2 ? ' · linha laranja = ' + C.ano2 : '');
var S = C.S, tot = C.tot, chips = document.getElementById('chips');
function chip(r, v, cls) { var c = document.createElement('div'); c.className = 'sl-chip ' + (cls || ''); c.innerHTML = '<span>' + r + '</span><strong>' + v + '</strong>'; chips.appendChild(c); }
chip('Acumulado realizado', txt(tot.real)); chip('Plano acumulado', txt(tot.plano));
chip('% do plano', tot.plano ? nf(tot.real / tot.plano * 100) + '%' : '–', tot.real >= tot.plano ? 'ok' : 'baixo');
chip('vs ' + C.ano_ant, (tot.v_ant >= 0 ? '+' : '') + nf(tot.v_ant * 100, 1) + '%', tot.v_ant >= 0 ? 'ok' : 'baixo');
var labels = S.map(function (x) { return MESES[x.mes - 1].slice(0, 3); }), real = S.map(function (x) { return x.real ? Math.round(x.real) : null; });
var rotulos = { id: 'rotulos', afterDatasetsDraw: function (ch) {
  var ctx = ch.ctx; ctx.save(); ctx.font = '700 11px sans-serif'; ctx.textAlign = 'center'; ctx.textBaseline = 'bottom';
  ch.getDatasetMeta(0).data.forEach(function (el, i) { var v = real[i]; if (!v) return; var h = el.base - el.y; ctx.fillStyle = h > 24 ? '#fff' : '#10263F'; ctx.fillText(n(v), el.x, h > 24 ? el.base - 8 : el.y - 6); });
  function etiqueta(t, x, y, cor) { ctx.font = '700 10.5px sans-serif'; var w = ctx.measureText(t).width + 10, h = 17; ctx.fillStyle = 'rgba(255,255,255,0.92)'; ctx.strokeStyle = cor; ctx.lineWidth = 1;
    ctx.beginPath(); if (ctx.roundRect) ctx.roundRect(x - w / 2, y - h / 2, w, h, 6); else ctx.rect(x - w / 2, y - h / 2, w, h); ctx.fill(); ctx.stroke(); ctx.fillStyle = cor; ctx.textBaseline = 'middle'; ctx.textAlign = 'center'; ctx.fillText(t, x, y + 0.5); }
  [[1, '#11703c', -17, S.map(function (x) { return x.plano; })], [2, '#6b7686', 19, S.map(function (x) { return x.ant; })]].forEach(function (cfg) {
    ch.getDatasetMeta(cfg[0]).data.forEach(function (el, i) { var v = cfg[3][i]; if (!v) return; etiqueta(n(v), el.x, Math.min(Math.max(el.y + cfg[2], 12), ch.chartArea.bottom - 28), cfg[1]); }); });
  if (C.S2) ch.getDatasetMeta(3).data.forEach(function (el, i) { var v = C.S2[i]; if (!v) return; etiqueta(n(v), el.x, Math.min(Math.max(el.y + 40, 12), ch.chartArea.bottom - 28), '#a85a00'); });
  ctx.restore(); } };
Chart.defaults.animation = false;
new Chart(document.getElementById('cv'), { data: { labels: labels, datasets: [
  { type: 'bar', label: String(C.ano), data: real, order: 3, borderRadius: 6, maxBarThickness: 46, backgroundColor: S.map(function (x) { return x.mes === C.mes ? '#0a3d8f' : 'rgba(47,127,224,0.88)'; }) },
  { type: 'line', label: 'Plano', data: S.map(function (x) { return Math.round(x.plano); }), order: 1, borderColor: '#1f9d55', backgroundColor: '#1f9d55', borderWidth: 2.5, borderDash: [7, 5], pointRadius: 4, pointBackgroundColor: '#fff', pointBorderWidth: 2, tension: 0.25 },
  { type: 'line', label: String(C.ano_ant), data: S.map(function (x) { return Math.round(x.ant); }), order: 2, borderColor: '#9aa6bc', backgroundColor: 'rgba(154,166,188,0.15)', fill: true, borderWidth: 2, pointRadius: 0, tension: 0.25 }].concat(C.S2 ? [{ type: 'line', label: String(C.ano2), data: C.S2.map(function (v) { return Math.round(v); }), order: 2, borderColor: '#e0912b', backgroundColor: '#e0912b', borderWidth: 2, pointRadius: 3, pointBackgroundColor: '#fff', pointBorderWidth: 2, tension: 0.25 }] : []) },
  options: { responsive: true, maintainAspectRatio: false, animation: false, layout: { padding: { top: 26, right: 8 } },
    plugins: { legend: { position: 'bottom', labels: { usePointStyle: true, boxWidth: 9, padding: 18 } }, tooltip: { enabled: false } },
    scales: { x: { grid: { display: false }, ticks: { font: { weight: '600' } } }, y: { beginAtZero: true, grid: { color: 'rgba(120,130,150,0.15)' }, border: { display: false }, ticks: { callback: function (v) { return n(v); } } } } },
  plugins: [rotulos] });
document.body.setAttribute('data-pronto', '1');
</script></body></html>"""


def _resumo(S, mes):
    ate = [x for x in S if x['mes'] <= mes]
    real, plano, ant = (sum(x[k] or 0 for x in ate) for k in ('real', 'plano', 'ant'))
    return dict(real=real, plano=plano, v_ant=(real / ant - 1) if ant else 0)


def renderizar(itens: list, ano: int, mes: int) -> list[bytes]:
    """itens: [dict(titulo, sub, unidade 'rs'|'mts', S=[{mes, ant, real, plano}])]. Devolve um PNG por item, na mesma ordem."""
    from playwright.sync_api import sync_playwright
    css = (RAIZ / 'css' / 'style.css').read_text(encoding='utf-8')
    chart = (RAIZ / 'js' / 'vendor' / 'chart.umd.js').read_text(encoding='utf-8')
    saida = []
    with sync_playwright() as pw:
        nav = pw.chromium.launch(executable_path=os.getenv('RELATORIO_FAT_CHROMIUM') or None)
        try:
            pg = nav.new_page(viewport={'width': 1500, 'height': 700}, device_scale_factor=2)
            for it in itens:
                dados = dict(it, ano=ano, ano_ant=ano - 1, mes=mes, tot=_resumo(it['S'], mes))
                html = HTML.replace('__CSS__', css).replace('__CHART__', chart).replace('__DATA__', json.dumps(dados, ensure_ascii=False))
                pg.set_content(html, wait_until='load')
                pg.wait_for_selector('body[data-pronto="1"]', timeout=30000)
                pg.wait_for_timeout(300)
                saida.append(pg.locator('#alvo').screenshot(type='png'))
        finally:
            nav.close()
    return saida
