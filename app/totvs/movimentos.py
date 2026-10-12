"""Movimentacoes de uma OP no local 23 (MALHARIA PESAGEM) pela API do TOTVS Moda (so consultas de leitura).

  - production-order/orders/search (expand locations): dados da OP, maquina e quanto esta agora em cada local
  - production/v2/movements: historico de movimentos da OP. Datas obrigatorias e no maximo 30 dias por consulta,
    entao a OP e consultada em blocos de 29 dias, da criacao dela ate hoje.

A peca so aparece quando ja passou do local 23 para o 24 (movimento 23 -> 24, a mesma linha do Relatorio 0032);
a quantidade desse movimento e o metro da peca. A hora (visto_em) e o momento em que o Gestao viu essa passagem.
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


def calcular(movs: list, op, metro_padrao):
    """Pecas que ja passaram do local 23 (MALHARIA PESAGEM) para o 24, na ordem em que passaram, e metros que ja seguiram.
    A peca so aparece depois que o movimento 23 -> 24 existe (e a mesma linha do Relatorio 0032); a quantidade do movimento e o metro da peca."""
    movs = [m for m in movs if str(m.get('productionOrder')) == str(op) and not m.get('isReversed')]
    movs.sort(key=lambda m: (str(m.get('movementDate')), m.get('sequence') or 0))
    lista, saiu = [], 0.0
    for m in movs:
        origem, destino, q = m.get('originLocationCode'), m.get('destinationLocationCode'), _num(m.get('totalQuantity'))
        if origem == LOCAL and destino is not None:
            saiu += q                                              # seguiu para o proximo local
        if origem == LOCAL and destino == 24 and m.get('movementType') != 'FinalizationMovement':
            lista.append(dict(item=len(lista) + 1, data=str(m.get('movementDate'))[:10], sequencia=m.get('sequence'),
                              acerto=round(q - metro_padrao, 3) if metro_padrao else 0.0, metros=round(q, 3)))
    return lista, saiu


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


# status interno (usado pelo resto do Gestao) de cada situacao automatica
FASE1, FASE2 = 'PCP - Fase¹', 'PCP - Fase²'
STATUS_INTERNO = {'PRODUZINDO': 'PRODUZINDO', 'MALHARIA': 'EM_SEQUENCIA', 'ESTOQUE': 'EM_SEQUENCIA', 'BLOQUEADA': 'EM_SEQUENCIA', FASE1: 'PCP', FASE2: 'PCP',
                  'FINALIZADA': 'FINALIZADO', 'CANCELADA': 'CANCELADO'}


def situacao_op(o: dict) -> str:
    """Onde a OP esta no TOTVS: local 23 = MALHARIA, 34 = ESTOQUE, local 22 = PCP - Fase², aguardando liberacao (sem movimento) = PCP - Fase¹.
    Sem nada no 23 e com material nos locais seguintes (24, 29, 30, 31), ou OP finalizada/cancelada = pesagem do cru terminou."""
    q = {}
    for l in o.get('locations') or []:
        q[l.get('locationCode')] = q.get(l.get('locationCode'), 0.0) + _num(l.get('quantity'))
    if o.get('status') == 40:
        return 'CANCELADA'                                         # cancelada no TOTVS: sai da fila e vai para o historico de canceladas
    if o.get('status') == 30:
        return 'FINALIZADA'
    if o.get('status') == 5:
        return 'BLOQUEADA'                                         # bloqueada no TOTVS: continua na fila, so mostra o status
    if q.get(23, 0) > 0.5:
        return 'MALHARIA'
    if q.get(34, 0) > 0.5:
        return 'ESTOQUE'
    if q.get(22, 0) > 0.5:
        return FASE2
    if sum(q.get(c, 0) for c in (24, 29, 30, 31)) > 0.5:
        return 'FINALIZADA'
    return FASE1                                                   # status 10 do TOTVS: aguardando liberacao


def atualizar_situacoes(info: dict, pecas_por_op: dict) -> None:
    """Define a situacao de cada OP da Programacao pelo local dela no TOTVS e faz a troca automatica:
    em cada maquina, a primeira OP da fila que esta na MALHARIA vira PRODUZINDO; quando a pesagem do cru dela termina, a proxima assume."""
    from app import db
    from app.models import HistoricoProducao, Sequencia
    por_maquina = {}
    for s in Sequencia.query.all():
        por_maquina.setdefault(s.machine_id, []).append(s)
    for lista in por_maquina.values():
        lista.sort(key=lambda s: (s.sequence_order if s.sequence_order is not None else 10 ** 6, s.id))
        produzindo = False
        for s in lista:
            o = info.get(str(s.op))
            if not o:
                continue                                           # OP que o TOTVS nao devolveu: nao mexe
            sit = situacao_op(o)
            if sit == 'MALHARIA' and not produzindo:
                sit, produzindo = 'PRODUZINDO', True
            novo = STATUS_INTERNO[sit]
            pecas = pecas_por_op.get(str(s.op))
            if pecas is not None:
                s.pecas_produzidas = pecas                          # pecas pesadas no local 23 (igual ao quantity_pieces do PCP Hub)
            if s.status != novo:                                   # historico das trocas (Finalizadas, Canceladas...) como no PCP Hub
                motivo = {'FINALIZADO': 'Pesagem do cru concluida no local 23 (automatico pelo TOTVS)', 'CANCELADO': 'Cancelada no TOTVS'}.get(
                    novo, 'Automatico pelo TOTVS (local da OP)')
                db.session.add(HistoricoProducao(
                    machine=str(s.machine.number) if s.machine else None, date=date.today(), artigo=s.artigo, cliente=s.cliente, op=s.op,
                    quantity_pieces=s.pecas_produzidas or 0, requested_quantity=s.qt_solicitada or 0, old_status=s.status, new_status=novo,
                    changed_at=datetime.utcnow(), reason=motivo, estimated_end_date=s.termino_malharia,
                    client_date=s.dt_cliente, total_pcs=s.total_pcs))
            if s.situacao != sit or s.status != novo:
                s.situacao, s.status = sit, novo
    db.session.commit()


def atualizar_todas(app) -> bool:
    """Busca no TOTVS as pesagens do local 23 de todas as OPs da Programacao e grava no banco."""
    from app import db
    from app.models import PesagemMalharia, PesagemResumo, Sequencia
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
                                               listNormalMovement=True, listFinalizationMovement=False, reversalType=2))
                    if r.status_code != 200:
                        raise RuntimeError(f'Movimentos: HTTP {r.status_code} {r.text[:160]}')
                    movs += r.json().get('items') or []
                    if ini <= inicio:
                        break
                    fim = ini - timedelta(days=1)

            por_op = {}
            for m in movs:
                por_op.setdefault(str(m.get('productionOrder')), []).append(m)
            agora = datetime.utcnow()
            # hora = quando o Gestao viu a peca (a API do TOTVS so tem a data). Guarda a de antes; pecas novas de OP ja conhecida ganham a hora de agora
            antigas = {(a.op, a.item, a.data): a.visto_em for a in PesagemMalharia.query.all()}
            ops_conhecidas = {r.op for r in PesagemResumo.query.all()}
            agora_local = datetime.now()
            novas, resumos, total_pecas = [], [], 0
            for op in ops:
                o = info.get(op)
                lista, saiu = calcular(por_op.get(op, []), op, padroes[op])
                total_pecas += len(lista)
                for l in lista:
                    chave = (op, l['item'], _data(l['data']))
                    visto = antigas.get(chave) if chave in antigas else (agora_local if op in ops_conhecidas else None)
                    novas.append(PesagemMalharia(op=op, ciclo=13, item=l['item'], data=_data(l['data']), sequencia=l['sequencia'],
                                                 acerto=l['acerto'], metros=l['metros'], visto_em=visto, synced_at=agora))
                resumos.append(PesagemResumo(op=op, ciclo=13, encontrada=bool(o), maquina=maquinas[op] if maquinas[op] is not None else (o or {}).get('machineCode'),
                                             artigo_desc=(((o or {}).get('items') or [{}])[0].get('productName') or '')[:200] or None,
                                             metro_padrao=padroes[op], pecas=len(lista),
                                             metros=round(sum(l['metros'] or 0 for l in lista), 3) if padroes[op] else None,
                                             no_local=round(_no_local(o), 3) if o else 0.0, saiu=round(saiu, 3),
                                             quantidade_op=_num((o or {}).get('quantity')), atualizado_em=agora))
            try:
                PesagemMalharia.query.delete()
                PesagemResumo.query.delete()
                db.session.bulk_save_objects(novas)
                db.session.bulk_save_objects(resumos)
                db.session.commit()
            except Exception:
                db.session.rollback()
                raise
            atualizar_situacoes(info, {r.op: r.pecas for r in resumos})
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
