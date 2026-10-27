"""Mapa de um artigo: todos os pedidos (carteira e atendidos), todas as OPs do ciclo e o vinculo pedido <-> OP calculado, lidos ao vivo do TOTVS."""
import threading
import traceback
from datetime import datetime, timedelta

from app.totvs import api, pedidos as P

ESTADO = dict(rodando=False, resultado=None, erro=None, artigo=None)
_trava = threading.Lock()
STATUS_OP_NOME = {5: 'bloqueada', 10: 'aguardando liberação', 20: 'em andamento', 30: 'finalizada', 40: 'cancelada'}


def calcular(artigo: str) -> dict:
    cli = api.TotvsModaClient()
    cli.autenticar()
    hoje = datetime.now().date()
    todos = P._paginar(cli, P.ORDERS_URL, dict(filter=dict(branchCodeList=[P.EMPRESA], startOrderDate=f'{hoje - timedelta(days=P.DIAS)}T00:00:00', endOrderDate=f'{hoje}T23:59:59',
                                                         orderStatusList=P.STATUS_PEDIDO), expand='items'))
    itens = []
    for p in todos:
        for it in p.get('items') or []:
            if str(it.get('name') or '').strip().startswith(artigo):
                itens.append((p, it, P._num(it.get('quantity')), P._num(it.get('settledQuantity')), P._num(it.get('pendingQuantity'))))
    produtos = {it.get('productCode') for _, it, *_ in itens}
    ops = [o for o in P._paginar(cli, P.OPS_URL, dict(filter=dict(branchCodeList=[P.EMPRESA], cycleCodeList=[P.CICLO], statusList=[5, 10, 20, 30]), expand='items'))
           if any(i.get('productCode') in produtos for i in o.get('items') or [])]
    ordenados = sorted(itens, key=lambda t: (str(t[0].get('orderDate')), t[0].get('orderCode') or 0))
    aloc = P.vincular_ops(ordenados, ops)
    ped_linhas, op_para_ped = [], {}
    for k, (p, it, sol, fat, pend) in enumerate(ordenados):
        vinc = [o['orderCode'] for o in aloc.get(k, [])]
        for oc in vinc:
            op_para_ped.setdefault(oc, []).append(p.get('orderCode'))
        ped_linhas.append(dict(cliente=p.get('customerName'), pedido=p.get('orderCode'), data=str(p.get('orderDate'))[:10], status=P.ROTULO_SITUACAO.get(p.get('statusOrder'), p.get('statusOrder')),
                               solicitada=sol, faturada=fat, cancelada=P._num(it.get('canceledQuantity')), pendente=pend, prevista=str(it.get('billingForecastDate') or '')[:10],
                               alterado=str(it.get('lastChangeDate') or '')[:10], ops=vinc, produto=it.get('productCode')))
    op_linhas = []
    for o in sorted(ops, key=lambda o: (str(o.get('insertDate')), o.get('orderCode'))):
        op_linhas.append(dict(op=o.get('orderCode'), ciclo=o.get('cycleCode'), cliente=o.get('customerName'), qt=P._num(o.get('quantity')), produzida=P._num(o.get('finishedQuantity')),
                              pendente=P._num(o.get('pendingQuantity')), status=STATUS_OP_NOME.get(o.get('status'), o.get('status')), criada=str(o.get('insertDate'))[:10],
                              maquina=o.get('machineCode'), pedidos=op_para_ped.get(o.get('orderCode'), [])))
    return dict(artigo=artigo, pedidos=ped_linhas, ops=op_linhas, gerado_em=datetime.now().isoformat(timespec='seconds'))


def iniciar(artigo: str) -> bool:
    if not _trava.acquire(blocking=False):
        return False

    def run():
        ESTADO.update(rodando=True, resultado=None, erro=None, artigo=artigo)
        try:
            ESTADO['resultado'] = calcular(artigo)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            ESTADO['erro'] = f'{type(e).__name__}: {str(e)[:300]}'
        finally:
            ESTADO['rodando'] = False
            _trava.release()
    threading.Thread(target=run, daemon=True, name='totvs-mapa-artigo').start()
    return True
