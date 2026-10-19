"""Cronograma de Amostras: pedidos de amostra do TOTVS (operacoes com AMOSTRA no nome), lidos pela API (somente leitura).

Prazo = data prevista de faturamento do pedido (billingForecastDate). Atendido = status Attended / PartiallyAnswered com faturamento.
"""
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

from . import api
import os

from .entrada import EMPRESA, ORDERS_URL, _janelas, _num

DIAS = 150
OPERACOES = [int(x) for x in os.getenv('AMOSTRA_OPERACOES', '513,519,542').split(',') if x.strip()]      # 513 amostra gratis, 519 amostra exportacao gratis, 542 amostra exportacao dolar
_CACHE = {}
STATUS_ROT = {'InProgress': 'Em andamento', 'PartiallyAnswered': 'Atendido parcial', 'Attended': 'Atendido', 'Canceled': 'Cancelado', 'Blocked': 'Bloqueado', 'Suspended': 'Suspenso'}


def _d(v):
    return str(v)[:10] if v else None


def dados(forcar=False) -> dict:
    c = _CACHE.get('d')
    if c and not forcar and time.time() - c[0] < 300:
        return c[1]
    cli = api.TotvsModaClient()
    cli.autenticar()
    hoje = date.today()
    ini = hoje - timedelta(days=DIAS)
    todos = {}
    for a, b in _janelas(ini, hoje):
        pag = 1
        while pag <= 20:
            r = cli.post(ORDERS_URL, dict(filter=dict(branchCodeList=[EMPRESA], operationCodeList=OPERACOES, startOrderDate=f'{a}T00:00:00', endOrderDate=f'{b}T23:59:59'), pageSize=500, page=pag))
            if r.status_code != 200:
                raise RuntimeError(f'amostras {a}..{b}: HTTP {r.status_code} {r.text[:200]}')
            j = r.json()
            for p in j.get('items') or []:
                todos[p.get('orderCode')] = p
            if not j.get('hasNext'):
                break
            pag += 1
    brutos = list(todos.values())
    try:
        from .entrada import _fantasias, _ler
        fant = _fantasias(cli, {p.get('customerCode') for p in brutos}, dict(_ler().get('clientes', {})))
    except Exception:  # noqa: BLE001
        fant = {}
    itens = []
    for p in brutos:
        st = p.get('statusOrder')
        prev = _d(p.get('billingForecastDate'))
        fat = _d(p.get('lastBillingDate'))
        ins = _d(p.get('insertDate'))
        aberto = st in ('InProgress', 'PartiallyAnswered', 'Blocked', 'Suspended')
        atrasado = bool(aberto and prev and prev < hoje.isoformat())
        dias_ciclo = None
        if fat and ins:
            dias_ciclo = (date.fromisoformat(fat) - date.fromisoformat(ins)).days
        itens.append(dict(pedido=str(p.get('orderCode')), cliente=(fant.get(str(p.get('customerCode'))) or str(p.get('customerName') or ''))[:60], data=_d(p.get('orderDate')), inserido=ins,
                          previsao=prev, faturado=fat, status=st, status_rot=STATUS_ROT.get(st, st), operacao=str(p.get('operationName') or ''), metros=round(_num(p.get('quantity')), 2),
                          valor=round(_num(p.get('netValue')), 2), po=str(p.get('customerOrderCode') or ''), aberto=aberto, atrasado=atrasado, dias_ciclo=dias_ciclo,
                          dias_para_prazo=(date.fromisoformat(prev) - hoje).days if prev and aberto else None))
    itens.sort(key=lambda x: (not x['aberto'], x['previsao'] or '9999', x['pedido']))
    validos = [x for x in itens if x['status'] != 'Canceled']
    abertos = [x for x in validos if x['aberto']]
    atendidos = [x for x in validos if x['status'] == 'Attended']
    ciclos = [x['dias_ciclo'] for x in atendidos if x['dias_ciclo'] is not None and x['dias_ciclo'] >= 0]
    por_mes, por_sem, por_cli = defaultdict(lambda: [0, 0, 0.0]), defaultdict(lambda: [0, 0.0]), Counter()
    for x in validos:
        m = (x['data'] or '')[:7]
        por_mes[m][0] += 1
        por_mes[m][1] += 1 if x['status'] == 'Attended' else 0
        por_mes[m][2] += x['metros']
        por_cli[x['cliente']] += 1
    for x in abertos:
        if x['previsao']:
            d0 = date.fromisoformat(x['previsao'])
            seg = (d0 - timedelta(days=d0.weekday())).isoformat()
            por_sem[seg][0] += 1
            por_sem[seg][1] += x['metros']
    res = dict(atualizado=datetime.now().isoformat(timespec='seconds'), dias=DIAS, itens=itens,
               kpis=dict(total=len(validos), abertos=len(abertos), atendidos=len(atendidos), cancelados=len([x for x in itens if x['status'] == 'Canceled']),
                         atrasados=len([x for x in abertos if x['atrasado']]), vencem_7d=len([x for x in abertos if x['dias_para_prazo'] is not None and 0 <= x['dias_para_prazo'] <= 7]),
                         sem_previsao=len([x for x in abertos if not x['previsao']]), metros_abertos=round(sum(x['metros'] for x in abertos), 2),
                         ciclo_medio=round(sum(ciclos) / len(ciclos), 1) if ciclos else None,
                         idade_media_abertos=round(sum((hoje - date.fromisoformat(x['data'])).days for x in abertos if x['data']) / max(len([x for x in abertos if x['data']]), 1), 1) if abertos else None, pct_atendido=round(len(atendidos) / len(validos), 3) if validos else 0,
                         no_prazo=round(1 - len([x for x in abertos if x['atrasado']]) / len(abertos), 3) if abertos else 1),
               por_mes=[dict(mes=k, pedidos=v[0], atendidos=v[1], metros=round(v[2], 2)) for k, v in sorted(por_mes.items()) if k],
               por_semana=[dict(semana=k, pedidos=v[0], metros=round(v[1], 2)) for k, v in sorted(por_sem.items())],
               por_cliente=[dict(cliente=k, pedidos=v) for k, v in por_cli.most_common(10)])
    _CACHE['d'] = (time.time(), res)
    return res
