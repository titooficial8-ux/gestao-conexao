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
BATCH_URL = f'{BASE_URL}/api/totvsmoda/product/v2/batch/search'
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


def calcular(movs: list, op, metro_padrao):
    """Pecas pesadas (na ordem) e metros que ja seguiram para o proximo local, a partir dos movimentos da OP."""
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
    return lista, saiu


def finalizacoes(movs: list, op) -> list:
    """Pecas finalizadas (todas as finalizacoes, inclusive 2a qualidade: o lote do TOTVS tem todas), na ordem em que foram finalizadas."""
    fins = [m for m in movs if str(m.get('productionOrder')) == str(op) and m.get('movementType') == 'FinalizationMovement' and not m.get('isReversed')]
    fins.sort(key=lambda m: (str(m.get('movementDate')), m.get('sequence') or 0))
    return [dict(item=i + 1, data=str(m.get('movementDate'))[:10], sequencia=m.get('sequence'), metros=_num(m.get('totalQuantity'))) for i, m in enumerate(fins)]


def casar_lote(fins: list, lotes: list, usados: set):
    """Acha o lote do TOTVS que e a finalizacao da OP: mesma quantidade de pecas e os mesmos metros, na mesma ordem.
    Devolve (codigo do lote, itens do lote) ou (None, []). Lote ja usado por outra OP nao entra."""
    mets = [f['metros'] for f in fins]
    if not mets:
        return None, []
    achados = []
    for b in lotes:
        if b.get('batchCode') in usados:
            continue
        its = sorted(b.get('batchItem') or [], key=lambda i: i.get('batchItemSequential') or 0)
        if len(its) == len(mets) and all(abs(_num(i.get('quantityItem')) - m) < 0.51 for i, m in zip(its, mets)):
            achados.append((b['batchCode'], its))
    if not achados:
        return None, []
    return max(achados, key=lambda a: a[0])                    # varios iguais: o lote mais novo


def _no_local(o: dict) -> float:
    return sum(_num(l.get('quantity')) for l in (o.get('locations') or []) if l.get('locationCode') == LOCAL)


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

    lista, saiu = calcular(movs, op, metro_padrao)
    no_local = _no_local(o)
    resultado = dict(
        op=str(op), ciclo=ciclo, encontrada=True, local=NOME_LOCAL, pecas=lista,
        maquina=o.get('machineCode'), maquina_descricao=o.get('machineDescription'), metro_padrao=metro_padrao,
        resumo=dict(pecas=len(lista), metros=round(sum(l['metros'] or 0 for l in lista), 3) if metro_padrao else None,
                    no_local=round(no_local, 3), saiu=round(saiu, 3), quantidade_op=_num(o.get('quantity')),
                    atualizado_em=datetime.now().isoformat(timespec='seconds')))
    _cache[chave] = (time.time(), resultado)
    return resultado


# ---------------------------------------------------------------------------
# Atualizacao automatica: todas as OPs da Programacao, de uma vez, a cada MOVIMENTOS_INTERVALO_SEG (padrao 10 s)
# ---------------------------------------------------------------------------
import os
import traceback

INTERVALO_SEG = float(os.getenv('MOVIMENTOS_INTERVALO_SEG', '10'))     # a busca leva ~3 s, entao 10 s e seguro
DIAS_HISTORICO = int(os.getenv('MOVIMENTOS_DIAS', '150'))
ESTADO = dict(rodando=False, ultima=None, erro=None, ops=0, pecas=0, duracao=None)
_trava_auto = threading.Lock()


def _metro_padrao(seq):
    from app.main.routes import resolve_metros_from_article
    try:
        v = float(str(resolve_metros_from_article(seq.artigo) or '').replace(',', '.'))
    except ValueError:
        v = 0.0
    if not v and seq.total_pcs and seq.qt_solicitada:
        v = round(seq.qt_solicitada / seq.total_pcs)              # 2096 m / 66 pecas = 32 m
    return v or None


def atualizar_todas(app) -> bool:
    """Busca no TOTVS as pesagens do local 23 de todas as OPs da Programacao e grava no banco."""
    from app import db
    from app.models import PesagemAcabado, PesagemMalharia, PesagemResumo, Sequencia
    if not _trava_auto.acquire(blocking=False):
        return False
    t0 = time.time()
    ESTADO.update(rodando=True, erro=None)
    try:
        with app.app_context():
            seqs = {}
            for s in Sequencia.query.all():
                if s.op and str(s.op).isdigit():
                    seqs.setdefault(str(s.op), s)
            if not seqs:
                raise RuntimeError('Nenhuma OP na Programacao.')
            padroes = {op: _metro_padrao(s) for op, s in seqs.items()}
            maquinas = {op: (s.machine.number if s.machine else None) for op, s in seqs.items()}
            ops = sorted(seqs, key=int)

            with _trava:
                cli = _cliente()
                info = {}
                for i in range(0, len(ops), 100):
                    r = cli.post(OPS_URL, dict(filter=dict(branchCodeList=[EMPRESA], cycleCodeList=[13], orderCodeList=[int(o) for o in ops[i:i + 100]]),
                                                expand='items,locations', pageSize=500, page=1))
                    if r.status_code != 200:
                        raise RuntimeError(f'OPs: HTTP {r.status_code} {r.text[:160]}')
                    for o in r.json().get('items') or []:
                        info[str(o.get('orderCode'))] = o
                hoje = date.today()
                inicio = min([_data(o.get('insertDate')) for o in info.values() if _data(o.get('insertDate'))] or [hoje - timedelta(days=60)])
                inicio = max(inicio, hoje - timedelta(days=DIAS_HISTORICO))
                movs, fim = [], hoje
                while True:
                    ini = max(inicio, fim - timedelta(days=JANELA_DIAS))
                    r = cli.post(MOV_URL, dict(branchCodeList=[EMPRESA], cycleCodeList=[13], productionOrderCodeList=ops,
                                               movementStartDate=ini.isoformat(), movementEndDate=fim.isoformat(),
                                               listNormalMovement=True, listFinalizationMovement=True, reversalType=2))
                    if r.status_code != 200:
                        raise RuntimeError(f'Movimentos: HTTP {r.status_code} {r.text[:160]}')
                    movs += r.json().get('items') or []
                    if ini <= inicio:
                        break
                    fim = ini - timedelta(days=1)

            por_op = {}
            for m in movs:
                por_op.setdefault(str(m.get('productionOrder')), []).append(m)

            # pecas finalizadas + peso (kg) vindo do lote do TOTVS (product/batch/search)
            fins_op = {op: finalizacoes(por_op.get(op, []), op) for op in ops}
            produto_op = {op: ((info.get(op) or {}).get('items') or [{}])[0].get('productCode') for op in ops}
            lotes_produto = {}
            produtos = sorted({produto_op[op] for op in ops if fins_op[op] and produto_op[op]})
            with _trava:
                for i in range(0, len(produtos), 40):
                    pag = 1
                    while True:
                        r = cli.post(BATCH_URL, dict(filter=dict(branchCode=EMPRESA, productCodeList=produtos[i:i + 40],
                                                                 updatedDateStart=f'{inicio.isoformat()}T00:00:00', updatedDateEnd=f'{hoje.isoformat()}T23:59:59'),
                                                     page=pag, pageSize=1000))
                        if r.status_code != 200:
                            raise RuntimeError(f'Lotes: HTTP {r.status_code} {r.text[:160]}')
                        j = r.json()
                        for b in j.get('items') or []:
                            lotes_produto.setdefault(b.get('productCode'), []).append(b)
                        if not j.get('hasNext'):
                            break
                        pag += 1
            agora = datetime.utcnow()
            novas, resumos, total_pecas, acabadas, usados = [], [], 0, [], set()
            for op in ops:
                fins = fins_op[op]
                lote, itens = casar_lote(fins, lotes_produto.get(produto_op[op], []), usados) if fins else (None, [])
                if lote:
                    usados.add(lote)
                for f, it in zip(fins, itens or [None] * len(fins)):
                    acabadas.append(PesagemAcabado(op=op, item=f['item'], data=_data(f['data']), sequencia=f['sequencia'], metros=f['metros'],
                                                   peso=_num(it.get('quantityWeigth')) if it else None, lote=lote, synced_at=agora))
                o = info.get(op)
                lista, saiu = calcular(por_op.get(op, []), op, padroes[op])
                total_pecas += len(lista)
                for l in lista:
                    novas.append(PesagemMalharia(op=op, ciclo=13, item=l['item'], data=_data(l['data']), sequencia=l['sequencia'],
                                                 acerto=l['acerto'], metros=l['metros'], synced_at=agora))
                resumos.append(PesagemResumo(op=op, ciclo=13, encontrada=bool(o), maquina=maquinas[op] if maquinas[op] is not None else (o or {}).get('machineCode'),
                                             metro_padrao=padroes[op], pecas=len(lista),
                                             metros=round(sum(l['metros'] or 0 for l in lista), 3) if padroes[op] else None,
                                             no_local=round(_no_local(o), 3) if o else 0.0, saiu=round(saiu, 3),
                                             quantidade_op=_num((o or {}).get('quantity')), atualizado_em=agora))
            try:
                PesagemMalharia.query.delete()
                PesagemResumo.query.delete()
                PesagemAcabado.query.delete()
                db.session.bulk_save_objects(acabadas)
                db.session.bulk_save_objects(novas)
                db.session.bulk_save_objects(resumos)
                db.session.commit()
            except Exception:
                db.session.rollback()
                raise
        ESTADO.update(ultima=datetime.now().isoformat(timespec='seconds'), ops=len(ops), pecas=total_pecas)
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        ESTADO.update(erro=str(e)[:300])
    finally:
        ESTADO.update(rodando=False, duracao=round(time.time() - t0, 1))
        _trava_auto.release()
    return ESTADO['erro'] is None


def iniciar_agendador(app) -> None:
    """Thread: atualiza ao subir e depois a cada MOVIMENTOS_INTERVALO_SEG segundos, com ou sem alguem olhando a tela."""
    if not (os.getenv('TOTVS_CLIENT_ID') and os.getenv('TOTVS_CLIENT_SECRET')):
        return

    def loop():
        time.sleep(25)
        while True:
            try:
                atualizar_todas(app)
            except Exception:  # noqa: BLE001
                traceback.print_exc()
            time.sleep(max(INTERVALO_SEG, 5))
    threading.Thread(target=loop, daemon=True, name='totvs-movimentos').start()
