"""Precos divergentes: itens dos pedidos de venda do mes cujo preco unitario (R$ ou US$ por metro) e diferente do preco da tabela de precos do pedido.

Fontes (somente leitura):
  sales-order/v2/orders/search (expand=items)   -> preco praticado de cada item (price) e a tabela do pedido (priceTableCode)
  product/v2/price-tables/search                 -> preco do produto naquela tabela
"""
import time
from datetime import date

from . import api
from .entrada import ORDERS_URL, EMPRESA, classificar, _num


def _lotes(lista, n):
    for i in range(0, len(lista), n):
        yield lista[i:i + n]

PRECOS_URL = f'{api.BASE_URL}/api/totvsmoda/product/v2/price-tables/search'
TOLERANCIA = 0.005                                                 # centavos de diferenca nao contam
_CACHE = {}
_TABELAS = {}                                                      # (tabela, produto) -> (instante, [precos])


def _precos_tabela(cli, tabela: int, produtos: list) -> dict:
    """{produto: [precos da tabela (todas as escalas)]} dos produtos pedidos; guarda 15 min."""
    agora = time.time()
    falta = [p for p in produtos if (tabela, p) not in _TABELAS or agora - _TABELAS[(tabela, p)][0] > 900]
    for lote in _lotes(falta, 80):
        pag, achou = 1, set()
        while pag <= 10:
            r = cli.post(PRECOS_URL, dict(filter=dict(hasPriceTableItem=True, branchPriceTableCodeList=[EMPRESA], priceTableCodeList=[tabela], productCodeList=lote),
                                          option=dict(branchCodeList=[EMPRESA], priceTableCode=tabela), pageSize=100, page=pag))
            if r.status_code != 200:
                raise RuntimeError(f'tabela de precos {tabela}: HTTP {r.status_code} {r.text[:200]}')
            j = r.json()
            for it in j.get('items') or []:
                ps = [_num(x.get('price')) for x in (it.get('prices') or []) if x.get('branchCode') in (None, EMPRESA)]
                _TABELAS[(tabela, it['productCode'])] = (agora, [p for p in ps if p])
                achou.add(it['productCode'])
            if not j.get('hasNext'):
                break
            pag += 1
        for p in lote:
            if p not in achou:
                _TABELAS[(tabela, p)] = (agora, [])                # produto fora da tabela: nao da para comparar
    return {p: _TABELAS[(tabela, p)][1] for p in produtos}


def divergentes() -> dict:
    hoje = date.today()
    ini = hoje.replace(day=1)
    c = _CACHE.get(ini)
    if c and time.time() - c[0] < 300:
        return c[1]
    cli = api.TotvsModaClient()
    cli.autenticar()
    pedidos, pag = [], 1
    while pag <= 40:
        r = cli.post(ORDERS_URL, dict(filter=dict(branchCodeList=[EMPRESA], startOrderDate=f'{ini}T00:00:00', endOrderDate=f'{hoje}T23:59:59'), expand='items', pageSize=200, page=pag))
        if r.status_code != 200:
            raise RuntimeError(f'pedidos: HTTP {r.status_code} {r.text[:200]}')
        j = r.json()
        pedidos += j.get('items') or []
        if not j.get('hasNext'):
            break
        pag += 1
    validos = []
    for p in pedidos:
        entra, local = classificar(p)
        if entra and p.get('priceTableCode') and p.get('items'):
            validos.append((p, local))
    por_tabela = {}
    for p, _l in validos:
        por_tabela.setdefault(p['priceTableCode'], set()).update(i['productCode'] for i in p['items'] if i.get('productCode'))
    tab_precos = {t: _precos_tabela(cli, t, sorted(prods)) for t, prods in por_tabela.items()}
    linhas, comparados = [], 0
    for p, local in validos:
        for it in p['items']:
            qtd, preco = _num(it.get('quantity')), _num(it.get('price'))
            tabela = tab_precos[p['priceTableCode']].get(it.get('productCode')) or []
            if not tabela or not qtd or not preco:
                continue
            comparados += 1
            if any(abs(preco - t) <= TOLERANCIA for t in tabela):
                continue
            ref = min(tabela, key=lambda t: abs(preco - t))
            linhas.append(dict(pedido=str(p['orderCode']), data=str(p.get('orderDate') or '')[:10], cliente=str(p.get('customerName') or '')[:60], local=local, tabela=p['priceTableCode'],
                               produto=str(it.get('referenceName') or it.get('name') or '')[:60], cor=str(it.get('colorName') or ''), metros=round(qtd, 2),
                               preco=round(preco, 4), preco_tabela=round(ref, 4), dif=round(preco - ref, 4), pct=round((preco - ref) / ref, 4),
                               impacto=round((preco - ref) * qtd, 2), alterado=str(it.get('lastChangeDate') or '')[:16]))
    linhas.sort(key=lambda x: -abs(x['impacto']))
    abaixo = [x for x in linhas if x['dif'] < 0]
    acima = [x for x in linhas if x['dif'] > 0]
    res = dict(inicio=ini.isoformat(), fim=hoje.isoformat(), itens=linhas, itens_comparados=comparados, n_pedidos=len({x['pedido'] for x in linhas}), n_pedidos_total=len(validos),
               abaixo=dict(n=len(abaixo), impacto=round(sum(x['impacto'] for x in abaixo), 2), metros=round(sum(x['metros'] for x in abaixo), 2)),
               acima=dict(n=len(acima), impacto=round(sum(x['impacto'] for x in acima), 2), metros=round(sum(x['metros'] for x in acima), 2)),
               metros_divergentes=round(sum(x['metros'] for x in linhas), 2), metros_total=round(sum(_num(i.get('quantity')) for p, _l in validos for i in p['items']), 2))
    _CACHE[ini] = (time.time(), res)
    return res
