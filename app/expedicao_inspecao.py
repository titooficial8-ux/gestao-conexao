"""Alerta por e-mail do saldo AGUARDANDO INSPECAO da Expedicao: quando passa de EXPEDICAO_INSPECAO_LIMITE metros (padrao 1.000 m), manda um e-mail com o PDF do saldo (por produto + saldo total em aberto).

.env (opcional):
  EXPEDICAO_INSPECAO_EMAIL=1                 (0 desliga o envio automatico)
  EXPEDICAO_INSPECAO_LIMITE=1000             (metros)
  EXPEDICAO_INSPECAO_DESTINOS=a@x.com,b@y.com  (padrao: so o Christian, fase de teste)
  EXPEDICAO_INSPECAO_HORAS=08:00-18:00       (janela do dia em que o alerta pode sair, horario de Brasilia)
Regra: no maximo 1 e-mail por dia util (nao envia em fim de semana nem feriado nacional), no 1o momento da janela em que o saldo esta acima do limite. O saldo e lido do painel da Expedicao
(atualizado do TOTVS pelo proprio agendador da Expedicao). Se o envio falhar, tenta de novo em 30 minutos."""
from __future__ import annotations

import json
import os
import threading
import time
import traceback
from datetime import datetime, timedelta
from pathlib import Path

DESTINO_PADRAO = 'christian.yarzon@conexaomalhas.com.br'
_trava = threading.Lock()


def config() -> dict:
    ini, _, fim = (os.getenv('EXPEDICAO_INSPECAO_HORAS', '08:00-18:00') + '-').partition('-')
    fim = fim.rstrip('-') or '18:00'

    def hm(t, p):
        try:
            h, m = [int(x) for x in t.strip().split(':')]
            return h, m
        except ValueError:
            return p
    try:
        limite = float(os.getenv('EXPEDICAO_INSPECAO_LIMITE', '1000'))
    except ValueError:
        limite = 1000.0
    return dict(ativo=os.getenv('EXPEDICAO_INSPECAO_EMAIL', '1') != '0', limite=limite, destinos=os.getenv('EXPEDICAO_INSPECAO_DESTINOS', DESTINO_PADRAO), inicio=hm(ini, (8, 0)), fim=hm(fim, (18, 0)))


def _fmt(v, casas=0):
    return f'{float(v or 0):,.{casas}f}'.replace(',', 'X').replace('.', ',').replace('X', '.')


def dados() -> dict:
    """Saldo aguardando inspecao como o painel da Expedicao mostra (precisa de app_context)."""
    from app.relatorio_fat.agenda import agora_brasilia
    from app.totvs import expedicao
    p = expedicao.painel(30)
    E = p.get('estoque') or {}
    I = E.get('inspecao') or {}
    lista = I.get('lista') or []
    return dict(total=float(I.get('total') or 0), itens=len(lista), unidade=I.get('unidade') or 'MT', nome=I.get('nome') or 'INSPEÇÃO', em=I.get('em'), lista=lista,
                na_exp_m=round(sum(x.get('exp_m') or 0 for x in lista), 2), na_exp_pecas=sum(x.get('exp_pecas') or 0 for x in lista), estoque_expedicao=float(E.get('total') or 0), gerado=agora_brasilia())


# ------------------------------------------------------------------ PDF
HTML = """<!doctype html><html lang="pt-br"><head><meta charset="utf-8"><style>
@page{size:8.27in 11.69in;margin:0}*{box-sizing:border-box}body{margin:0;font-family:'Segoe UI',Arial,sans-serif;color:#454C52}
.pg{width:8.27in;height:11.69in;position:relative;overflow:hidden;page-break-after:always;background:#fff url('{{ fundo }}') center/cover}
.logo{position:absolute;right:.4in;top:.2in;width:1.6in}.sec{position:absolute;left:.4in;top:.25in;font-size:11pt;font-weight:700;color:#008EC9}
.tit{position:absolute;left:.4in;top:.55in;font-size:18pt;font-weight:700;color:#1F4E79;max-width:5.6in}
.rod{position:absolute;left:0;right:0;bottom:0;height:.24in;background:#008EC9;color:#fff;font-size:8pt;display:flex;justify-content:space-between;padding:.04in .3in}
.nota{position:absolute;left:.35in;bottom:.3in;font-size:9pt;color:#6B7280;font-style:italic}
.cont{position:absolute;left:.4in;right:.4in;top:1.3in;bottom:.5in}
.cards{display:flex;flex-wrap:wrap;gap:.12in;margin-bottom:.22in}.card{flex:1 1 22%;background:#fff;border:1px solid #d9e2ec;border-left:6px solid #1F4E79;border-radius:8px;padding:.1in .13in}
.card.v{border-left-color:#C62828}.card.l{border-left-color:#B7791F}.card small{display:block;font-size:8.5pt;color:#6B7280;text-transform:uppercase;letter-spacing:.04em}.card b{font-size:18pt;color:#1F4E79}.card.v b{color:#C62828}
.alerta{background:#FFF4E5;border:1px solid #F5C77E;border-radius:8px;padding:.09in .14in;margin-bottom:.2in;font-size:10pt;color:#7A4B00}
table{width:100%;border-collapse:collapse;font-size:9pt;background:rgba(255,255,255,.93)}th{background:#1F4E79;color:#fff;text-align:left;padding:5px 6px;font-size:8.5pt}td{padding:4px 6px;border-bottom:1px solid #e5e9ee}
tr:nth-child(even) td{background:#F4F6F8}.n{text-align:right}tr.tot td{background:#DCE8F5;font-weight:700;color:#1F4E79;border-top:2px solid #1F4E79}
</style></head><body>
{% set total = paginas|length %}
{% for p in paginas %}<div class="pg">
<img class="logo" src="{{ logo }}"><div class="sec">Expedição · Aguardando inspeção</div>
<div class="tit">Saldo aguardando inspeção{{ ' (cont.)' if p.parte>1 }}</div>
<div class="cont">
{% if p.parte==1 %}
 <div class="cards"><div class="card v"><small>Saldo em aberto</small><b>{{ f(d.total) }} m</b></div><div class="card"><small>Produtos em aberto</small><b>{{ d.itens }}</b></div><div class="card l"><small>Desses, na expedição</small><b>{{ f(d.na_exp_m) }} m</b></div><div class="card"><small>Peças na expedição</small><b>{{ f(d.na_exp_pecas) }}</b></div></div>
 <div class="alerta"><b>Saldo acima do limite de {{ f(limite) }} m.</b> São {{ f(d.total) }} m em {{ d.itens }} produto(s) aguardando inspeção, para inventariar ou limpar no sistema.{% if d.estoque_expedicao %} Estoque total da Expedição: {{ f(d.estoque_expedicao) }} m.{% endif %}</div>
{% endif %}
 <table><tr><th>Produto</th><th>Descrição</th><th class="n">Em inspeção (m)</th><th class="n">Na expedição (m)</th><th class="n">Peças na exp.</th></tr>
 {% for x in p.linhas %}<tr><td>{{ x.cod }}</td><td>{{ x.nome }}</td><td class="n">{{ f(x.saldo) }}</td><td class="n">{{ f(x.exp_m) if x.exp_m else '—' }}</td><td class="n">{{ f(x.exp_pecas) if x.exp_pecas else '—' }}</td></tr>{% endfor %}
 {% if p.ultima %}<tr class="tot"><td colspan="2">SALDO TOTAL EM ABERTO · {{ d.itens }} produto(s)</td><td class="n">{{ f(d.total) }} m</td><td class="n">{{ f(d.na_exp_m) }} m</td><td class="n">{{ f(d.na_exp_pecas) }}</td></tr>{% endif %}
 </table>
</div>
{% if p.ultima %}<div class="nota">Dados do TOTVS{% if d.em %} lidos em {{ em_txt }}{% endif %} · saldo {{ d.nome }}</div>{% endif %}
<div class="rod"><span>www.gestaoconexao.com.br</span><span>Expedição · Gerado em {{ gerado }} · {{ loop.index }}/{{ total }}</span></div></div>{% endfor %}
</body></html>"""


def _paginas(lista: list) -> list:
    pag, i, primeira = [], 0, 12
    resto = list(lista)
    corte = primeira
    while True:
        bloco, resto = resto[:corte], resto[corte:]
        pag.append(dict(parte=len(pag) + 1, linhas=bloco, ultima=not resto))
        if not resto:
            return pag
        corte = 26


def html(d: dict) -> str:
    from flask import current_app
    from app.reprova_cru import _img
    em_txt = ''
    try:
        em_txt = datetime.fromisoformat(d['em']).strftime('%d/%m/%Y %H:%M') if d.get('em') else ''
    except ValueError:
        pass
    return current_app.jinja_env.from_string(HTML).render(d=d, paginas=_paginas(d['lista']), f=_fmt, limite=config()['limite'], em_txt=em_txt, logo=_img('logo_colorida.png', 'image/png'), fundo=_img('conteudo_fundo.jpg', 'image/jpeg'),
                                                           gerado=d['gerado'].strftime('%d/%m/%Y %H:%M'))


def gerar_pdf(d: dict) -> bytes:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        nav = pw.chromium.launch(executable_path=os.getenv('RELATORIO_FAT_CHROMIUM') or None)
        try:
            pg = nav.new_page(viewport={'width': 794, 'height': 1123})
            pg.set_content(html(d), wait_until='load')
            return pg.pdf(prefer_css_page_size=True, print_background=True)
        finally:
            nav.close()


# ------------------------------------------------------------------ e-mail
def corpo(d: dict) -> str:
    from app.relatorio_fat import envio_email as envio
    return ('Prezados,\n\n'
            f'O saldo aguardando inspeção na Expedição está em {_fmt(d["total"])} m, em {d["itens"]} produto(s), acima do limite de {_fmt(config()["limite"])} m. '
            'Segue em anexo o relatório em PDF, com o saldo total em aberto e o detalhe por produto.\n\n'
            f'Gerado em {d["gerado"]:%d/%m/%Y} às {d["gerado"]:%H:%M}.\n\n'
            'Atenciosamente,\n'
            'Gestão Conexão\n'
            'www.gestaoconexao.com.br\n\n'
            '--\n' + envio.AVISO_LEGAL)


def enviar(destinos: list, d: dict = None, teste: bool = False) -> dict:
    from flask import current_app
    from app.relatorio_fat import agenda, envio_email as envio
    d = d or dados()
    pdf = gerar_pdf(d)
    assunto = ('[TESTE] ' if teste else '') + f'Expedição — saldo aguardando inspeção: {_fmt(d["total"])} m ({d["itens"]} produto{"s" if d["itens"] != 1 else ""})'
    rec = envio.enviar(destinos, assunto, corpo(d), [(f'saldo_aguardando_inspecao_{d["gerado"]:%Y-%m-%d_%H%M}.pdf', pdf, 'application/pdf')])
    agenda.registrar_envio(current_app._get_current_object(), assunto, destinos, rec, 'inspecao-teste' if teste else 'inspecao')
    return rec


# ------------------------------------------------------------------ agendador
def _arquivo(app) -> Path:
    return Path(app.instance_path) / 'expedicao_inspecao.json'


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
    from app.relatorio_fat.agenda import _reivindicar, agora_brasilia, dia_sem_envio
    cfg = config()
    if not cfg['ativo']:
        return
    agora = agora_brasilia()
    if agora.weekday() >= 5 or dia_sem_envio(agora.date()):
        return
    if not ((agora.hour, agora.minute) >= cfg['inicio'] and (agora.hour, agora.minute) <= cfg['fim']):
        return
    est = _ler(app)
    hoje = agora.date().isoformat()
    if est.get('enviado_em') == hoje:
        return
    if est.get('erro_em') and (agora - datetime.fromisoformat(est['erro_em'])) < timedelta(minutes=30):
        return
    if not _trava.acquire(blocking=False):
        return
    try:
        with app.app_context():
            d = dados()
            est['ultima_leitura'] = f'{agora:%d/%m %H:%M} · {_fmt(d["total"])} m em {d["itens"]} produto(s)'
            if d['total'] <= cfg['limite'] or not d['lista']:
                _gravar(app, est)
                return
            if d.get('em') and (agora - datetime.fromisoformat(d['em'])) > timedelta(hours=6):          # leitura do TOTVS velha: espera a proxima
                est['ultima_leitura'] += ' · leitura do TOTVS antiga, aguardando atualizar'
                _gravar(app, est)
                return
            lock = _reivindicar(agora.date(), 'inspecao')
            if lock is None:
                return
            try:
                if not envio.configurado():
                    raise RuntimeError('E-mail não configurado: preencha no .env ' + ', '.join(envio.faltando()))
                destinos = envio.validar(cfg['destinos'])
                enviar(destinos, d)
                est.update(enviado_em=hoje, ultimo=f'{agora:%d/%m %H:%M} · {_fmt(d["total"])} m em {d["itens"]} produto(s) enviados para {len(destinos)} pessoa(s)', erro=None, erro_em=None)
                print(f'[expedicao-inspecao] {est["ultimo"]}', flush=True)
            except Exception as e:  # noqa: BLE001
                traceback.print_exc()
                est.update(erro=str(e)[:300], erro_em=agora.isoformat(timespec='seconds'))
                try:
                    lock.unlink()
                except OSError:
                    pass
            _gravar(app, est)
    except Exception:  # noqa: BLE001
        traceback.print_exc()
    finally:
        _trava.release()


def iniciar_agendador(app) -> None:
    def loop():
        time.sleep(90)
        while True:
            try:
                _tick(app)
            except Exception:  # noqa: BLE001
                traceback.print_exc()
            time.sleep(300)
    threading.Thread(target=loop, daemon=True, name='expedicao-inspecao-email').start()
