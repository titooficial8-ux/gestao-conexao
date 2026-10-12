"""Relatorio Pedidos alimentado pela API do TOTVS Moda (atualiza sozinho a cada 2 minutos).

Monta o relatorio assim (so consultas de leitura):
  1. sales-order/orders/search  -> pedidos (datas, situacao, quantidades por item)
  2. person/legal-entities/search -> nome fantasia do cliente
  3. product/products/search -> segmento (CLAS SEG) e estrutura (CLAS ESTRUT.) do artigo
  4. product/balances/search -> saldo do estoque 1 (expedicao)
  5. production-order/orders/search (ciclo 13) -> OP: quantidade, finalizada de 1a e pendente.
     A OP liga ao pedido por cliente + produto, usando a data (OP criada depois do pedido) e a quantidade
  6. contas: Chegada 30 = data do pedido + 30 dias; Qt. a faturar = finalizada de 1a - faturada
"""
import json
import os
import threading
import time
import traceback
from collections import defaultdict
from datetime import date, datetime, timedelta

from app import db
from app.models import PedidoRelatorio

from . import api

BASE_URL = api.BASE_URL
ORDERS_URL = f'{BASE_URL}/api/totvsmoda/sales-order/v2/orders/search'
OPS_URL = f'{BASE_URL}/api/totvsmoda/production-order/v2/orders/search'
LEGAL_URL = f'{BASE_URL}/api/totvsmoda/person/v2/legal-entities/search'
PRODUCTS_URL = f'{BASE_URL}/api/totvsmoda/product/v2/products/search'
BALANCES_URL = f'{BASE_URL}/api/totvsmoda/product/v2/balances/search'

EMPRESA = int(os.getenv('PEDIDOS_EMPRESA', '1'))
CICLO = int(os.getenv('PEDIDOS_CICLO', '13'))
ESTOQUE = int(os.getenv('PEDIDOS_ESTOQUE', '1'))             # saldo de expedicao
DIAS = int(os.getenv('PEDIDOS_DIAS', '180'))                 # pedidos feitos nos ultimos N dias
INTERVALO_MIN = float(os.getenv('PEDIDOS_INTERVALO_MIN', '2'))
STATUS_PEDIDO = ['InProgress', 'BillingReleased', 'PartiallyAnswered', 'Blocked', 'InComposition', 'InAnalysis']
STATUS_OP = [5, 10, 20]                                      # bloqueada, aguardando liberacao, em andamento
ROTULO_SITUACAO = {
    'InProgress': 'EM ANDAMENTO', 'BillingReleased': 'LIBERADO P/ FATURAMENTO', 'PartiallyAnswered': 'PARCIALMENTE ATENDIDO',
    'Attended': 'ATENDIDO', 'Blocked': 'BLOQUEADO', 'Canceled': 'CANCELADO', 'InComposition': 'EM COMPOSIÇÃO', 'InAnalysis': 'EM ANÁLISE',
}

ARQUIVO = api.OUT_DIR / 'pedidos_ultima.json'              # guarda a ultima atualizacao para aparecer mesmo depois de reiniciar


def _ler_ultima() -> dict:
    try:
        return json.loads(ARQUIVO.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


_ult = _ler_ultima()
ESTADO = dict(rodando=False, ultima=_ult.get('ultima'), msg='', erro=None, n=_ult.get('n', 0), duracao=None)
_trava = threading.Lock()


def configurado() -> bool:
    return bool(os.getenv('TOTVS_CLIENT_ID') and os.getenv('TOTVS_CLIENT_SECRET'))


def _data(v):
    try:
        return date.fromisoformat(str(v)[:10]) if v else None
    except ValueError:
        return None


def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0.0


def _paginar(cli, url, corpo, tam=500):
    pag, itens = 1, []
    while True:
        r = cli.post(url, dict(corpo, pageSize=tam, page=pag))
        if r.status_code != 200:
            raise RuntimeError(f'{url.split("/v2/")[1]}: HTTP {r.status_code} {r.text[:200]}')
        j = r.json()
        itens += j.get('items') or []
        if not j.get('hasNext'):
            return itens
        pag += 1


def _em_lotes(codigos, n=100):
    codigos = sorted({c for c in codigos if c is not None})
    for i in range(0, len(codigos), n):
        yield codigos[i:i + n]


def buscar() -> list[dict]:
    """Consulta o TOTVS e devolve as linhas do relatorio (uma por item de pedido)."""
    cli = api.TotvsModaClient()
    cli.autenticar()
    hoje = datetime.now().date()
    ESTADO['msg'] = 'Buscando pedidos...'
    pedidos = _paginar(cli, ORDERS_URL, dict(
        filter=dict(branchCodeList=[EMPRESA], startOrderDate=f'{hoje - timedelta(days=DIAS)}T00:00:00', endOrderDate=f'{hoje}T23:59:59',
                    orderStatusList=STATUS_PEDIDO), expand='items'))
    itens = []
    for p in pedidos:
        for it in p.get('items') or []:
            solicitada, faturada, pendente = _num(it.get('quantity')), _num(it.get('settledQuantity')), _num(it.get('pendingQuantity'))
            if pendente <= 0 and faturada <= 0:
                continue                                       # item todo cancelado
            itens.append((p, it, solicitada, faturada, pendente))
    clientes = {p['customerCode'] for p, *_ in itens}
    produtos = {it.get('productCode') for _, it, *_ in itens}

    ESTADO['msg'] = 'Buscando clientes, artigos e saldos...'
    fantasia = {}
    for lote in _em_lotes(clientes):
        for x in _paginar(cli, LEGAL_URL, dict(filter=dict(personCodeList=lote))):
            fantasia[x.get('code')] = (x.get('fantasyName') or x.get('name') or '').strip()
    segmento, estrutura = {}, {}
    for lote in _em_lotes(produtos):
        for x in _paginar(cli, PRODUCTS_URL, dict(filter=dict(productCodeList=lote), option=dict(branchInfoCode=EMPRESA), expand='classifications')):
            for c in x.get('classifications') or []:
                if c.get('typeCode') == 504:
                    segmento[x['productCode']] = c.get('code')
                elif c.get('typeCode') == 505:
                    estrutura[x['productCode']] = c.get('name')
    saldo = {}
    for lote in _em_lotes(produtos, 200):
        for x in _paginar(cli, BALANCES_URL, dict(filter=dict(productCodeList=lote),
                                                  option=dict(balances=[dict(branchCode=EMPRESA, stockCodeList=[ESTOQUE])])), tam=1000):
            for b in x.get('balances') or []:
                if b.get('stockCode') == ESTOQUE:
                    saldo[x['productCode']] = _num(b.get('stock'))

    ESTADO['msg'] = 'Buscando ordens de producao...'
    ops = _paginar(cli, OPS_URL, dict(filter=dict(branchCodeList=[EMPRESA], cycleCodeList=[CICLO], statusList=STATUS_OP),
                                      expand='items,orderFinishedQuantitiesTypes'))
    por_chave = defaultdict(list)                              # (cliente, produto) -> OPs
    for op in ops:
        for i in op.get('items') or []:
            por_chave[(op.get('customerCode'), i.get('productCode'))].append(op)
    for lista in por_chave.values():
        lista.sort(key=lambda o: (str(o.get('insertDate') or ''), o.get('orderCode') or 0))
    usado = defaultdict(float)                                 # quantidade de cada OP ja destinada a pedidos

    from app.main.routes import normalize_client_name          # filiais Ortobom entram como ORTOBOM, como no PCP Hub
    linhas = []
    for p, it, solicitada, faturada, pendente in sorted(itens, key=lambda t: (str(t[0].get('orderDate')), t[0].get('orderCode') or 0)):
        pid = it.get('productCode')
        dt_pedido = _data(p.get('orderDate'))
        op = None
        cands = [o for o in por_chave.get((p['customerCode'], pid), []) if (_data(o.get('insertDate')) or date.min) >= (dt_pedido or date.min)]
        for o in cands:                                        # 1a OP com sobra para este pedido; senao a 1a depois dele
            if _num(o.get('quantity')) - usado[o['orderCode']] >= pendente > 0:
                op = o
                break
        op = op or (cands[0] if cands else None)
        if op:
            usado[op['orderCode']] += pendente
        fin1 = _num((op.get('orderFinishedQuantitiesTypes') or {}).get('normalFinishedQuantity')) if op else 0.0
        prev = _data(it.get('billingForecastDate')) or _data(p.get('billingForecastDate'))
        linhas.append(dict(
            cod_pedido=str(p.get('orderCode')), nome_cliente=normalize_client_name(fantasia.get(p['customerCode']) or p.get('customerName')),
            dt_pedido=dt_pedido, dt_prevfaturamento=prev, dt_chegada30=(dt_pedido + timedelta(days=30)) if dt_pedido else None,
            tp_situacao=ROTULO_SITUACAO.get(p.get('statusOrder'), ''), cod_produto=str(pid), ds_produto=it.get('name'),
            cod_segmento=segmento.get(pid), estrutura=estrutura.get(pid),
            qt_solicitada=solicitada, qt_faturada=faturada, qt_pendente=pendente,
            nr_cicloop=str(op['cycleCode']) if op else '', nr_op=str(op['orderCode']) if op else '',
            qt_finalizada=fin1, qt_pendenteop=_num(op.get('pendingQuantity')) if op else 0.0,
            qt_afaturar=max(fin1 - faturada, 0.0) if op else 0.0, qt_saldo=saldo.get(pid, 0.0), region='BR'))
    return linhas


def gravar(linhas: list[dict]) -> None:
    """Troca os pedidos BR pelos novos (os de outras regioes ficam como estao)."""
    agora = datetime.utcnow()
    try:
        PedidoRelatorio.query.filter(db.or_(PedidoRelatorio.region == 'BR', PedidoRelatorio.region.is_(None))).delete(synchronize_session=False)
        db.session.bulk_save_objects([PedidoRelatorio(synced_at=agora, **l) for l in linhas])
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise


def atualizar(app) -> bool:
    """Uma rodada completa. Devolve False se ja houver outra em andamento."""
    if not _trava.acquire(blocking=False):
        return False
    t0 = time.time()
    ESTADO.update(rodando=True, erro=None, msg='Conectando ao TOTVS...')
    try:
        linhas = buscar()
        if not linhas:
            raise RuntimeError('O TOTVS nao devolveu pedidos; mantive os dados anteriores.')
        with app.app_context():
            gravar(linhas)
        ESTADO.update(ultima=datetime.now().isoformat(timespec='seconds'), n=len(linhas), msg=f'{len(linhas)} itens de pedido atualizados')
        try:
            ARQUIVO.parent.mkdir(parents=True, exist_ok=True)
            ARQUIVO.write_text(json.dumps(dict(ultima=ESTADO['ultima'], n=len(linhas))), encoding='utf-8')
        except OSError:
            pass
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        ESTADO.update(erro=str(e)[:300], msg='Falha ao atualizar - mantive os dados anteriores')
    finally:
        ESTADO.update(rodando=False, duracao=round(time.time() - t0, 1))
        _trava.release()
    return ESTADO['erro'] is None


def iniciar_agendador(app) -> None:
    """Thread: atualiza logo ao subir e depois a cada PEDIDOS_INTERVALO_MIN minutos (padrao 2)."""
    if not configurado():
        return

    def loop():
        time.sleep(15)
        while True:
            try:
                atualizar(app)
            except Exception:  # noqa: BLE001
                traceback.print_exc()
            time.sleep(max(INTERVALO_MIN, 1) * 60)
    threading.Thread(target=loop, daemon=True, name='totvs-pedidos').start()
