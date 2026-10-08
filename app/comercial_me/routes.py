"""Aba COMERCIAL ME (exportacao): mostra as planilhas importadas por
importar_comercial_me.py (arquivos em app/seed_data/comercial_me)."""
import json
import os
import re
from functools import lru_cache

from flask import Blueprint, abort, jsonify, session
from flask_login import current_user, login_required

from app.models import Tab

comercial_me_bp = Blueprint('comercial_me', __name__, url_prefix='/comercial-me')

DADOS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'seed_data', 'comercial_me')
SLUG = 'pedidos-comercial-me'
ORDEM_GRUPOS = ['Pedidos', 'Crédito e pagamentos', 'Amostras e cotações', 'Documentação e envios',
                'Volume de vendas', 'Outras planilhas']


def ambiente_atual():
    return 'GT' if session.get('login_pais') == 'GT' else 'BR'


@lru_cache(maxsize=1)
def _catalogo_completo():
    caminho = os.path.join(DADOS_DIR, 'catalogo.json')
    if not os.path.exists(caminho):
        return {'gerado_em': None, 'tabelas': []}
    with open(caminho, encoding='utf-8') as f:
        return json.load(f)


def catalogo(regiao):
    """Tabelas visiveis no ambiente (BR ou GT), na ordem dos grupos."""
    tabelas = [t for t in _catalogo_completo()['tabelas'] if t['regiao'] in (regiao, 'ALL')]
    tabelas.sort(key=lambda t: ORDEM_GRUPOS.index(t['grupo']) if t['grupo'] in ORDEM_GRUPOS else 99)
    return tabelas


@lru_cache(maxsize=64)
def _carregar(id_):
    with open(os.path.join(DADOS_DIR, id_ + '.json'), encoding='utf-8') as f:
        return json.load(f)


def _coluna(d, prefixo):
    for i, nome in enumerate(d['colunas']):
        if nome.upper().startswith(prefixo.upper()):
            return i
    return None


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else 0


def _ano(v):
    m = re.match(r'\d{2}/\d{2}/(\d{4})', str(v or ''))
    return int(m.group(1)) if m else None


def _ranking(linhas, i_chave, i_valor, limite=10):
    soma = {}
    for r in linhas:
        k = (r[i_chave] or '').strip() if isinstance(r[i_chave], str) else r[i_chave]
        if not k:
            continue
        soma[k] = soma.get(k, 0) + _num(r[i_valor])
    return sorted(([k, round(v)] for k, v in soma.items() if v > 0), key=lambda x: -x[1])[:limite]


def visao_geral(regiao, lang='pt'):
    """Indicadores calculados direto das tabelas importadas (nada digitado a mao)."""
    ids = {t['id'] for t in catalogo(regiao)}
    kpis, rankings = [], []
    ano = 2026
    T = (lambda pt, es: es if lang == 'es' else pt)

    if regiao == 'BR' and {'br_followup', 'br_detalhes'} <= ids:
        fu, de = _carregar('br_followup'), _carregar('br_detalhes')
        i_cli, i_mts = _coluna(fu, 'CLIENTE'), _coluna(fu, 'TOTAL METROS')
        pedidos = [r for r in fu['linhas'] if r[i_cli]]
        c_cli, c_pais, c_data, c_usd = (_coluna(de, p) for p in ('CLIENTE', 'PAIS', 'DATA PRO-FORMA', 'TOTAL US$'))
        itens = [r for r in de['linhas'] if r[c_cli]]
        itens_ano = [r for r in itens if _ano(r[c_data]) == ano]
        kpis += [
            dict(rotulo=T('Pedidos no follow-up', 'Pedidos en seguimiento'), valor=len(pedidos), nota=T('todas as pro-formas da planilha', 'todas las pro-formas de la planilla')),
            dict(rotulo=T('Metros pedidos', 'Metros pedidos'), valor=round(sum(_num(r[i_mts]) for r in pedidos)), sufixo=' m', nota=T('soma do follow-up', 'suma del seguimiento')),
            dict(rotulo=f'Pro-formas {ano} (US$)', valor=round(sum(_num(r[c_usd]) for r in itens_ano)), prefixo='US$ ', nota=f'{len({r[c_cli] for r in itens_ano})} ' + T('clientes', 'clientes')),
            dict(rotulo=T('Pro-formas desde 2025 (US$)', 'Pro-formas desde 2025 (US$)'), valor=round(sum(_num(r[c_usd]) for r in itens)), prefixo='US$ ', nota=f'{len({r[c_cli] for r in itens})} clientes'),
        ]
        rankings += [
            dict(titulo=T(f'Top 10 clientes em {ano} (US$)', f'Top 10 clientes en {ano} (US$)'), itens=_ranking(itens_ano, c_cli, c_usd)),
            dict(titulo=T(f'Valor por país em {ano} (US$)', f'Valor por país en {ano} (US$)'), itens=_ranking(itens_ano, c_pais, c_usd)),
        ]
    if regiao == 'BR' and 'cred_gestao' in ids:
        cr = _carregar('cred_gestao')
        i_saldo, i_total, i_st = (_coluna(cr, p) for p in ('TOTAL DUE BALANCE', 'TOTAL DUE PAYMENT', 'STATUS PGTO'))
        saldo = sum(_num(r[i_saldo]) for r in cr['linhas'])
        por_status = {}
        for r in cr['linhas']:
            s = (r[i_st] or '').strip().lower()
            if s and _num(r[i_saldo]) < 0:
                por_status[s] = por_status.get(s, 0) + (-_num(r[i_saldo]))
        kpis.append(dict(rotulo=T('Saldo líquido a receber (US$)', 'Saldo neto por cobrar (US$)'), valor=round(-saldo), prefixo='US$ ', nota=T('mesmo total da aba SUMMARY', 'mismo total de la hoja SUMMARY')))
        rankings.append(dict(titulo=T('Saldo a receber por situação (US$)', 'Saldo por cobrar por situación (US$)'),
                             itens=sorted(([k.capitalize(), round(v)] for k, v in por_status.items()), key=lambda x: -x[1])))
    if regiao == 'GT' and 'gt_followup' in ids:
        fu = _carregar('gt_followup')
        c_cli, c_pais, c_data, c_mts, c_usd = (_coluna(fu, p) for p in ('CLIENTE', 'PAIS', 'DATA PRO-FORMA', 'TOTAL MTS', 'TOTAL US$'))
        pedidos = [r for r in fu['linhas'] if r[c_cli]]
        ped_ano = [r for r in pedidos if _ano(r[c_data]) == ano]
        kpis += [
            dict(rotulo=T('Pedidos no follow-up', 'Pedidos en seguimiento'), valor=len(pedidos), nota=T('todas as pro-formas da planilha', 'todas las pro-formas de la planilla')),
            dict(rotulo=T('Metros / jardas pedidos', 'Metros / yardas pedidos'), valor=round(sum(_num(r[c_mts]) for r in pedidos)), nota=T('soma do follow-up', 'suma del seguimiento')),
            dict(rotulo=f'Pro-formas {ano} (US$)', valor=round(sum(_num(r[c_usd]) for r in ped_ano)), prefixo='US$ ', nota=f'{len({r[c_cli] for r in ped_ano})} clientes'),
            dict(rotulo=T('Pro-formas no total (US$)', 'Pro-formas en total (US$)'), valor=round(sum(_num(r[c_usd]) for r in pedidos)), prefixo='US$ ', nota=f'{len({r[c_cli] for r in pedidos})} clientes'),
        ]
        rankings += [
            dict(titulo=f'Top 10 clientes em {ano} (US$)', itens=_ranking(ped_ano, c_cli, c_usd)),
            dict(titulo=f'Valor por país em {ano} (US$)', itens=_ranking(ped_ano, c_pais, c_usd)),
        ]
    return dict(kpis=kpis, rankings=rankings)


def _autorizar():
    tab = Tab.query.filter_by(slug=SLUG).first_or_404()
    if not tab.visible_to(current_user):
        abort(403)


@comercial_me_bp.route('/tabela/<id_>')
@login_required
def tabela(id_):
    _autorizar()
    permitido = {t['id'] for t in catalogo(ambiente_atual())}
    if id_ not in permitido:  # tambem barra qualquer id fora do catalogo (sem caminho de arquivo livre)
        abort(404)
    d = _carregar(id_)
    resp = jsonify({k: d[k] for k in ('id', 'titulo', 'grupo', 'tipo', 'arquivo', 'aba', 'atualizado_em', 'colunas', 'linhas')})
    resp.headers['Cache-Control'] = 'private, max-age=60'
    return resp
