"""Entrada de pedidos por dia (2024, 2025 e 2026) direto da API do TOTVS Moda (so consultas de leitura).

Regras (conferidas contra a planilha "Pedido de venda"):
  - vale a DATA DO PEDIDO (orderDate) e o valor liquido do pedido (netValue; nas exportacoes ja vem em R$)
  - entram so operacoes de VENDA (500 - venda ou revenda, 545 - venda exportacao...); ficam de fora amostras,
    remessas/substituicoes e pedidos cancelados
  - mercado externo (ME) = operacao de exportacao/USD; o resto e mercado interno (MI)
"""
import json
import os
import threading
import time
import traceback
from datetime import date, datetime, timedelta

from . import api, planilha_pedidos

ORDERS_URL = f'{api.BASE_URL}/api/totvsmoda/sales-order/v2/orders/search'
EMPRESA = int(os.getenv('PEDIDOS_EMPRESA', '1'))
ANOS_TOTVS = (2025, 2026)                                          # 2025 e 2026 vem do TOTVS
ANOS = (2023, 2024, 2025, 2026)                                    # 2023 e 2024 vem da planilha (planilha_pedidos)
RECENTE_DIAS = int(os.getenv('ENTRADA_RECENTE_DIAS', '75'))        # a cada rodada, refaz so os ultimos N dias (pedido muda de status)
LEGAL_URL = f'{api.BASE_URL}/api/totvsmoda/person/v2/legal-entities/search'
RAPIDA_DIAS = 3                                                    # ciclo rapido (a cada INTERVALO_SEG): so os ultimos dias
INTERVALO_SEG = float(os.getenv('ENTRADA_INTERVALO_SEG', '20'))
COMPLETA_MIN = float(os.getenv('ENTRADA_COMPLETA_MIN', '10'))      # a cada N min refaz tambem os ultimos RECENTE_DIAS (cancelamentos, mudancas)
VERSAO = 3                                                         # sobe quando muda o formato guardado: o historico e buscado de novo
ARQUIVO = api.OUT_DIR / 'entrada_pedidos.json'
_trava = threading.Lock()
ESTADO = dict(rodando=False, ultima=None, msg='', erro=None, n=0)


def configurado() -> bool:
    return bool(os.getenv('TOTVS_CLIENT_ID') and os.getenv('TOTVS_CLIENT_SECRET'))


def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0.0


def classificar(p: dict):
    """(entra?, 'MI'|'ME') de um pedido."""
    nome = str(p.get('operationName') or '').upper()
    if p.get('statusOrder') == 'Canceled':
        return False, 'MI'
    externo = any(k in nome for k in ('EXPORT', 'USD', 'DOLAR', 'DÓLAR'))
    entra = 'VENDA' in nome and 'AMOSTRA' not in nome and 'SUBSTITUI' not in nome
    return entra, 'ME' if externo else 'MI'


def _ler() -> dict:
    try:
        return json.loads(ARQUIVO.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def _gravar(d: dict) -> None:
    ARQUIVO.parent.mkdir(parents=True, exist_ok=True)
    tmp = ARQUIVO.with_suffix('.tmp')
    tmp.write_text(json.dumps(d, ensure_ascii=False), encoding='utf-8')
    tmp.replace(ARQUIVO)


def _janelas(ini: date, fim: date):
    """Blocos de ate ~1 mes (a API limita o periodo)."""
    a = ini
    while a <= fim:
        b = min(fim, (a.replace(day=1) + timedelta(days=32)).replace(day=1) - timedelta(days=1))
        yield a, b
        a = b + timedelta(days=1)


def _buscar(cli, ini: date, fim: date) -> list[dict]:
    out, pag = [], 1
    while True:
        r = cli.post(ORDERS_URL, dict(filter=dict(branchCodeList=[EMPRESA], startOrderDate=f'{ini}T00:00:00', endOrderDate=f'{fim}T23:59:59'),
                                      pageSize=500, page=pag))
        if r.status_code != 200:
            raise RuntimeError(f'pedidos {ini}..{fim}: HTTP {r.status_code} {r.text[:200]}')
        j = r.json()
        out += j.get('items') or []
        if not j.get('hasNext'):
            return out
        pag += 1


def _fantasias(cli, codigos: set, conhecidos: dict) -> dict:
    """Nome fantasia dos clientes que ainda nao estao no cache ({codigo: nome})."""
    faltam = sorted(c for c in codigos if c is not None and str(c) not in conhecidos)
    for k in range(0, len(faltam), 100):
        pag = 1
        while True:
            r = cli.post(LEGAL_URL, dict(filter=dict(personCodeList=faltam[k:k + 100]), pageSize=500, page=pag))
            if r.status_code != 200:
                break
            j = r.json()
            for x in j.get('items') or []:
                nome = (x.get('fantasyName') or x.get('name') or '').strip()
                if nome:
                    conhecidos[str(x.get('code'))] = nome
            if not j.get('hasNext'):
                break
            pag += 1
    return conhecidos


_ULT_COMPLETA = 0.0


def atualizar(rapida: bool = False) -> bool:
    """rapida=True: so os ultimos dias (roda a cada ~20 s para o pedido novo aparecer na hora); senao a rodada completa."""
    global _ULT_COMPLETA
    if not _trava.acquire(blocking=False):
        return False
    ESTADO.update(rodando=True, erro=None, msg='Conectando ao TOTVS...')
    try:
        cli = api.TotvsModaClient()
        cli.autenticar()
        dados = _ler()
        if dados.get('versao') != VERSAO:
            dados = {}
        pedidos = dados.get('pedidos', {})
        fantasia = dados.get('clientes', {})
        hoje = date.today()
        primeira = not pedidos
        if primeira:
            ini = date(ANOS_TOTVS[0], 1, 1)
        elif rapida:
            ini = hoje - timedelta(days=RAPIDA_DIAS)
        else:
            ini = hoje - timedelta(days=RECENTE_DIAS)
        for a, b in _janelas(ini, hoje):
            ESTADO['msg'] = f'Buscando pedidos de {a:%m/%Y}...'
            novos = _buscar(cli, a, b)
            for k in [k for k, v in pedidos.items() if a.isoformat() <= v[0] <= b.isoformat()]:   # o que sumiu/mudou no periodo sai
                pedidos.pop(k)
            for p in novos:
                entra, local = classificar(p)
                if entra and p.get('orderDate'):
                    pedidos[str(p['orderCode'])] = [str(p['orderDate'])[:10], round(_num(p.get('netValue')), 2), round(_num(p.get('quantity')), 2), local,
                                                    p.get('customerCode'), str(p.get('insertDate') or '')[:19], str(p.get('customerName') or '')[:60]]
        try:
            fantasia = _fantasias(cli, {v[4] for v in pedidos.values()}, fantasia)
        except Exception:  # noqa: BLE001  - sem o nome fantasia usa a razao social
            traceback.print_exc()
        _gravar(dict(versao=VERSAO, atualizado=datetime.now().isoformat(timespec='seconds'), pedidos=pedidos, clientes=fantasia))
        if not rapida:
            _ULT_COMPLETA = time.time()
        ESTADO.update(ultima=datetime.now().isoformat(timespec='seconds'), n=len(pedidos), msg=f'{len(pedidos)} pedidos')
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        ESTADO.update(erro=str(e)[:300], msg='Falha ao atualizar - mantive os dados anteriores')
    finally:
        ESTADO['rodando'] = False
        _trava.release()
    return ESTADO['erro'] is None


def iniciar_agendador(app=None) -> None:
    if not configurado():
        return

    def loop():
        time.sleep(25)
        while True:
            try:
                completa = time.time() - _ULT_COMPLETA > COMPLETA_MIN * 60
                atualizar(rapida=not completa)
            except Exception:  # noqa: BLE001
                traceback.print_exc()
            time.sleep(max(INTERVALO_SEG, 10))
    threading.Thread(target=loop, daemon=True, name='totvs-entrada-pedidos').start()


def resumo(mes: int | None = None) -> dict:
    """Dados da tela: por dia do mes escolhido e por mes, para cada ano."""
    dados = _ler()
    if dados.get('versao') != VERSAO:
        dados = {}
    pedidos = dados.get('pedidos', {})
    fantasia = dados.get('clientes', {})
    from app.main.routes import normalize_client_name
    hoje = date.today()
    mes = mes or hoje.month
    por_dia = {a: {} for a in ANOS}                 # ano -> dia -> [valor, n, metros, valor_me]
    por_mes = {a: [[0.0, 0, 0.0, 0.0] for _ in range(12)] for a in ANOS}
    por_cliente = {a: {} for a in ANOS}              # ano -> cliente -> [valor, n, 'MI'|'ME'] (so o mes escolhido)
    try:
        planilha_pedidos.sincronizar()                     # planilha na pasta do Gestao: le sozinho quando aparece ou e trocada
    except Exception:  # noqa: BLE001
        traceback.print_exc()
    plan = planilha_pedidos.ler()
    registros = []                                   # (data, valor, metros, local, cliente)
    for d, valor, metros, local, cod, _ins, razao in pedidos.values():
        if int(d[:4]) in ANOS_TOTVS:
            registros.append((d, valor, metros, local, normalize_client_name(fantasia.get(str(cod)) or razao) or 'SEM CLIENTE'))
    for d, valor, metros, local, cliente in plan.get('pedidos', []):
        if int(d[:4]) not in ANOS_TOTVS:
            registros.append((d, valor, metros, local, normalize_client_name(cliente) or 'SEM CLIENTE'))
    for d, valor, metros, local, nome in registros:
        a, m, dia = int(d[:4]), int(d[5:7]), int(d[8:10])
        if a not in por_mes:
            continue
        x = por_mes[a][m - 1]
        x[0] += valor; x[1] += 1; x[2] += metros; x[3] += valor if local == 'ME' else 0
        if m == mes:
            y = por_dia[a].setdefault(dia, [0.0, 0, 0.0, 0.0])
            y[0] += valor; y[1] += 1; y[2] += metros; y[3] += valor if local == 'ME' else 0
            c = por_cliente[a].setdefault((nome, local), [0.0, 0])
            c[0] += valor; c[1] += 1
    dias = []
    for dia in range(1, 32):
        linha = dict(dia=dia, anos={})
        for a in ANOS:
            try:
                dt = date(a, mes, dia)
            except ValueError:
                continue
            if dt > hoje:
                continue
            v = por_dia[a].get(dia, [0.0, 0, 0.0, 0.0])
            linha['anos'][str(a)] = dict(valor=round(v[0], 2), n=v[1], metros=round(v[2], 2), me=round(v[3], 2))
        if linha['anos']:
            dias.append(linha)
        elif dia <= 28:
            dias.append(linha)
    tot = {}
    for a in ANOS:
        v = [sum(por_dia[a].get(d, [0, 0, 0, 0])[i] for d in por_dia[a]) for i in range(4)]
        tot[str(a)] = dict(valor=round(v[0], 2), n=int(v[1]), metros=round(v[2], 2), me=round(v[3], 2), mi=round(v[0] - v[3], 2))
    meses = {str(a): [dict(valor=round(x[0], 2), n=x[1], metros=round(x[2], 2)) for x in por_mes[a]] for a in ANOS}
    clientes, clientes_loc = {}, {}
    for a in ANOS:
        junto = {}
        for (nome, _loc), v in por_cliente[a].items():
            j = junto.setdefault(nome, [0.0, 0]); j[0] += v[0]; j[1] += v[1]
        clientes[str(a)] = sorted(([n, round(v[0], 2), v[1]] for n, v in junto.items()), key=lambda x: -x[1])
        grupos = {}
        for loc in ('MI', 'ME'):
            itens = sorted(([n, round(v[0], 2), v[1]] for (n, l), v in por_cliente[a].items() if l == loc), key=lambda x: -x[1])
            grupos[loc] = dict(total=round(sum(i[1] for i in itens), 2), n=sum(i[2] for i in itens), itens=itens)
        clientes_loc[str(a)] = grupos
    ultimos = sorted(((cod, v) for cod, v in pedidos.items() if v[5]), key=lambda x: x[1][5], reverse=True)[:10]
    ultimos = [dict(codigo=cod, data=v[0], hora=v[5][11:16], valor=round(v[1], 2), metros=round(v[2]), me=v[3] == 'ME',
                    cliente=normalize_client_name(fantasia.get(str(v[4])) or v[6]) or 'SEM CLIENTE',
                    unidade=(fantasia.get(str(v[4])) or v[6]) if normalize_client_name(fantasia.get(str(v[4])) or v[6]) == 'ORTOBOM' else '') for cod, v in ultimos]
    return dict(mes=mes, anos=list(ANOS), clientes=clientes, clientes_loc=clientes_loc, ultimos=ultimos,
                planilha=dict(atualizado=plan.get('atualizado'), arquivo=plan.get('arquivo'), n=len(plan.get('pedidos', []))) if plan.get('pedidos') else None, hoje=hoje.isoformat(), atualizado=dados.get('atualizado'), dias=dias, total=tot, meses=meses,
                vazio=not pedidos, estado=dict(ESTADO))
