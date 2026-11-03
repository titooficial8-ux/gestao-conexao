"""Story: PDF de uma pagina com a programacao de todas as maquinas (1o RODANDO + fila EM SEQUENCIA), igual ao botao Story do PCP Hub."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from flask import render_template_string

HTML = """<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><style>
@page{size:A4;margin:8mm}*{box-sizing:border-box}
body{margin:0;font-family:Arial,Helvetica,sans-serif;color:#1f2937;width:194mm}
h1{margin:0;text-align:center;font-size:15px;letter-spacing:.04em;color:#9ca3af;font-weight:700}
.sub{text-align:center;font-size:9px;color:#6b7280;margin:3px 0 8px}
.cols{column-count:2;column-gap:12px}
.maq{break-inside:avoid;margin:0 0 7px}
.maq h2{margin:0 0 2px;font-size:11px;color:#9ca3af;font-weight:700;text-transform:uppercase}
.rod{border:1px solid #0f766e;border-bottom:0;padding:3px 5px;font-size:9.5px;line-height:1.35}
.rod b.t{color:#0f766e;text-transform:uppercase}
.rod.vazio{color:#6b7280}
table{width:100%;border-collapse:collapse;font-size:9.5px;border:1px solid #0f766e}
th{color:#9ca3af;font-weight:700;text-transform:uppercase;font-size:9px;padding:2px 4px;border:1px solid #d1d5db}
td{padding:2px 4px;border:1px solid #d1d5db;text-align:center}td.l{text-align:left}
.sem{border:1px solid #0f766e;border-top:0;font-size:9px;color:#6b7280;padding:2px 5px}
</style></head><body>
<h1>PROGRAMAÇÃO POR MÁQUINA — EM SEQUÊNCIA</h1>
<div class="sub">{{ data }} · {{ maquinas|length }} máquinas · {{ total_ops }} OPs</div>
<div class="cols">
{% for m in maquinas %}<div class="maq"><h2>Máquina {{ m.numero }}</h2>
{% if m.rodando %}<div class="rod"><b class="t">1º Rodando</b><br><b>Artigo:</b> {{ m.rodando.artigo }} <b>OP:</b> {{ m.rodando.op }} <b>Cliente:</b> {{ m.rodando.cliente }}</div>
{% else %}<div class="rod vazio"><b class="t">Sem artigo rodando</b></div>{% endif %}
{% if m.fila %}<table><tr><th style="width:9%">Nº</th><th>Artigo</th><th>Cliente</th><th style="width:17%">OP</th></tr>
{% for q in m.fila %}<tr><td>{{ q.n }}</td><td>{{ q.artigo }}</td><td class="l">{{ q.cliente }}</td><td>{{ q.op }}</td></tr>{% endfor %}</table>
{% else %}<div class="sem">Sem OPs em sequência</div>{% endif %}</div>
{% endfor %}</div></body></html>"""


def dados() -> dict:
    from app.models import Machine, Sequencia
    por = {}
    for q in Sequencia.query.join(Machine).all():
        if q.machine and q.status in ('PRODUZINDO', 'EM_SEQUENCIA'):
            por.setdefault(q.machine.number, []).append(q)
    maquinas, total = [], 0
    for n in sorted(por):
        itens = sorted(por[n], key=lambda x: x.sequence_order or 0)
        rod = next((x for x in itens if x.status == 'PRODUZINDO'), None)
        fila = [dict(n=x.sequence_order, artigo=x.artigo or '', cliente=x.cliente or '', op=x.op or '') for x in itens if x.status == 'EM_SEQUENCIA']
        maquinas.append(dict(numero=n, rodando=dict(artigo=rod.artigo or '', op=rod.op or '', cliente=rod.cliente or '') if rod else None, fila=fila))
        total += len(fila)
    hoje = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=-3))).strftime('%d/%m/%Y')
    return dict(maquinas=maquinas, total_ops=total, data=hoje)


def gerar_pdf() -> tuple[bytes, str]:
    """(pdf, nome do arquivo). Tudo numa unica folha A4: a escala se ajusta ao tamanho da programacao."""
    from playwright.sync_api import sync_playwright
    d = dados()
    html = render_template_string(HTML, **d)
    with sync_playwright() as pw:
        nav = pw.chromium.launch(executable_path=os.getenv('RELATORIO_FAT_CHROMIUM') or None)
        try:
            pg = nav.new_page(viewport={'width': 794, 'height': 1123})
            pg.set_content(html, wait_until='load')
            h = pg.evaluate('document.documentElement.scrollHeight')
            util = 1123 - 2 * 30                                    # A4 a 96 dpi menos as margens de 8 mm
            escala = min(1.0, max(0.3, util / max(h, 1)))
            pdf = pg.pdf(format='A4', print_background=True, margin=dict(top='8mm', bottom='8mm', left='8mm', right='8mm'), scale=escala, page_ranges='1')
        finally:
            nav.close()
    return pdf, f'Programação por Máquina - {d["data"].replace("/", "_")}.pdf'
