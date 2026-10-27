"""Materia-prima demandada pela producao (aba Programacao > Dashboard de materia-prima), direto do TOTVS (somente leitura).

Fontes:
  production-order/v2/pending-material-consumption (PCPFP164)
      por materia-prima: quantidade das OPs EM ANDAMENTO e AGUARDANDO LIBERACAO  -> o que as OPs em maquina vao consumir
  production/v2/material-requirements (PCPFL022)
      por materia-prima: demanda liquida (pendente) dos lotes de planejamento    -> o que ainda vai virar OP
  production-order/v2/orders/search?expand=materials (opcional)
      materiais de cada OP, quando o TOTVS trouxer quantidade                    -> detalhe por OP

As duas primeiras exigem a lista de materias-primas: usa a lista do almoxarifado (MP em KG, ativas).
Atualiza a cada EMPENHO_INTERVALO_MIN minutos (padrao 5) e guarda em instance/totvs/empenho_op.json.
"""
import json
import os
import re
import threading
import time
import traceback
from datetime import datetime

from . import api

BASE_URL = api.BASE_URL
EMPRESA = int(os.getenv('PEDIDOS_EMPRESA', '1'))
CICLO = int(os.getenv('EMPENHO_CICLO', '13'))
INTERVALO_MIN = max(2, int(os.getenv('EMPENHO_INTERVALO_MIN', '5')))
ARQUIVO = api.OUT_DIR / 'empenho_op.json'
PEND_URL = f'{BASE_URL}/api/totvsmoda/production-order/v2/pending-material-consumption'
REQ_URL = f'{BASE_URL}/api/totvsmoda/production/v2/material-requirements'
OPS_URL = f'{BASE_URL}/api/totvsmoda/production-order/v2/orders/search'
ESTADO = dict(rodando=False, ultima=None, erro=None, msg='')
_trava = threading.Lock()
_SW = {}


def configurado() -> bool:
    return bool(os.getenv('TOTVS_CLIENT_ID') and os.getenv('TOTVS_CLIENT_SECRET'))


def ler() -> dict:
    try:
        return json.loads(ARQUIVO.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _lotes(lista, n):
    for i in range(0, len(lista), n):
        yield lista[i:i + n]


# ---------------------------------------------------------------------------
# consultas
# ---------------------------------------------------------------------------
def buscar_pendente(cli, codigos: list, log: list) -> dict:
    """{codigo: dict(nome, andamento, aguardando)} das OPs em andamento e aguardando liberacao."""
    out = {}
    for lote in _lotes(codigos, 100):
        pag = 1
        while pag <= 20:
            r = cli.post(PEND_URL, dict(branchCode=EMPRESA, rawMaterialCodeList=lote, page=pag, pageSize=1000))
            if r.status_code != 200:
                log.append(dict(endpoint='pendente', http=r.status_code, erro=r.text[:200]))
                return out
            j = r.json()
            for it in j.get('items') or []:
                c = str(it.get('rawMaterialCode'))
                o = out.setdefault(c, dict(nome=it.get('rawMaterialDescription') or '', andamento=0.0, aguardando=0.0))
                o['andamento'] += _num(it.get('progressOrderQuantity')) or 0.0
                o['aguardando'] += _num(it.get('waitReleaseOrderQuantity')) or 0.0
            if not j.get('hasNext'):
                break
            pag += 1
    return out


def buscar_lotes(cli, codigos: list, log: list) -> dict:
    """{codigo: demanda liquida (kg)} dos lotes de planejamento ainda sem OP."""
    out = {}
    for lote in _lotes(codigos, 100):
        pag = 1
        while pag <= 20:
            r = cli.post(REQ_URL, dict(branchCode=EMPRESA, cycleCode=CICLO, rawMaterialCodes=lote, pageNumber=pag, pageSize=100))
            if r.status_code != 200:
                log.append(dict(endpoint='necessidade', http=r.status_code, erro=r.text[:200]))
                return out
            j = r.json()
            for it in j.get('items') or []:
                q = _num(it.get('pendingQuantity'))
                if q:
                    c = str(it.get('rawMaterialCode'))
                    out[c] = out.get(c, 0.0) + q
            if not j.get('hasNext'):
                break
            pag += 1
    return out


_PRIO = [('planned', 4), ('required', 4), ('estimated', 3), ('pending', 3), ('consum', 2), ('quantity', 1)]


def _qtd_item(item: dict):
    melhor = (None, -1)
    for k, v in item.items():
        n = _num(v)
        kl = k.lower()
        if n is None or 'percent' in kl or 'code' in kl or 'sequence' in kl:
            continue
        for chave, peso in _PRIO:
            if chave in kl and peso > melhor[1]:
                melhor = (n, peso)
    return melhor[0]


def buscar_materiais_op(cli, ops: list, log: list) -> dict:
    """{op: {codigo: [nome, qtd]}}: materiais de cada OP (expand=materials), quando a resposta trouxer quantidade."""
    out = {}
    for lote in _lotes(ops, 50):
        try:
            r = cli.post(OPS_URL, dict(filter=dict(branchCodeList=[EMPRESA], cycleCodeList=[CICLO], orderCodeList=lote), expand='materials', pageSize=100, page=1))
        except Exception as e:  # noqa: BLE001
            log.append(dict(endpoint='materiais_op', erro=str(e)[:160]))
            return out
        if r.status_code != 200:
            log.append(dict(endpoint='materiais_op', http=r.status_code, erro=r.text[:160]))
            return out
        for o in r.json().get('items') or []:
            op = str(o.get('orderCode'))
            mats = ((o.get('materials') or {}).get('orderMaterials')) or []
            for m in mats:
                q = _qtd_item(m)
                if q:
                    c = str(m.get('materialProductCode'))
                    out.setdefault(op, {})[c] = [m.get('materialProductName') or '', round(q, 3)]
    return out


# ---------------------------------------------------------------------------
# atualizacao automatica
# ---------------------------------------------------------------------------
def ops_da_programacao(app) -> list:
    """OPs programadas em maquina + OPs abertas da carteira de pedidos (Relatorio Pedidos) que ainda nao estao em maquina."""
    from app.models import PedidoRelatorio, Sequencia
    with app.app_context():
        ops = {int(s.op) for s in Sequencia.query.all() if s.op and str(s.op).isdigit() and (s.status or '') in ('PRODUZINDO', 'EM_SEQUENCIA', 'PCP')}
        carteira = {int(r.nr_op) for r in PedidoRelatorio.query.filter(PedidoRelatorio.nr_op.isnot(None), PedidoRelatorio.qt_pendente > 0).all() if r.nr_op and str(r.nr_op).isdigit()}
        return sorted(ops | set(sorted(carteira)[-600:]))


def atualizar(app) -> bool:
    if not _trava.acquire(blocking=False):
        return False
    from . import almoxarifado
    ESTADO.update(rodando=True, erro=None, msg='Lendo a matéria-prima demandada pela produção…')
    try:
        codigos = [i['codigo'] for i in almoxarifado.ler().get('itens', [])]
        if not codigos:
            raise RuntimeError('Ainda nao ha lista de materias-primas do almoxarifado (aguarde a primeira consulta).')
        cli = api.TotvsModaClient()
        cli.autenticar()
        log = []
        pend = buscar_pendente(cli, codigos, log)
        lotes = buscar_lotes(cli, codigos, log)
        try:
            ops = ops_da_programacao(app)
            por_op = buscar_materiais_op(cli, ops, log) if ops else {}
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            por_op = {}
        mats = {}
        for c in set(pend) | set(lotes):
            p = pend.get(c) or {}
            mats[c] = dict(nome=p.get('nome') or '', andamento=round(p.get('andamento', 0.0), 1), aguardando=round(p.get('aguardando', 0.0), 1), lotes=round(lotes.get(c, 0.0), 1))
        ARQUIVO.parent.mkdir(parents=True, exist_ok=True)
        tmp = ARQUIVO.with_suffix('.tmp')
        tmp.write_text(json.dumps(dict(versao=2, atualizado=datetime.now().isoformat(timespec='seconds'), materias=mats, por_op=por_op, n_codigos=len(codigos), log=log), ensure_ascii=False), encoding='utf-8')
        api.trocar_arquivo(tmp, ARQUIVO)
        if not mats and log:
            raise RuntimeError(log[0].get('erro') or 'O TOTVS nao devolveu consumo de materia-prima')
        ESTADO.update(ultima=datetime.now().isoformat(timespec='seconds'), msg=f'{len(mats)} matérias-primas com demanda')
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        ESTADO.update(erro=str(e)[:300], msg='Falha ao ler a demanda de matéria-prima')
    finally:
        ESTADO['rodando'] = False
        _trava.release()
    return ESTADO['erro'] is None


def iniciar_agendador(app) -> None:
    if not configurado():
        return

    def loop():
        time.sleep(75)
        while True:
            try:
                atualizar(app)
            except Exception:  # noqa: BLE001
                traceback.print_exc()
            time.sleep(INTERVALO_MIN * 60)
    threading.Thread(target=loop, daemon=True, name='totvs-empenho').start()


# ---------------------------------------------------------------------------
# dados do dashboard
# ---------------------------------------------------------------------------
def dashboard() -> dict:
    from app.models import Sequencia
    from . import almoxarifado
    d = ler()
    if d.get('versao') != 2 or not d.get('atualizado'):
        return dict(pronto=False, estado=dict(ESTADO))
    saldos = {str(i['codigo']): i for i in almoxarifado.ler().get('itens', [])}
    seqs = {str(s.op): s for s in Sequencia.query.all() if s.op}
    mats = []
    for c, m in (d.get('materias') or {}).items():
        s = saldos.get(c) or {}
        demanda = m['andamento'] + m['aguardando']
        total = demanda + m['lotes']
        sd = s.get('saldo')
        mats.append(dict(codigo=c, nome=s.get('nome') or m['nome'], fornecedor=s.get('fornecedor'), andamento=m['andamento'], aguardando=m['aguardando'], lotes=m['lotes'], demanda=round(demanda, 1), total=round(total, 1),
                         saldo=sd, falta=round(max(demanda - sd, 0), 1) if sd is not None else None, falta_total=round(max(total - sd, 0), 1) if sd is not None else None,
                         cobertura=round(sd / demanda * 100, 0) if sd is not None and demanda else None))
    mats = [m for m in mats if m['total'] > 0]
    from app.models import PedidoRelatorio
    ped_op = {}
    for r in PedidoRelatorio.query.filter(PedidoRelatorio.nr_op.isnot(None), PedidoRelatorio.qt_pendente > 0).all():
        lst = ped_op.setdefault(str(r.nr_op), [])
        if r.cod_pedido and r.cod_pedido not in [x['pedido'] for x in lst]:
            lst.append(dict(pedido=r.cod_pedido, cliente=r.nome_cliente or '', pendente=int(r.qt_pendente or 0), dt_pedido=r.dt_pedido.isoformat() if r.dt_pedido else None, dt_prev=r.dt_prevfaturamento.isoformat() if r.dt_prevfaturamento else None, artigo=r.ds_produto or ''))
    ops, empenhos = [], {}
    for op, itens in (d.get('por_op') or {}).items():
        s = seqs.get(op)
        peds = ped_op.get(op, [])
        if not s and not peds:
            continue
        lista = sorted(([c, v[0], v[1]] for c, v in itens.items()), key=lambda x: -x[2])
        datas = [x['dt_prev'] for x in peds if x['dt_prev']]
        entradas = [x['dt_pedido'] for x in peds if x['dt_pedido']]
        info = dict(op=op, origem='maquina' if s else 'carteira', maquina=s.machine.number if s and s.machine else None, artigo=(s.artigo if s else (peds[0]['artigo'] if peds else '')),
                    cliente=(s.cliente if s else (peds[0]['cliente'] if peds else '')), status=(s.status if s else 'CARTEIRA'), ordem=(s.sequence_order if s and s.sequence_order is not None else 10 ** 6),
                    termino=s.termino_malharia.isoformat() if s and s.termino_malharia else None, dt_entrada=min(entradas) if entradas else None, dt_prev=min(datas) if datas else (s.dt_cliente.isoformat() if s and s.dt_cliente else None),
                    pedidos=[dict(pedido=x['pedido'], cliente=x['cliente'], pendente=x['pendente'], dt_pedido=x['dt_pedido'], dt_prev=x['dt_prev']) for x in sorted(peds, key=lambda x: x['dt_prev'] or '9')[:4]])
        ops.append(dict(info, total=round(sum(x[2] for x in lista), 1), materias=[dict(codigo=c, nome=(saldos.get(c) or {}).get('nome') or n, kg=k) for c, n, k in lista]))
        for c, n, k in lista:
            empenhos.setdefault(c, []).append(dict(info, kg=k))

    def prioridade(e):                      # quem produz primeiro consome primeiro: em producao, depois fila das maquinas, depois carteira; dentro, a data mais proxima
        return (0 if e['status'] == 'PRODUZINDO' else 1 if e['origem'] == 'maquina' else 2, e['dt_prev'] or '9999', e['ordem'])
    for m in mats:
        lista = sorted(empenhos.get(m['codigo'], []), key=prioridade)
        acum, sd = 0.0, m['saldo']
        for e in lista:
            acum += e['kg']
            e['acum'] = round(acum, 1)
            e['coberta'] = None if sd is None else acum <= sd
        m['empenhos'] = lista[:40]
        m['n_empenhos'] = len(lista)
        m['kg_identificado'] = round(acum, 1)
        m['kg_sem_op'] = round(max(m['demanda'] - acum, 0), 1)
    return dict(pronto=True, atualizado=d.get('atualizado'), materias=sorted(mats, key=lambda x: -x['total']), ops=sorted(ops, key=lambda x: -x['total']), n_ops_programacao=len(ops_resumo(seqs)),
                estado=dict(ESTADO), log=d.get('log') or [])


def ops_resumo(seqs: dict) -> list:
    return [s for s in seqs.values() if (s.status or '') in ('PRODUZINDO', 'EM_SEQUENCIA', 'PCP')]
