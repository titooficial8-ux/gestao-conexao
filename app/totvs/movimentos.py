"""Movimentacoes de uma OP no local 23 (MALHARIA PESAGEM) pela API do TOTVS Moda (so consultas de leitura).

  - production-order/orders/search (expand locations): dados da OP, maquina e quanto esta agora em cada local
  - production/v2/movements: historico de movimentos da OP. Datas obrigatorias e no maximo 30 dias por consulta,
    entao a OP e consultada em blocos de 29 dias, da criacao dela ate hoje.

Cada peca pesada na malharia vira um movimento de "Acerto" no local 23 sobre o metro padrao do artigo:
  entrada sem origem -> 23 (acerto positivo)  = metro padrao + acerto
  saida 23 -> sem destino (acerto negativo)   = metro padrao - acerto
A API nao tem o peso (kg) nem a hora da pesagem (so a data).
"""
import threading
import time
from datetime import date, datetime, timedelta

from . import api

BASE_URL = api.BASE_URL
OPS_URL = f'{BASE_URL}/api/totvsmoda/production-order/v2/orders/search'
MOV_URL = f'{BASE_URL}/api/totvsmoda/production/v2/movements'
EMPRESA = 1
LOCAL = 23
NOME_LOCAL = 'MALHARIA PESAGEM'
JANELA_DIAS = 29
MAX_JANELAS = 8                                               # ~8 meses de historico por OP

_cli = None
_trava = threading.Lock()
_cache: dict = {}


def _cliente():
    global _cli
    if _cli is None:
        _cli = api.TotvsModaClient()
        _cli.autenticar()
    return _cli


def _data(v):
    try:
        return date.fromisoformat(str(v)[:10]) if v else None
    except ValueError:
        return None


def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0.0


def pecas(op: str, ciclo: int = 13, metro_padrao: float | None = None) -> dict:
    """Pecas pesadas no local 23 da OP, na ordem em que foram pesadas."""
    chave = (str(op), ciclo, metro_padrao)
    quente = _cache.get(chave)
    if quente and time.time() - quente[0] < 15:
        return quente[1]
    with _trava:
        cli = _cliente()
        r = cli.post(OPS_URL, dict(filter=dict(branchCodeList=[EMPRESA], cycleCodeList=[ciclo], orderCodeList=[int(op)]),
                                    expand='locations', pageSize=10, page=1))
        if r.status_code != 200:
            raise RuntimeError(f'OP {op}: HTTP {r.status_code}')
        achadas = r.json().get('items') or []
        if not achadas:
            return dict(op=str(op), ciclo=ciclo, encontrada=False, pecas=[], resumo={})
        o = achadas[0]
        hoje = date.today()
        inicio = _data(o.get('insertDate')) or (hoje - timedelta(days=JANELA_DIAS * 3))
        movs, fim = [], hoje
        for _ in range(MAX_JANELAS):
            ini = max(inicio, fim - timedelta(days=JANELA_DIAS))
            r = cli.post(MOV_URL, dict(branchCodeList=[EMPRESA], cycleCodeList=[ciclo], productionOrderCodeList=[str(op)],
                                       movementStartDate=ini.isoformat(), movementEndDate=fim.isoformat(),
                                       listNormalMovement=True, listFinalizationMovement=False, reversalType=2))
            if r.status_code != 200:
                raise RuntimeError(f'Movimentos da OP {op}: HTTP {r.status_code} {r.text[:160]}')
            movs += r.json().get('items') or []
            if ini <= inicio:
                break
            fim = ini - timedelta(days=1)

    movs = [m for m in movs if str(m.get('productionOrder')) == str(op) and not m.get('isReversed')]
    movs.sort(key=lambda m: (str(m.get('movementDate')), m.get('sequence') or 0))
    lista, saiu = [], 0.0
    for m in movs:
        origem, destino, q = m.get('originLocationCode'), m.get('destinationLocationCode'), _num(m.get('totalQuantity'))
        if destino == LOCAL and origem is None:                    # peca pesada acima do metro padrao
            acerto = q
        elif origem == LOCAL and destino is None:                  # peca pesada abaixo do metro padrao
            acerto = -q
        else:
            if origem == LOCAL and destino is not None:
                saiu += q                                          # seguiu para o proximo local
            continue
        lista.append(dict(item=len(lista) + 1, data=str(m.get('movementDate'))[:10], sequencia=m.get('sequence'), acerto=round(acerto, 3),
                          metros=round(metro_padrao + acerto, 3) if metro_padrao else None))
    no_local = sum(_num(l.get('quantity')) for l in (o.get('locations') or []) if l.get('locationCode') == LOCAL)
    resultado = dict(
        op=str(op), ciclo=ciclo, encontrada=True, local=NOME_LOCAL, pecas=lista,
        maquina=o.get('machineCode'), maquina_descricao=o.get('machineDescription'), metro_padrao=metro_padrao,
        resumo=dict(pecas=len(lista), metros=round(sum(l['metros'] or 0 for l in lista), 3) if metro_padrao else None,
                    no_local=round(no_local, 3), saiu=round(saiu, 3), quantidade_op=_num(o.get('quantity')),
                    atualizado_em=datetime.now().isoformat(timespec='seconds')))
    _cache[chave] = (time.time(), resultado)
    return resultado
