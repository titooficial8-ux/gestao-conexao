"""Reprovas no cru do dia de producao anterior: relatorio em PDF (mesmo visual do Relatorio de Faturamento) e envio automatico por e-mail as 07h.

Fonte: NCs da revisao do cru (lista ncform do SharePoint: origem Auditoria Interna, setor Producao). Dia de producao = 06h20 -> 06h19 do dia seguinte.
.env (tudo opcional):
  REPROVA_EMAIL_ENVIO=0                 (1 liga o envio automatico; comeca DESLIGADO ate o teste ser aprovado)
  REPROVA_EMAIL_HORA=07:00              (horario de Brasilia)
  REPROVA_EMAIL_DESTINOS=luciane.frias@conexaomalhas.com.br
  REPROVA_EMAIL_SEMPRE=0                (0 = so envia se houve reprova no dia; 1 = envia mesmo sem reprova)
  REPROVA_EMAIL_JANELA_H=4              (se o Gestao so ligar depois disso, pula o envio do dia)
O SMTP e o mesmo do Relatorio de Faturamento (SMTP_HOST, SMTP_USER, SMTP_PASSWORD).
"""
import base64
import json
import os
import threading
import time
import traceback
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

from flask import Blueprint, Response, abort, jsonify, render_template_string, request
from flask_login import current_user, login_required

ASSETS = Path(__file__).parent / 'relatorio_fat' / 'assets'
rep_bp = Blueprint('reprova_cru', __name__, url_prefix='/reprovas-cru')
LINHAS_POR_PAGINA = 13
_trava = threading.Lock()


def config() -> dict:
    return dict(ativo=os.getenv('REPROVA_EMAIL_ENVIO', '0') == '1', hora=os.getenv('REPROVA_EMAIL_HORA', '07:00'),
                destinos=os.getenv('REPROVA_EMAIL_DESTINOS', 'luciane.frias@conexaomalhas.com.br'), sempre=os.getenv('REPROVA_EMAIL_SEMPRE', '0') == '1',
                janela=float(os.getenv('REPROVA_EMAIL_JANELA_H', '4') or 4))


def dia_anterior(agora: datetime = None) -> date:
    """Dia de producao anterior: depois das 06h20 e o dia de ontem; antes disso, o de anteontem (o de ontem ainda nao fechou)."""
    agora = agora or datetime.now()
    return (agora - timedelta(hours=6, minutes=20)).date() - timedelta(days=1)


def dados(dia: date) -> dict:
    from app.malharia_info import _ncs, _origem
    ini = datetime(dia.year, dia.month, dia.day, 6, 20)
    fim = ini + timedelta(days=1)
    itens = [n for n in _ncs() if n['criado'] and ini <= n['criado'] < fim and n['defeitos'] and (not n['setor'] or 'produ' in n['setor'].lower()) and 'auditoria interna' in str(n['origem']).lower()]
    itens.sort(key=lambda n: n['criado'])
    art, dfs, maqs = defaultdict(lambda: dict(pecas=0, metros=0.0, kg=0.0, defeitos=defaultdict(int), maquinas=set(), cliente='')), defaultdict(lambda: [0, 0.0]), defaultdict(lambda: dict(pecas=0, metros=0.0, kg=0.0))
    for n in itens:
        a = art[n['artigo']]
        a['pecas'] += 1
        a['metros'] += n['metros']
        a['kg'] += n['quilos']
        a['cliente'] = a['cliente'] or n['cliente']
        a['maquinas'].add(n['maquina'])
        for d in n['defeitos']:
            a['defeitos'][d] += 1
            dfs[d][0] += 1
            dfs[d][1] += n['metros'] / len(n['defeitos'])
        m = maqs[n['maquina']]
        m['pecas'] += 1
        m['metros'] += n['metros']
        m['kg'] += n['quilos']
    por_artigo = sorted(({'artigo': k, 'cliente': v['cliente'], 'pecas': v['pecas'], 'metros': round(v['metros'], 1), 'kg': round(v['kg'], 1),
                          'defeitos': ', '.join(f'{d} ({c})' for d, c in sorted(v['defeitos'].items(), key=lambda x: -x[1])[:3]), 'maquinas': ', '.join(sorted(v['maquinas']))} for k, v in art.items()),
                        key=lambda x: (-x['pecas'], -x['metros']))
    return dict(dia=dia, janela=f'{ini:%d/%m/%Y %H:%M} a {fim - timedelta(minutes=1):%d/%m/%Y %H:%M}', itens=[dict(n, origem=_origem(n['origem'])) for n in itens],
                pecas=len(itens), metros=round(sum(n['metros'] for n in itens), 1), kg=round(sum(n['quilos'] for n in itens), 1), n_artigos=len(art), n_maquinas=len(maqs),
                por_artigo=por_artigo, por_defeito=sorted(({'defeito': k, 'pecas': v[0], 'metros': round(v[1], 1)} for k, v in dfs.items()), key=lambda x: -x['pecas']),
                por_maquina=sorted(({'maquina': k, 'pecas': v['pecas'], 'metros': round(v['metros'], 1), 'kg': round(v['kg'], 1)} for k, v in maqs.items()), key=lambda x: (-x['pecas'], x['maquina'])))


def _fmt(v, casas=1):
    return f'{v:,.{casas}f}'.replace(',', 'X').replace('.', ',').replace('X', '.')


def _img(nome, mime):
    return f'data:{mime};base64,' + base64.b64encode((ASSETS / nome).read_bytes()).decode()


def _paginas(d):
    pag = []
    pag.append(dict(tipo='resumo'))
    for i in range(0, max(len(d['por_artigo']), 1), LINHAS_POR_PAGINA):
        pag.append(dict(tipo='artigos', linhas=d['por_artigo'][i:i + LINHAS_POR_PAGINA], parte=i // LINHAS_POR_PAGINA + 1))
    pag.append(dict(tipo='defmaq'))
    for i in range(0, len(d['itens']), LINHAS_POR_PAGINA):
        pag.append(dict(tipo='detalhe', linhas=d['itens'][i:i + LINHAS_POR_PAGINA], parte=i // LINHAS_POR_PAGINA + 1))
    return pag


HTML = """<!doctype html><html lang="pt-br"><head><meta charset="utf-8"><style>
@page{size:13.333in 7.5in;margin:0}*{box-sizing:border-box}body{margin:0;font-family:'Segoe UI',Arial,sans-serif;color:#454C52}
.pg{width:13.333in;height:7.5in;position:relative;overflow:hidden;page-break-after:always;background:#fff url('{{ fundo }}') center/cover}
.logo{position:absolute;right:.55in;top:.18in;width:1.88in}.sec{position:absolute;left:.5in;top:.22in;font-size:13pt;font-weight:700;color:#008EC9}
.tit{position:absolute;left:.5in;top:.5in;font-size:22pt;font-weight:700;color:#1F4E79;max-width:9.6in}
.rod{position:absolute;left:0;right:0;bottom:0;height:.21in;background:#008EC9;color:#fff;font-size:9pt;display:flex;justify-content:space-between;padding:.03in .4in}
.nota{position:absolute;left:.35in;bottom:.3in;font-size:9pt;color:#6B7280;font-style:italic}
.cont{position:absolute;left:.5in;right:.5in;top:1.2in;bottom:.55in}
.cards{display:flex;gap:.2in;margin-bottom:.25in}.card{flex:1;background:#fff;border:1px solid #d9e2ec;border-left:6px solid #1F4E79;border-radius:8px;padding:.15in .2in}
.card.v{border-left-color:#C62828}.card.l{border-left-color:#B7791F}.card small{display:block;font-size:10pt;color:#6B7280;text-transform:uppercase;letter-spacing:.04em}.card b{font-size:26pt;color:#1F4E79}.card.v b{color:#C62828}
table{width:100%;border-collapse:collapse;font-size:10.5pt;background:rgba(255,255,255,.92)}th{background:#1F4E79;color:#fff;text-align:left;padding:6px 8px;font-size:9.5pt}td{padding:5px 8px;border-bottom:1px solid #e5e9ee}
tr:nth-child(even) td{background:#F4F6F8}.n{text-align:right}.dois{display:flex;gap:.3in}.dois>div{flex:1}h3{margin:0 0 6px;color:#2E75B6;font-size:13pt}
.vazio{margin-top:1.2in;text-align:center;font-size:20pt;color:#1B7F3B;font-weight:700}
</style></head><body>
{% set total = paginas|length %}
{% for p in paginas %}<div class="pg">
<img class="logo" src="{{ logo }}"><div class="sec">Qualidade · Revisão do cru</div>
<div class="tit">{% if p.tipo=='resumo' %}Reprovas no cru · {{ d.dia.strftime('%d/%m/%Y') }}{% elif p.tipo=='artigos' %}Reprovas por artigo{{ ' (cont.)' if p.parte>1 }}{% elif p.tipo=='defmaq' %}Por defeito e por máquina{% else %}Peças reprovadas, uma a uma{{ ' (cont.)' if p.parte>1 }}{% endif %}</div>
<div class="cont">
{% if p.tipo=='resumo' %}
 <div class="cards"><div class="card v"><small>Peças reprovadas</small><b>{{ d.pecas }}</b></div><div class="card v"><small>Metros reprovados</small><b>{{ f(d.metros) }}</b></div><div class="card l"><small>Quilos</small><b>{{ f(d.kg) }}</b></div><div class="card"><small>Artigos afetados</small><b>{{ d.n_artigos }}</b></div><div class="card"><small>Máquinas</small><b>{{ d.n_maquinas }}</b></div></div>
 {% if d.pecas %}<h3>Top artigos reprovados</h3><table><tr><th>Artigo</th><th>Cliente</th><th class="n">Peças</th><th class="n">Metros</th><th>Principais defeitos</th></tr>{% for a in d.por_artigo[:6] %}<tr><td><b>{{ a.artigo }}</b></td><td>{{ a.cliente }}</td><td class="n">{{ a.pecas }}</td><td class="n">{{ f(a.metros) }}</td><td>{{ a.defeitos }}</td></tr>{% endfor %}</table>
 {% else %}<div class="vazio">Nenhuma peça reprovada no cru neste dia de produção.</div>{% endif %}
 <div class="nota" style="position:static;margin-top:.15in">Dia de produção: {{ d.janela }} · Origem: Auditoria Interna (Reprova Cru)</div>
{% elif p.tipo=='artigos' %}
 <table><tr><th>Artigo</th><th>Cliente</th><th>Máquinas</th><th class="n">Peças</th><th class="n">Metros</th><th class="n">Kg</th><th>Principais defeitos</th></tr>{% for a in p.linhas %}<tr><td><b>{{ a.artigo }}</b></td><td>{{ a.cliente }}</td><td>{{ a.maquinas }}</td><td class="n">{{ a.pecas }}</td><td class="n">{{ f(a.metros) }}</td><td class="n">{{ f(a.kg) }}</td><td>{{ a.defeitos }}</td></tr>{% endfor %}</table>
{% elif p.tipo=='defmaq' %}
 <div class="dois"><div><h3>Por defeito</h3><table><tr><th>Defeito</th><th class="n">Peças</th><th class="n">Metros</th></tr>{% for x in d.por_defeito[:14] %}<tr><td>{{ x.defeito }}</td><td class="n">{{ x.pecas }}</td><td class="n">{{ f(x.metros) }}</td></tr>{% endfor %}</table></div>
 <div><h3>Por máquina</h3><table><tr><th>Máquina</th><th class="n">Peças</th><th class="n">Metros</th><th class="n">Kg</th></tr>{% for x in d.por_maquina[:14] %}<tr><td>MQ {{ x.maquina }}</td><td class="n">{{ x.pecas }}</td><td class="n">{{ f(x.metros) }}</td><td class="n">{{ f(x.kg) }}</td></tr>{% endfor %}</table></div></div>
{% else %}
 <table><tr><th>Hora</th><th>MQ</th><th>OP</th><th>Peça</th><th>Artigo</th><th>Cliente</th><th>Defeito</th><th class="n">Metros</th><th class="n">Kg</th><th>Turno</th><th>Apontado por</th></tr>{% for n in p.linhas %}<tr><td>{{ n.criado.strftime('%d/%m %H:%M') }}</td><td>{{ n.maquina }}</td><td>{{ n.op or '–' }}</td><td>{{ n.peca }}</td><td><b>{{ n.artigo }}</b></td><td>{{ n.cliente }}</td><td>{{ n.defeitos|join(', ') }}</td><td class="n">{{ f(n.metros) }}</td><td class="n">{{ f(n.quilos) }}</td><td>{{ n.turno }}</td><td>{{ n.emitente }}</td></tr>{% endfor %}</table>
{% endif %}
</div>
<div class="rod"><span>www.gestaoconexao.com.br</span><span>Relatório de Reprovas no Cru · Gerado em {{ gerado }} · {{ loop.index }}/{{ total }}</span></div></div>{% endfor %}
</body></html>"""


def html(d: dict) -> str:
    return render_template_string(HTML, d=d, paginas=_paginas(d), f=_fmt, logo=_img('logo_colorida.png', 'image/png'), fundo=_img('conteudo_fundo.jpg', 'image/jpeg'),
                                  gerado=datetime.now().strftime('%d/%m/%Y %H:%M'))


def gerar_pdf(d: dict) -> bytes:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        nav = pw.chromium.launch(executable_path=os.getenv('RELATORIO_FAT_CHROMIUM') or None)
        try:
            pg = nav.new_page(viewport={'width': 1280, 'height': 720})
            pg.set_content(html(d), wait_until='load')
            return pg.pdf(prefer_css_page_size=True, print_background=True)
        finally:
            nav.close()


def enviar(d: dict, destinos: list, teste: bool = False) -> dict:
    from app.relatorio_fat import envio_email as envio
    pdf = gerar_pdf(d)
    dia = d['dia'].strftime('%d/%m/%Y')
    assunto = ('[TESTE] ' if teste else '') + f'Reprovas no cru — {dia} ({d["pecas"]} peça{"s" if d["pecas"] != 1 else ""})'
    corpo = (f'Prezados,\n\nSegue em anexo o relatório de reprovas no cru do dia de produção {dia} ({d["janela"]}).\n\n'
             f'Peças reprovadas: {d["pecas"]}\nMetros: {_fmt(d["metros"])}\nArtigos afetados: {d["n_artigos"]}\n\nAtenciosamente,\nGestão Conexão\nwww.gestaoconexao.com.br')
    return envio.enviar(destinos, assunto, corpo, [(f'reprovas_cru_{d["dia"].isoformat()}.pdf', pdf, 'application/pdf')])


# ------------------------------------------------------------------ envio automatico
def _arquivo(app) -> Path:
    return Path(app.instance_path) / 'reprova_email.json'


def _ler(app) -> dict:
    try:
        return json.loads(_arquivo(app).read_text(encoding='utf-8'))
    except Exception:  # noqa: BLE001
        return {}


def _gravar(app, est: dict) -> None:
    try:
        _arquivo(app).parent.mkdir(parents=True, exist_ok=True)
        _arquivo(app).write_text(json.dumps(est), encoding='utf-8')
    except OSError:
        pass


def _tick(app) -> None:
    from app.relatorio_fat import envio_email as envio
    from app.relatorio_fat.agenda import _hm, _reivindicar, agora_brasilia
    cfg = config()
    if not cfg['ativo']:
        return
    agora = agora_brasilia()
    h, m = _hm(cfg['hora'], (7, 0))
    alvo = agora.replace(hour=h, minute=m, second=0, microsecond=0)
    est = _ler(app)
    if agora < alvo or agora > alvo + timedelta(hours=cfg['janela']) or est.get('enviado') == agora.date().isoformat():
        return
    if est.get('erro_em') and (agora - datetime.fromisoformat(est['erro_em'])) < timedelta(minutes=30):
        return
    if not _trava.acquire(blocking=False):
        return
    lock = None
    try:
        lock = _reivindicar(agora.date(), 'reprova' + cfg['hora'])
        if lock is None:
            return
        dia = dia_anterior(agora)
        with app.app_context():
            d = dados(dia)
            if not d['pecas'] and not cfg['sempre']:
                est.update(enviado=agora.date().isoformat(), ultimo=f'{dia}: sem reprovas, nada enviado', erro=None)
                _gravar(app, est)
                return
            if not envio.configurado():
                raise RuntimeError('E-mail não configurado: preencha no .env ' + ', '.join(envio.faltando()))
            destinos = envio.validar(cfg['destinos'])
            enviar(d, destinos)
        est.update(enviado=agora.date().isoformat(), ultimo=f'{dia}: {d["pecas"]} peças enviadas para {", ".join(destinos)}', erro=None, erro_em=None)
        print(f'[reprovas-cru] enviado para {", ".join(destinos)} ({d["pecas"]} peças)', flush=True)
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        est.update(erro=str(e)[:300], erro_em=agora.isoformat(timespec='seconds'))
        if lock:
            try:
                lock.unlink()
            except OSError:
                pass
    finally:
        _gravar(app, est)
        _trava.release()


def iniciar_agendador(app) -> None:
    def loop():
        time.sleep(50)
        while True:
            try:
                _tick(app)
            except Exception:  # noqa: BLE001
                traceback.print_exc()
            time.sleep(60)
    threading.Thread(target=loop, daemon=True, name='reprova-cru-email').start()


# ------------------------------------------------------------------ tela e rotas
def _pode() -> bool:
    from app.models import Tab
    tab = Tab.query.filter_by(slug='programacao').first()
    return bool(tab and tab.visible_to(current_user))


def _dia_param():
    try:
        return date.fromisoformat(request.values.get('dia') or '')
    except ValueError:
        return dia_anterior()


PAGINA = """<!doctype html><meta charset="utf-8"><title>Reprovas no cru</title><style>body{font:14px Segoe UI,Arial;margin:24px;color:#1e293b;max-width:1100px}
h1{color:#1F4E79;margin:0 0 4px}.bar{display:flex;gap:10px;align-items:center;margin:14px 0;flex-wrap:wrap}button,.b{border:0;border-radius:8px;padding:9px 16px;font-weight:700;cursor:pointer;background:#1F4E79;color:#fff;text-decoration:none;font-size:14px}
.b2{background:#e5e7eb;color:#1e293b}input{padding:8px;border:1px solid #94a3b8;border-radius:8px}.cards{display:flex;gap:12px;margin:12px 0}.c{background:#f1f5f9;border-radius:10px;padding:10px 16px;min-width:130px}.c small{display:block;color:#64748b;font-size:11px;text-transform:uppercase}.c b{font-size:24px;color:#C62828}
table{border-collapse:collapse;width:100%;margin-top:8px}th{background:#1F4E79;color:#fff;text-align:left;padding:6px 8px;font-size:12px}td{padding:5px 8px;border-bottom:1px solid #e5e9ee}.n{text-align:right}.cfg{background:#fffbeb;border:1px solid #fcd34d;border-radius:8px;padding:10px 14px;margin-top:14px;font-size:13px}#msg{font-weight:700}</style>
<h1>Reprovas no cru</h1><div style="color:#64748b">Dia de produção {{ d.janela }} · origem Auditoria Interna (Reprova Cru)</div>
<form class="bar" method="get"><input type="date" name="dia" value="{{ d.dia.isoformat() }}"><button class="b2" style="background:#e5e7eb;color:#1e293b">Ver dia</button>
<a class="b" target="_blank" href="/reprovas-cru/pdf?dia={{ d.dia.isoformat() }}">Gerar PDF de REPROVAS</a>
<button type="button" id="teste">Enviar e-mail de teste para mim ({{ email }})</button><span id="msg"></span></form>
<div class="cards"><div class="c"><small>Peças</small><b>{{ d.pecas }}</b></div><div class="c"><small>Metros</small><b>{{ f(d.metros) }}</b></div><div class="c"><small>Kg</small><b>{{ f(d.kg) }}</b></div><div class="c"><small>Artigos</small><b>{{ d.n_artigos }}</b></div></div>
<table><tr><th>Artigo</th><th>Cliente</th><th>Máquinas</th><th class="n">Peças</th><th class="n">Metros</th><th>Principais defeitos</th></tr>{% for a in d.por_artigo %}<tr><td><b>{{ a.artigo }}</b></td><td>{{ a.cliente }}</td><td>{{ a.maquinas }}</td><td class="n">{{ a.pecas }}</td><td class="n">{{ f(a.metros) }}</td><td>{{ a.defeitos }}</td></tr>{% else %}<tr><td colspan="6">Nenhuma reprova neste dia de produção.</td></tr>{% endfor %}</table>
<div class="cfg"><b>Envio automático:</b> {{ 'LIGADO' if cfg.ativo else 'DESLIGADO (REPROVA_EMAIL_ENVIO=1 no .env liga)' }} · todo dia às {{ cfg.hora }} · para {{ cfg.destinos }} · {{ 'envia mesmo sem reprova' if cfg.sempre else 'só envia se houve reprova' }} · e-mail (SMTP): {{ 'configurado' if smtp else 'NÃO configurado' }}{% if ult %}<br>Último: {{ ult.get('ultimo') or '–' }}{% if ult.get('erro') %} · <span style="color:#b91c1c">erro: {{ ult.erro }}</span>{% endif %}{% endif %}</div>
<script>document.getElementById('teste').onclick=function(){var m=document.getElementById('msg');m.textContent='Gerando e enviando…';fetch('/reprovas-cru/enviar-teste?dia={{ d.dia.isoformat() }}',{method:'POST',credentials:'same-origin'}).then(function(r){return r.json()}).then(function(j){m.textContent=j.ok?'Enviado para '+j.para.join(', '):('Erro: '+j.erro)}).catch(function(){m.textContent='Falha na requisição'})}</script>"""


@rep_bp.route('/')
@login_required
def pagina():
    if not _pode():
        abort(403)
    from flask import current_app
    from app.relatorio_fat import envio_email as envio
    return render_template_string(PAGINA, d=dados(_dia_param()), f=_fmt, cfg=config(), email=current_user.email, smtp=envio.configurado(), ult=_ler(current_app))


@rep_bp.route('/pdf')
@login_required
def pdf():
    if not _pode():
        abort(403)
    try:
        b = gerar_pdf(dados(_dia_param()))
    except Exception as e:  # noqa: BLE001
        return Response(f'Não foi possível gerar o PDF: {e}\n(Chromium do relatório instalado? python -m playwright install chromium)', status=500, mimetype='text/plain')
    return Response(b, mimetype='application/pdf', headers={'Content-Disposition': f'inline; filename=reprovas_cru_{_dia_param().isoformat()}.pdf'})


@rep_bp.route('/enviar-teste', methods=['POST'])
@login_required
def enviar_teste():
    if not _pode():
        abort(403)
    from app.relatorio_fat import envio_email as envio
    try:
        destinos = envio.validar(request.values.get('para') or current_user.email)
        enviar(dados(_dia_param()), destinos, teste=True)
        return jsonify(ok=True, para=destinos)
    except Exception as e:  # noqa: BLE001
        return jsonify(ok=False, erro=str(e)[:300]), 500
