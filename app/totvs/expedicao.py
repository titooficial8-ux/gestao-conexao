"""Expedicao: saldo do estoque 1 (Almoxarifado Produtivo) por lote e Notas Fiscais, direto do TOTVS (so leitura).

Saldo: product/v2/batch/search (branchCode, balanceCodeList=[1], hasBalance=true); saldo do produto = soma de batchItem.balance > 0
(mesmo criterio do relatorio 040 / VR_PRD_LOTEI). NAO usa product/v2/balances/search.
Notas: fiscal/v2/invoices/search (saidas). Guarda em instance/totvs/expedicao.json."""
import json
import os
import threading
import time
import traceback
from datetime import date, datetime, timedelta

from app import db

from . import api
from .pedidos import _em_lotes, _paginar

BASE = api.BASE_URL
BATCH_URL = f'{BASE}/api/totvsmoda/product/v2/batch/search'
BALANCES_URL = f'{BASE}/api/totvsmoda/product/v2/balances/search'
PRODUCTS_URL = f'{BASE}/api/totvsmoda/product/v2/products/search'
SALDO_INSPECAO = os.getenv('EXPEDICAO_SALDO_INSPECAO', '').strip()     # codigo do saldo 'aguardando inspecao'; vazio = acha pelo nome (INSPE)
UNIDADE_INSPECAO = os.getenv('EXPEDICAO_UNIDADE_INSPECAO', 'MT').strip().upper()
EMPRESA = int(os.getenv('PEDIDOS_EMPRESA', '1'))
SALDO = int(os.getenv('EXPEDICAO_SALDO', '1'))
NF_DIAS = int(os.getenv('EXPEDICAO_NF_DIAS', '365'))          # historico de NFs guardado (consulta de NF)
NF_RECENTE_DIAS = 90                                             # a cada rodada completa refaz so os ultimos 90 dias
INTERVALO_SEG = max(30, int(os.getenv('EXPEDICAO_INTERVALO_SEG', '120')))        # a cada 2 min
COMPLETA_MIN = 20
ARQUIVO = api.OUT_DIR / 'expedicao.json'
ARQ_MOV = api.OUT_DIR / 'expedicao_mov.json'
KARDEX_URL = f'{BASE}/api/totvsmoda/product/v2/kardex-movement'
MOV_INTERVALO_MIN = max(5, int(os.getenv('EXPEDICAO_MOV_MIN', '30')))
MOV_DIAS = 179                                                  # a API limita o kardex a 180 dias
_trava_mov = threading.Lock()
_fmt_data = {'so_data': False}
ESTADO = dict(rodando=False, ultima=None, erro=None, msg='', erro_notas=None, erro_estoque=None, erro_mov=None, erro_inspecao=None)
_trava = threading.Lock()
_ult_completa = 0.0
CHAVES_DATA = ('lastMovementDate', 'lastMovement', 'entryDate', 'inclusionDate', 'insertDate', 'creationDate', 'date', 'batchDate')

STATUS_NF = {'Normal': 'Emitida', 'Issued': 'Emitida', 'Canceled': 'Cancelada', 'Denied': 'Denegada', 'Deleted': 'Excluída'}
STATUS_NFE = {'Authorized': 'Autorizada', 'Canceled': 'Cancelada', 'Denied': 'Denegada', 'Rejected': 'Rejeitada', 'Pending': 'Pendente',
              'Sent': 'Enviada', 'Sending': 'Enviando', 'NotSent': 'Não enviada', 'Unused': 'Inutilizada', 'Disabled': 'Inutilizada'}


def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0.0


def ler() -> dict:
    try:
        return json.loads(ARQUIVO.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def _gravar(d: dict) -> None:
    ARQUIVO.parent.mkdir(parents=True, exist_ok=True)
    tmp = ARQUIVO.with_suffix('.tmp')
    tmp.write_text(json.dumps(d, ensure_ascii=False), encoding='utf-8')
    api.trocar_arquivo(tmp, ARQUIVO)


def _dia(v):
    try:
        return datetime.fromisoformat(str(v).replace('Z', '')[:19]).date().isoformat()
    except ValueError:
        return None


# ---------------------------------------------------------------- estoque
def _data_qualquer(lt: dict):
    """Qualquer campo com 'date' no nome (entrada/inclusao/movimento de preferencia) que o TOTVS mande no lote."""
    cand = [(k, _dia(v)) for k, v in lt.items() if 'date' in k.lower() and _dia(v)]
    for pref in ('entry', 'input', 'inclus', 'insert', 'creat', 'moviment', 'product'):
        for k, d in cand:
            if pref in k.lower():
                return d
    return cand[0][1] if cand else None


def _lotes_do_item(x: dict) -> list:
    for k in ('batchItem', 'batchItems', 'items', 'batches'):
        if isinstance(x.get(k), list):
            return x[k]
    return [x] if 'balance' in x else []


def _buscar_lotes(cli) -> list:
    """Itens do batch/search. Primeiro sem lista de produtos; se a API exigir, descobre os produtos com saldo e consulta em blocos."""
    filtro = dict(branchCode=EMPRESA, balanceCodeList=[SALDO], hasBalance=True)
    try:
        return _paginar(cli, BATCH_URL, dict(filter=filtro), tam=500)
    except RuntimeError as e:
        ESTADO['msg'] = f'batch sem lista de produtos recusado ({str(e)[:80]}); usando lista de produtos'
    produtos = set()
    for x in _paginar(cli, BALANCES_URL, dict(filter=dict(hasBalance=True), option=dict(balances=[dict(branchCode=EMPRESA, stockCodeList=[SALDO])])), tam=1000):
        if any(_num(b.get('stock')) > 0 and b.get('stockCode') == SALDO for b in x.get('balances') or []):
            produtos.add(x.get('productCode'))
    itens = []
    for lote in _em_lotes(produtos, 100):
        itens += _paginar(cli, BATCH_URL, dict(filter=dict(filtro, productCodeList=lote)), tam=500)
    return itens


def atualizar_estoque(cli, dados: dict) -> None:
    itens = _buscar_lotes(cli)
    hoje = date.today().isoformat()
    visto = dados.get('visto', {})                          # 1a vez que o Gestao viu o lote (quando o TOTVS nao informa a data do lote)
    produtos, amostra = {}, None
    for x in itens:
        pc = str(x.get('productCode') or x.get('code') or '')
        if not pc:
            continue
        for lt in _lotes_do_item(x):
            bal = _num(lt.get('balance'))
            if bal <= 0:
                continue
            amostra = amostra or dict(item={k: v for k, v in x.items() if not isinstance(v, (list, dict))}, lote=lt)
            lid = str(lt.get('batchCode') or lt.get('batchNumber') or lt.get('code') or lt.get('lot') or len(produtos.get(pc, {}).get('lotes', [])))
            data = next((_dia(lt.get(k)) for k in CHAVES_DATA if _dia(lt.get(k))), None) or _data_qualquer(lt)
            visto.setdefault(f'{pc}|{lid}', hoje)
            p = produtos.setdefault(pc, dict(saldo=0.0, lotes=[]))
            p['saldo'] += bal
            p['lotes'].append([lid, round(bal, 2), data or visto[f'{pc}|{lid}'], bool(data)])
    for k in [k for k in visto if k.split('|')[0] not in produtos]:
        visto.pop(k)                                         # produto saiu do estoque
    try:
        _flags(cli, list(produtos), dados)                     # acabado? unidade? (so tecido acabado em metros fica no painel)
    except Exception:  # noqa: BLE001
        traceback.print_exc()
    nomes = dados.get('artigos', {})
    faltam = [pc for pc in produtos if pc not in nomes]
    if faltam:
        try:
            from .artigos import artigos_de_produtos
            for pc, (cod, nome) in artigos_de_produtos(cli, faltam).items():
                nomes[pc] = [cod, nome]
        except Exception:  # noqa: BLE001
            traceback.print_exc()
    dados.update(estoque={pc: dict(p, saldo=round(p['saldo'], 2)) for pc, p in produtos.items()}, visto=visto, artigos=nomes,
                 estoque_em=datetime.now().isoformat(timespec='seconds'), amostra_lote=amostra)


def _flags(cli, pcs, dados: dict) -> dict:
    """{productCode: [acabado?, unidade, nome]} do cadastro (so busca o que ainda nao esta no cache)."""
    flags = dados.setdefault('flags', {})
    for lote in _em_lotes([p for p in pcs if str(p) not in flags and str(p).isdigit()], 100):
        achados = {}
        for x in _paginar(cli, PRODUCTS_URL, dict(filter=dict(productCodeList=[int(c) for c in lote]), option=dict(branchInfoCode=EMPRESA)), tam=100):
            achados[str(x.get('productCode'))] = x
        for c in lote:
            x = achados.get(str(c)) or {}
            flags[str(c)] = [bool(x.get('isFinishedProduct')) and not x.get('isRawMaterial'), str(x.get('measuredUnit') or '').strip().upper(), str(x.get('productName') or '')[:120]]
    return flags


def _achar_inspecao(cli, dados: dict):
    """(codigo, descricao) do saldo 'aguardando inspecao': EXPEDICAO_SALDO_INSPECAO ou o estoque cujo nome traz INSPE."""
    if SALDO_INSPECAO.isdigit():
        return int(SALDO_INSPECAO), dados.get('inspecao_desc') or f'saldo {SALDO_INSPECAO}'
    if dados.get('inspecao_cod') is not None:
        return dados['inspecao_cod'], dados.get('inspecao_desc')
    from . import almoxarifado
    est = almoxarifado.descobrir_estoques(cli)
    dados['estoques_vistos'] = {str(k): v for k, v in est.items()}
    cand = [(c, d) for c, d in est.items() if 'INSPE' in d.upper()]
    if not cand:
        raise RuntimeError('Nao achei um saldo com "INSPE" no nome. Saldos vistos: ' + '; '.join(f'{c}={d}' for c, d in sorted(est.items())[:25]) + ' (defina EXPEDICAO_SALDO_INSPECAO no .env)')
    dados.update(inspecao_cod=cand[0][0], inspecao_desc=cand[0][1])
    return cand[0]


def atualizar_inspecao(cli, dados: dict) -> None:
    cod, desc = _achar_inspecao(cli, dados)
    itens = _paginar(cli, BALANCES_URL, dict(filter=dict(hasStock=True, branchStockCode=EMPRESA, stockCode=cod), option=dict(balances=[dict(branchCode=EMPRESA, stockCodeList=[cod])])), tam=1000)
    saldos = {}
    for x in itens:
        for b in x.get('balances') or []:
            if b.get('stockCode') == cod and _num(b.get('stock')) > 0:
                saldos[str(x.get('productCode'))] = round(_num(b.get('stock')), 3)
    flags = _flags(cli, list(saldos), dados)
    kg = {pc: [v, flags.get(pc, [0, '', ''])[2]] for pc, v in saldos.items() if not UNIDADE_INSPECAO or flags.get(pc, [0, '', ''])[1] == UNIDADE_INSPECAO}
    dados.update(inspecao=kg, inspecao_unidade=UNIDADE_INSPECAO, inspecao_total_itens=len(saldos), inspecao_em=datetime.now().isoformat(timespec='seconds'))


# ---------------------------------------------------------------- notas
def _buscar_notas(cli, ini: date, fim: date) -> list:
    saida, a = [], ini
    while a <= fim:
        b = min(fim, a + timedelta(days=api.WINDOW_DAYS - 1))
        pag = 1
        while True:
            r = cli.post(api.INVOICES_URL, dict(filter=dict(branchCodeList=[EMPRESA], startIssueDate=f'{a}T00:00:00', endIssueDate=f'{b}T23:59:59'),
                                                 page=pag, pageSize=100, order='branchCode,invoiceDate,invoiceSequence'))
            if r.status_code == 404:
                break
            if r.status_code != 200:
                raise RuntimeError(f'notas {a}..{b}: HTTP {r.status_code} {r.text[:200]}')
            j = r.json()
            saida += j.get('items') or []
            if not j.get('hasNext'):
                break
            pag += 1
        a = b + timedelta(days=1)
    return saida


def _e_saida(n: dict) -> bool:
    t = str(n.get('operationType') or '').lower()
    return not any(k in t for k in ('input', 'entrada'))


def atualizar_notas(cli, dados: dict, completa: bool) -> None:
    hoje = date.today()
    notas = dados.get('notas', {})
    ini = hoje - timedelta(days=NF_DIAS if (not notas or not dados.get('notas_hist')) else NF_RECENTE_DIAS if completa else 7)      # 1a vez: busca todo o historico
    novos = _buscar_notas(cli, ini, hoje)
    for k in [k for k, v in notas.items() if v[0] >= ini.isoformat()]:
        notas.pop(k)
    for n in novos:
        if str(n.get('invoiceStatus')) == 'Deleted':
            continue                                                   # NF excluida nao e nota de verdade: fica fora
        e = n.get('eletronic') or {}
        notas[f"{n.get('branchCode')}-{n.get('invoiceSequence')}"] = [
            _dia(n.get('issueDate')) or _dia(n.get('invoiceDate')) or '', n.get('invoiceCode'), n.get('serialCode'), str(n.get('invoiceStatus') or ''),
            str(e.get('electronicInvoiceStatus') or ''), n.get('personCode'), str(n.get('personName') or '')[:60], round(_num(n.get('quantity')), 2),
            round(_num(n.get('totalValue')), 2), str(n.get('operatioName') or n.get('operationName') or '')[:50], str(n.get('issueDate') or '')[:19], 'E' if not _e_saida(n) else 'S']      # [11] = S saida / E entrada (devolucao)
    corte = (hoje - timedelta(days=NF_DIAS + 5)).isoformat()
    for k in [k for k, v in notas.items() if v[0] < corte]:
        notas.pop(k)
    dados.update(notas=notas, notas_hist=True, notas_em=datetime.now().isoformat(timespec='seconds'))
    if novos and 'amostra_nf' not in dados:
        dados['amostra_nf'] = {k: v for k, v in novos[0].items() if not isinstance(v, (list, dict))} | dict(eletronic=novos[0].get('eletronic'))


def atualizar(completa: bool = True) -> bool:
    global _ult_completa
    if not _trava.acquire(blocking=False):
        return False
    ESTADO.update(rodando=True, erro=None, erro_notas=None, erro_estoque=None, erro_inspecao=None, msg='Lendo Expedição no TOTVS...')
    try:
        cli = api.TotvsModaClient()
        cli.autenticar()
        dados = ler()
        for nome, fn in (('estoque', lambda: atualizar_estoque(cli, dados)), ('notas', lambda: atualizar_notas(cli, dados, completa)), ('inspecao', lambda: atualizar_inspecao(cli, dados))):
            try:
                fn()
            except Exception as e:  # noqa: BLE001
                traceback.print_exc()
                ESTADO['erro_' + nome] = str(e)[:300]
        _gravar(dados)
        if completa:
            _ult_completa = time.time()
        ESTADO.update(ultima=datetime.now().isoformat(timespec='seconds'), msg='ok')
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        ESTADO.update(erro=str(e)[:300], msg='Falha ao ler a Expedição - mantive os dados anteriores')
    finally:
        ESTADO['rodando'] = False
        _trava.release()
    return ESTADO['erro'] is None


def ler_mov() -> dict:
    try:
        return json.loads(ARQ_MOV.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def _kardex(cli, pc: str, hoje: date):
    """[ultima mov, ultima entrada, ultima saida] (ISO) do produto no saldo da Expedicao nos ultimos 180 dias; None se nao houve movimento."""
    for tentativa in range(2):
        ini, fim = hoje - timedelta(days=MOV_DIAS), hoje
        f = (lambda d: d.isoformat()) if _fmt_data['so_data'] else (lambda d, h='00:00:00': f'{d.isoformat()}T{h}')
        r = cli.get(KARDEX_URL, dict(ProductCode=pc, BranchCode=EMPRESA, StartDate=f(ini), EndDate=f(fim) if _fmt_data['so_data'] else f'{fim.isoformat()}T23:59:59', BalanceType=SALDO))
        if r.status_code == 400 and 'MovementDataNotFound' in r.text and not tentativa:
            _fmt_data['so_data'] = not _fmt_data['so_data']           # pode ser o formato da data: tenta o outro uma vez
            continue
        break
    if r.status_code == 400 and 'MovementDataNotFound' in r.text:
        return None
    if r.status_code != 200:
        raise RuntimeError(f'kardex {pc}: HTTP {r.status_code} {r.text[:120]}')
    j = r.json()
    movs = (j[0] if isinstance(j, list) and j else j if isinstance(j, dict) else {}).get('movements') or []
    ult = ent = sai = prim = None
    for m in movs:
        d = str(m.get('movementDate') or '')[:10]
        if not d:
            continue
        ult = max(ult or d, d)
        if abs(_num(m.get('inQuantity'))) > 0:
            ent = max(ent or d, d)
            prim = min(prim or d, d)
        if abs(_num(m.get('outQuantity'))) > 0:
            sai = max(sai or d, d)
    return [ult, ent, sai, prim] if ult else None


def atualizar_mov() -> bool:
    """Kardex de cada produto com saldo na Expedicao (so leitura, 3 consultas ao mesmo tempo): base do 'tempo parado'."""
    if not _trava_mov.acquire(blocking=False):
        return False
    try:
        from concurrent.futures import ThreadPoolExecutor
        base = ler()
        flags = base.get('flags') or {}
        produtos = [pc for pc in (base.get('estoque') or {}) if not flags.get(pc) or (flags[pc][0] and flags[pc][1] in ('MT', 'M'))]      # so tecido acabado
        if not produtos:
            return False
        cli = api.TotvsModaClient()
        cli.autenticar()
        hoje, res, erros = date.today(), {}, []

        def um(pc):
            try:
                return pc, _kardex(cli, pc, hoje), None
            except Exception as e:  # noqa: BLE001
                return pc, None, str(e)[:160]
        with ThreadPoolExecutor(max_workers=3) as ex:
            for pc, v, err in ex.map(um, produtos):
                if err:
                    erros.append(err)
                else:
                    res[pc] = v                                  # None = sem movimento em 180 dias
        if res:
            ARQ_MOV.parent.mkdir(parents=True, exist_ok=True)
            tmp = ARQ_MOV.with_suffix('.tmp')
            tmp.write_text(json.dumps(dict(atualizado=datetime.now().isoformat(timespec='seconds'), produtos=res, erros=len(erros), amostra_erro=erros[:2]), ensure_ascii=False), encoding='utf-8')
            api.trocar_arquivo(tmp, ARQ_MOV)
        ESTADO['erro_mov'] = erros[0] if erros and not res else None
        return bool(res)
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        ESTADO['erro_mov'] = str(e)[:300]
        return False
    finally:
        _trava_mov.release()


def iniciar_agendador(app=None) -> None:
    from .pedidos import configurado
    if not configurado():
        return

    def loop():
        time.sleep(50)
        while True:
            try:
                atualizar(completa=time.time() - _ult_completa > COMPLETA_MIN * 60)
            except Exception:  # noqa: BLE001
                traceback.print_exc()
            time.sleep(INTERVALO_SEG)
    def loop_mov():
        time.sleep(120)
        while True:
            try:
                atualizar_mov()
            except Exception:  # noqa: BLE001
                traceback.print_exc()
            time.sleep(MOV_INTERVALO_MIN * 60)
    threading.Thread(target=loop, daemon=True, name='totvs-expedicao').start()
    threading.Thread(target=loop_mov, daemon=True, name='totvs-expedicao-mov').start()


# ---------------------------------------------------------------- dashboard
FAIXAS = [(30, 'até 30 dias'), (60, '31 a 60 dias'), (90, '61 a 90 dias'), (180, '91 a 180 dias'), (10 ** 6, 'mais de 180 dias')]


def _situacao(n: list) -> str:
    """Situacao unica da NF: cancelada/denegada/excluida pelo status da nota; senao pelo status da NF-e (autorizada = ok)."""
    st = STATUS_NF.get(n[3], n[3] or '–')
    if st != 'Emitida':
        return st
    nfe = n[4]
    if not nfe or nfe == 'Authorized':
        return 'Autorizada'
    return STATUS_NFE.get(nfe, nfe)


def _pct(atual: float, ant: float):
    return round((atual - ant) / ant * 100) if ant else None


MESES = ['janeiro','fevereiro','março','abril','maio','junho','julho','agosto','setembro','outubro','novembro','dezembro']


def painel(dias: int = 0) -> dict:
    """Periodo automatico: do dia 1 do mes ate hoje, comparado com o mesmo intervalo do mes anterior."""
    d = ler()
    mov = ler_mov().get('produtos') or {}
    hoje = date.today()
    dias = hoje.day
    ini = hoje.replace(day=1).isoformat()
    fim_mes_ant = hoje.replace(day=1) - timedelta(days=1)
    ini_ant = fim_mes_ant.replace(day=1).isoformat()
    fim_ant = fim_mes_ant.replace(day=min(hoje.day, fim_mes_ant.day)).isoformat()
    periodo = f'1 a {hoje.day} de {MESES[hoje.month - 1]}'

    todas = [n for n in (d.get('notas') or {}).values() if (len(n) < 12 or n[11] != 'E') and n[3] != 'Deleted']          # o painel conta so saidas; as entradas aparecem na consulta de NF
    notas = [n for n in todas if n[0] >= ini]
    anteriores = [n for n in todas if ini_ant <= n[0] <= fim_ant and _situacao(n) == 'Autorizada']
    por_sit, por_dia, por_cli = {}, {}, {}
    for n in notas:
        s = _situacao(n)
        c = por_sit.setdefault(s, dict(situacao=s, qtd=0, valor=0.0, metros=0.0))
        c['qtd'] += 1
        c['valor'] += n[8]
        c['metros'] += n[7]
        if s == 'Autorizada':
            x = por_dia.setdefault(n[0], dict(dia=n[0], qtd=0, valor=0.0, metros=0.0))
            x['qtd'] += 1
            x['valor'] += n[8]
            x['metros'] += n[7]
            y = por_cli.setdefault(n[6] or '–', dict(cliente=n[6] or '–', qtd=0, valor=0.0, metros=0.0))
            y['qtd'] += 1
            y['valor'] += n[8]
            y['metros'] += n[7]
    ok = por_sit.get('Autorizada', dict(qtd=0, valor=0, metros=0))
    problemas = sorted((n for n in notas if _situacao(n) not in ('Autorizada', 'Cancelada', 'Excluída', 'Inutilizada')), key=lambda n: n[10], reverse=True)
    ultimas = sorted(notas, key=lambda n: (n[10], n[1] or 0), reverse=True)[:10]
    fmt = lambda n: dict(dia=n[0], nf=n[1], serie=n[2], situacao=_situacao(n), cliente=n[6], metros=n[7], valor=n[8], operacao=n[9], hora=n[10][11:16])
    serie = []
    for i in range(dias):                                     # todos os dias do periodo (dia sem NF = 0), para o grafico nao ter buracos
        dd = (hoje - timedelta(days=dias - 1 - i)).isoformat()
        serie.append(por_dia.get(dd) or dict(dia=dd, qtd=0, valor=0.0, metros=0.0))
    # estoque
    est, art, flags = d.get('estoque') or {}, d.get('artigos') or {}, d.get('flags') or {}

    def vale(pc):                       # so tecido ACABADO em metros; 2a qualidade e sucata nao entram na expedicao
        f = flags.get(pc)
        if f and (not f[0] or f[1] not in ('MT', 'M')):
            return False
        nome = (str(art.get(pc, ['', ''])[0]) + ' ' + str(art.get(pc, ['', ''])[1]) + ' ' + (f[2] if f else '')).upper()
        return 'SEGUNDA' not in nome and 'SUCATA' not in nome
    est_raw = est
    est = {pc: p for pc, p in est.items() if vale(pc)}
    tem_mov = bool(mov)
    por_art = {}
    for pc, p in est.items():
        cod, nome = art.get(pc, [pc, ''])
        a = por_art.setdefault(cod, dict(cod=cod, nome=nome, saldo=0.0, lotes=0, dias=None, ult_entrada=None, ult_mov=None, segunda=False))
        a['saldo'] += p['saldo']
        a['lotes'] += len(p['lotes'])
        if pc in mov:
            m = mov[pc]
            dias_pc = (hoje - date.fromisoformat(m[0])).days if m else 181          # sem movimento em 180 dias: "mais de 180"
            a['dias'] = dias_pc if a['dias'] is None else min(a['dias'], dias_pc)
            if m and m[1]:
                a['ult_entrada'] = max(a['ult_entrada'] or m[1], m[1])
            if m and m[0]:
                a['ult_mov'] = max(a['ult_mov'] or m[0], m[0])
    arts = sorted(por_art.values(), key=lambda a: a['saldo'], reverse=True)
    total = sum(a['saldo'] for a in arts)
    datados = [a for a in arts if a['dias'] is not None]
    faixas = [dict(faixa=r, artigos=0, metros=0.0, pecas=0, lista=[]) for _, r in FAIXAS]
    for a in sorted(datados, key=lambda a: -a['saldo']):
        for i, (lim, _r) in enumerate(FAIXAS):
            if a['dias'] <= lim:
                faixas[i]['artigos'] += 1
                faixas[i]['metros'] += a['saldo']
                faixas[i]['pecas'] += a['lotes']
                faixas[i]['lista'].append(dict(a, saldo=round(a['saldo'], 2)))
                break
    m30 = sum(n[7] for n in todas if n[0] >= (hoje - timedelta(days=29)).isoformat() and _situacao(n) == 'Autorizada')
    parados = sorted(datados, key=lambda a: (a['dias'], a['saldo']), reverse=True)
    r2 = lambda v: round(v, 2)
    ins = (d.get('inspecao') or {}) if d.get('inspecao_unidade') == UNIDADE_INSPECAO else {}      # lista guardada com outra unidade nao vale
    insp = dict(cod=d.get('inspecao_cod'), nome=d.get('inspecao_desc'), unidade=UNIDADE_INSPECAO, total=r2(sum(v[0] for v in ins.values())), itens=len(ins), outros=max(0, (d.get('inspecao_total_itens') or 0) - len(ins)),
                em=d.get('inspecao_em'), lista=[dict(cod=pc, nome=v[1], saldo=v[0], exp_m=r2((est_raw.get(pc) or {}).get('saldo', 0)), exp_pecas=len((est_raw.get(pc) or {}).get('lotes', []))) for pc, v in sorted(ins.items(), key=lambda kv: -kv[1][0])])
    p90 = sum(a['saldo'] for a in datados if a['dias'] > 90)
    return dict(
        ok=True, dias=dias, periodo=periodo, mes=MESES[hoje.month - 1], hoje=hoje.isoformat(), notas_em=d.get('notas_em'), estoque_em=d.get('estoque_em'), mov_em=ler_mov().get('atualizado'),
        atualizado=max([x for x in (d.get('notas_em'), d.get('estoque_em')) if x] or [None]) if (d.get('notas_em') or d.get('estoque_em')) else None,
        fonte=dict(ultima=ESTADO['ultima'], erro=ESTADO['erro'], erro_notas=ESTADO['erro_notas'], erro_estoque=ESTADO['erro_estoque'], erro_mov=ESTADO['erro_mov'], erro_inspecao=ESTADO['erro_inspecao'], rodando=ESTADO['rodando']),
        notas=dict(total=len(notas), autorizadas=ok['qtd'], metros_hoje=r2(sum(n[7] for n in notas if n[0] == hoje.isoformat() and _situacao(n) == 'Autorizada')), valor=r2(ok['valor']), metros=r2(ok['metros']), hoje=sum(1 for n in notas if n[0] == hoje.isoformat() and _situacao(n) == 'Autorizada'),
                   pendentes=len(problemas), ticket=r2(ok['valor'] / ok['qtd']) if ok['qtd'] else 0,
                   var=dict(valor=_pct(ok['valor'], sum(n[8] for n in anteriores)), metros=_pct(ok['metros'], sum(n[7] for n in anteriores)), qtd=_pct(ok['qtd'], len(anteriores))) if anteriores is not None else None,
                   por_situacao=sorted((dict(v, valor=r2(v['valor']), metros=r2(v['metros'])) for v in por_sit.values()), key=lambda v: -v['qtd']),
                   por_dia=[dict(x, valor=r2(x['valor']), metros=r2(x['metros'])) for x in serie],
                   clientes=[dict(x, valor=r2(x['valor']), metros=r2(x['metros'])) for x in sorted(por_cli.values(), key=lambda x: -x['valor'])[:6]],
                   ultimas=[fmt(n) for n in ultimas], problemas=[fmt(n) for n in problemas[:60]]),
        estoque=dict(total=r2(total), artigos=len(arts), lotes=sum(a['lotes'] for a in arts),
                     top5_pct=round(sum(a['saldo'] for a in arts[:5]) / total * 100) if total else 0, top10_pct=round(sum(a['saldo'] for a in arts[:10]) / total * 100) if total else 0,
                     saida_30d=r2(m30), cobertura_dias=round(total / (m30 / 30)) if m30 else None,
                     parado_90=r2(p90), parado_90_pct=round(p90 / total * 100) if total else 0, parado_90_artigos=sum(1 for a in datados if a['dias'] > 90),
                     idade_pronta=tem_mov, idade_cobertura=round(len(datados) / len(arts) * 100) if arts else 0,
                     parados90=[dict(a, saldo=r2(a['saldo'])) for a in parados if a['dias'] > 90],
                     idade_media=round(sum(a['dias'] * a['saldo'] for a in datados) / sum(a['saldo'] for a in datados)) if datados and sum(a['saldo'] for a in datados) else None,
                     mov_30_pct=round(sum(a['saldo'] for a in datados if a['dias'] <= 30) / sum(a['saldo'] for a in datados) * 100) if datados and sum(a['saldo'] for a in datados) else None,
                     pecas_90=sum(a['lotes'] for a in datados if a['dias'] > 90),
                     inspecao=insp,
                     maiores=[dict(a, saldo=r2(a['saldo']), pct=round(a['saldo'] / total * 100, 1) if total else 0) for a in arts[:8]],
                     parados=[dict(a, saldo=r2(a['saldo'])) for a in parados[:8]],
                     faixas=[dict(f, metros=r2(f['metros'])) for f in faixas]))


# ---------------------------------------------------------------- pedidos x expedicao
def pedidos_x_expedicao() -> dict:
    """Cruza os pedidos em aberto (Relatorio Pedidos / 38) com o que ja esta na Expedicao: quanto tempo o pedido levou para o artigo chegar
    e quanto tempo ele ja esta armazenado. O saldo de cada artigo e distribuido entre os pedidos abertos, do mais antigo para o mais novo."""
    from app.models import PedidoRelatorio
    d, mov = ler(), ler_mov().get('produtos') or {}
    hoje = date.today()
    est, art, flags = d.get('estoque') or {}, d.get('artigos') or {}, d.get('flags') or {}

    def vale(pc):
        f = flags.get(pc)
        if f and (not f[0] or f[1] not in ('MT', 'M')):
            return False
        nome = (str(art.get(pc, ['', ''])[0]) + ' ' + str(art.get(pc, ['', ''])[1]) + ' ' + (f[2] if f else '')).upper()
        return 'SEGUNDA' not in nome and 'SUCATA' not in nome
    est = {pc: p for pc, p in est.items() if vale(pc)}
    abertos, vistos = {}, set()
    for r in PedidoRelatorio.query.filter(db.or_(PedidoRelatorio.region == 'BR', PedidoRelatorio.region.is_(None))).all():
        if (r.tp_situacao or '') in ('ATENDIDO', 'CANCELADO') or not (r.qt_pendente or 0) > 0 or not r.dt_pedido:
            continue
        k = (r.cod_pedido, r.cod_produto)
        if k in vistos:
            continue                                        # o relatorio tem uma linha por OP: conta o pedido uma vez
        vistos.add(k)
        abertos.setdefault(str(r.cod_produto), []).append(r)
    itens, artigos = [], []
    for pc, p in est.items():
        cod, nome = art.get(pc, [pc, ''])
        m = mov.get(pc)
        ult_ent = m[1] if m else None
        prim_ent = (m[3] if m and len(m) > 3 else None) or ult_ent
        ult_mov = m[0] if m else None
        if pc in mov:
            dias_exp = (hoje - date.fromisoformat(ult_ent)).days if ult_ent else 181          # sem entrada nos ultimos 180 dias
        else:
            dias_exp = None
        peds = sorted(abertos.get(pc, []), key=lambda r: (r.dt_pedido, r.cod_pedido or ''))
        saldo = p['saldo']
        a = dict(cod=cod, nome=nome, saldo=round(saldo, 2), pecas=len(p['lotes']), dias_exp=dias_exp, ult_entrada=ult_ent, ult_mov=ult_mov, pedidos=len(peds),
                 pendente=round(sum(r.qt_pendente or 0 for r in peds), 2), pedido_mais_antigo=peds[0].dt_pedido.isoformat() if peds else None,
                 idade_pedido=(hoje - peds[0].dt_pedido).days if peds else None,
                 lead=((date.fromisoformat(ult_ent) - peds[0].dt_pedido).days if ult_ent and peds else None), valor=0.0)
        artigos.append(a)
        resto = saldo
        n_antes = len(itens)
        for r in peds:
            pronto = min(r.qt_pendente or 0, max(resto, 0))
            resto -= pronto
            vu = r.vl_unitario or 0
            itens.append(dict(vl_unit=round(vu, 4), valor_pendente=round((r.qt_pendente or 0) * vu, 2), valor=round(pronto * vu, 2), cliente=r.nome_cliente or '', unidade=r.unidade or '', pedido=r.cod_pedido, cod=cod, nome=nome, dt_pedido=r.dt_pedido.isoformat(),
                              idade_pedido=(hoje - r.dt_pedido).days, pendente=round(r.qt_pendente or 0, 2), pronto=round(pronto, 2), ult_entrada=ult_ent, dias_exp=dias_exp,
                              lead=((date.fromisoformat(ult_ent) - r.dt_pedido).days if ult_ent else None), situacao=r.tp_situacao or ''))
    for a in artigos:
        a['valor'] = round(sum(i['valor'] for i in itens if i['cod'] == a['cod']), 2) if a['pedidos'] else 0.0
    prontos = sorted((i for i in itens if i['pronto'] > 0), key=lambda i: (-(i['dias_exp'] or 0), -i['pronto']))
    sem_pedido = sorted((a for a in artigos if not a['pedidos']), key=lambda a: -a['saldo'])
    com_pedido = [a for a in artigos if a['pedidos']]
    leads = [a['lead'] for a in com_pedido if a['lead'] is not None and a['lead'] >= 0]
    exps = [a['dias_exp'] for a in artigos if a['dias_exp'] is not None]
    faixas_lead = [('até 15 dias', 15), ('16 a 30 dias', 30), ('31 a 45 dias', 45), ('46 a 60 dias', 60), ('mais de 60 dias', 10 ** 6)]
    hist = [dict(faixa=n, artigos=0, metros=0.0, lista=[]) for n, _ in faixas_lead]
    for a in com_pedido:
        if a['lead'] is None or a['lead'] < 0:
            continue
        for i, (_n, lim) in enumerate(faixas_lead):
            if a['lead'] <= lim:
                hist[i]['artigos'] += 1
                hist[i]['metros'] += a['saldo']
                hist[i]['lista'].append(dict(cod=a['cod'], nome=a['nome'], saldo=a['saldo'], pecas=a['pecas'], idade_pedido=a['idade_pedido'], dias_exp=a['dias_exp'], lead=a['lead'], pedidos=a['pedidos'], valor=a['valor']))
                break
    por_cli = {}
    for i in prontos:
        c = por_cli.setdefault(i['cliente'] or '–', dict(cliente=i['cliente'] or '–', metros=0.0, valor=0.0, pedidos=0, dias=0, fv=[0.0] * 4, fm=[0.0] * 4))
        c['metros'] += i['pronto']
        c['valor'] += i['valor']
        fx = 0 if (i['dias_exp'] or 0) <= 30 else 1 if (i['dias_exp'] or 0) <= 60 else 2 if (i['dias_exp'] or 0) <= 90 else 3
        c['fv'][fx] += i['valor']
        c['fm'][fx] += i['pronto']
        c['pedidos'] += 1
        c['dias'] = max(c['dias'], i['dias_exp'] or 0)
    r2 = lambda v: round(v, 2)
    faixas_v = [('até 30 dias', 30), ('31 a 60 dias', 60), ('61 a 90 dias', 90), ('mais de 90 dias', 10 ** 6)]
    fv = [dict(faixa=n, valor=0.0, metros=0.0, pedidos=0) for n, _ in faixas_v]
    for i in prontos:
        for k, (_n, lim) in enumerate(faixas_v):
            if (i['dias_exp'] or 0) <= lim:
                fv[k]['valor'] += i['valor']
                fv[k]['metros'] += i['pronto']
                fv[k]['pedidos'] += 1
                break
    return dict(
        ok=True, atualizado=d.get('estoque_em'), mov_pronto=bool(mov), pedidos_em=None, tem_preco=any(i['vl_unit'] for i in itens),
        faixas_valor=[dict(f, valor=r2(f['valor']), metros=r2(f['metros'])) for f in fv],
        kpis=dict(prontos_valor=r2(sum(i['valor'] for i in prontos)), prontos_90_valor=r2(sum(i['valor'] for i in prontos if (i['dias_exp'] or 0) > 90)), prontos_metros=r2(sum(i['pronto'] for i in prontos)), prontos_pedidos=len(prontos), prontos_clientes=len(por_cli),
                  lead_medio=round(sum(leads) / len(leads)) if leads else None, armazenado_medio=round(sum(exps) / len(exps)) if exps else None,
                  prontos_90=r2(sum(i['pronto'] for i in prontos if (i['dias_exp'] or 0) > 90)),
                  sem_pedido_metros=r2(sum(a['saldo'] for a in sem_pedido)), sem_pedido_artigos=len(sem_pedido), artigos=len(artigos), artigos_com_pedido=len(com_pedido)),
        hist=[dict(h, metros=r2(h['metros'])) for h in hist],
        pontos=[dict(cod=a['cod'], nome=a['nome'], x=a['idade_pedido'], y=a['dias_exp'], saldo=a['saldo'], pedidos=a['pedidos'], lead=a['lead'], valor=a['valor']) for a in com_pedido if a['dias_exp'] is not None],
        prontos=prontos[:600], sem_pedido=sem_pedido[:400],
        clientes=[dict(c, metros=r2(c['metros']), valor=r2(c['valor']), fv=[r2(v) for v in c['fv']], fm=[r2(v) for v in c['fm']]) for c in sorted(por_cli.values(), key=lambda c: -(c['valor'] or c['metros']))[:10]])


# ---------------------------------------------------------------- consulta de NF
def _sem_acento(t: str) -> str:
    import unicodedata
    return ''.join(c for c in unicodedata.normalize('NFD', str(t or '')) if unicodedata.category(c) != 'Mn').upper()


def buscar_nfs(q: str, tipo: str = 'todas', de: str = '', ate: str = '', sit: str = '') -> tuple:
    """NFs guardadas (ate 12 meses, saidas e entradas) por numero da NF ou nome do cliente, com filtros de tipo e periodo; sem busca devolve as mais recentes."""
    q = (q or '').strip()
    notas = ler().get('notas') or {}
    achadas, cont = [], {}
    for chave, n in notas.items():
        if n[3] == 'Deleted':
            continue
        t = n[11] if len(n) > 11 else 'S'
        if tipo == 'saida' and t == 'E' or tipo == 'entrada' and t != 'E':
            continue
        if (de and n[0] < de) or (ate and n[0] > ate):
            continue
        if q and not (q.isdigit() and str(n[1] or '').lstrip('0').endswith(q.lstrip('0') or q)) and _sem_acento(q) not in _sem_acento(n[6]):
            continue
        sn = _situacao(n)
        cont[sn] = cont.get(sn, 0) + 1
        if sit and sn != sit:
            continue
        achadas.append(dict(seq=chave.split('-', 1)[1], dia=n[0], nf=n[1], serie=n[2], situacao=sn, cliente=n[6], metros=n[7], valor=n[8], operacao=n[9], hora=n[10][11:16], data=n[0], tipo='Entrada' if t == 'E' else 'Saída'))
    achadas.sort(key=lambda x: (x['dia'], x['hora'], x['nf'] or 0), reverse=True)
    return achadas[:80], dict(sorted(cont.items(), key=lambda kv: -kv[1])), len(achadas)


def _g(v):
    return v if v not in (None, '') else None


def detalhe_nf(seq: str, data: str) -> dict:
    """Todos os dados da NF direto do TOTVS (cabecalho, cliente, totais, itens, pagamentos, pedidos)."""
    cli = api.TotvsModaClient()
    cli.autenticar()
    d0 = date.fromisoformat(data)
    nota, pag = None, 1
    while nota is None:
        r = cli.post(api.INVOICES_URL, dict(filter=dict(branchCodeList=[EMPRESA], startIssueDate=f'{d0}T00:00:00', endIssueDate=f'{d0}T23:59:59'), expand=api.EXPAND, page=pag, pageSize=100))
        if r.status_code != 200:
            raise RuntimeError(f'nota {seq}: HTTP {r.status_code} {r.text[:160]}')
        j = r.json()
        nota = next((n for n in j.get('items') or [] if str(n.get('invoiceSequence')) == str(seq)), None)
        if not j.get('hasNext'):
            break
        pag += 1
    if not nota:
        raise RuntimeError('NF não encontrada no TOTVS.')
    p, e = nota.get('person') or {}, nota.get('eletronic') or {}
    st = STATUS_NF.get(str(nota.get('invoiceStatus')), nota.get('invoiceStatus'))
    nfe = STATUS_NFE.get(str(e.get('electronicInvoiceStatus')), e.get('electronicInvoiceStatus'))
    itens = [dict(seq=i.get('sequence'), cod=i.get('code'), nome=i.get('name'), ncm=i.get('ncm'), cfop=i.get('cfop'), un=i.get('measureUnit'), qtd=i.get('quantity'),
                  unit=i.get('unitNetValue'), bruto=i.get('grossValue'), desc=i.get('discountValue'), liquido=i.get('netValue')) for i in nota.get('items') or []]
    pagtos = []
    for x in nota.get('payments') or []:
        pagtos.append(dict(venc=str(x.get('dueDate') or x.get('expirationDate') or '')[:10], valor=x.get('value') or x.get('installmentValue') or x.get('totalValue') or x.get('amount'),
                           forma=x.get('paymentType') or x.get('documentType') or x.get('description') or '', parcela=x.get('installment') or x.get('sequence') or ''))
    return dict(
        nf=nota.get('invoiceCode'), serie=nota.get('serialCode'), modelo=nota.get('documentType'), sequencia=nota.get('invoiceSequence'), empresa=nota.get('branchCode'), cnpj_empresa=nota.get('branchCnpj'),
        situacao=st, nfe=nfe, chave=e.get('accessKey'), protocolo=e.get('receiptNumber') or e.get('protocol'), emissao=str(nota.get('issueDate') or '')[:19], data_fatura=str(nota.get('invoiceDate') or '')[:10],
        operacao=nota.get('operatioName') or nota.get('operationName'), tipo=nota.get('operationType'), cod_operacao=nota.get('operationCode'),
        cliente=dict(codigo=nota.get('personCode'), nome=nota.get('personName'), cnpj=p.get('personCpfCnpj'), cidade=p.get('city'), uf=p.get('stateAbbreviation')),
        pagamento=nota.get('paymentConditionName'), pedidos=[str(x.get('orderCode')) for x in nota.get('salesOrder') or [] if x.get('orderCode')],
        totais=dict(quantidade=nota.get('quantity'), produtos=nota.get('productValue'), desconto_pct=nota.get('discountPercentage'), frete=nota.get('shippingValue'), seguro=nota.get('insuranceValue'),
                    despesas=nota.get('additionalValue'), ipi=nota.get('ipiValue'), icms=nota.get('icmsValue'), icms_st=nota.get('icmsSubStValue'), total=nota.get('totalValue')),
        itens=itens, pagamentos=pagtos)


def nf_html(n: dict) -> str:
    """Pagina A4 da NF (para o PDF)."""
    from html import escape as h

    def num(v, d=2):
        try:
            return f'{float(v):,.{d}f}'.replace(',', 'X').replace('.', ',').replace('X', '.')
        except (TypeError, ValueError):
            return h(str(v)) if v not in (None, '') else '–'

    def brl(v):
        return 'R$ ' + num(v) if v not in (None, '') else '–'
    c, t = n['cliente'], n['totais']
    linhas = ''.join(f"<tr><td>{h(str(i['seq'] or ''))}</td><td>{h(str(i['cod'] or ''))}</td><td>{h(str(i['nome'] or ''))}</td><td>{h(str(i['ncm'] or ''))}</td><td>{h(str(i['cfop'] or ''))}</td><td class=r>{num(i['qtd'], 2)} {h(str(i['un'] or ''))}</td><td class=r>{brl(i['unit'])}</td><td class=r>{brl(i['liquido'])}</td></tr>" for i in n['itens'])
    pag = ''.join(f"<tr><td>{h(str(x['parcela']))}</td><td>{h(x['venc'])}</td><td>{h(str(x['forma']))}</td><td class=r>{brl(x['valor'])}</td></tr>" for x in n['pagamentos'])
    emi = n['emissao'].replace('T', ' ')
    return f"""<!doctype html><html lang=pt-BR><head><meta charset=utf-8><title>NF {h(str(n['nf']))}</title><style>
@page{{size:A4;margin:14mm}}body{{font-family:Arial,Helvetica,sans-serif;color:#16283c;font-size:11px}}h1{{font-size:20px;margin:0}}.sub{{color:#64748b;margin:2px 0 14px}}
.box{{border:1px solid #d5dfec;border-radius:8px;padding:10px 12px;margin-bottom:10px}}.box h2{{font-size:11px;text-transform:uppercase;letter-spacing:.08em;color:#64748b;margin:0 0 6px}}
.g{{display:grid;grid-template-columns:repeat(3,1fr);gap:6px 14px}}.g b{{display:block;font-size:9px;text-transform:uppercase;color:#64748b}}table{{width:100%;border-collapse:collapse}}th{{text-align:left;font-size:9px;text-transform:uppercase;color:#64748b;border-bottom:1px solid #cbd5e1;padding:4px}}td{{padding:4px;border-bottom:1px solid #eef2f7}}.r{{text-align:right}}
.tag{{display:inline-block;background:#0f2a4a;color:#fff;border-radius:999px;padding:2px 10px;font-size:10px;font-weight:bold}}.key{{font-family:monospace;font-size:10px;word-break:break-all}}</style></head><body>
<h1>Nota Fiscal {h(str(n['nf']))} <span class=tag>{h(str(n['situacao']))}</span></h1><div class=sub>Série {h(str(n['serie'] or '–'))} · emitida em {h(emi)} · NF-e: {h(str(n['nfe'] or '–'))} · consulta feita no Gestão Conexão a partir do TOTVS</div>
<div class=box><h2>Cliente</h2><div class=g><div><b>Nome</b>{h(str(c['nome'] or ''))}</div><div><b>CNPJ/CPF</b>{h(str(c['cnpj'] or '–'))}</div><div><b>Cidade / UF</b>{h(str(c['cidade'] or '–'))} / {h(str(c['uf'] or '–'))}</div></div></div>
<div class=box><h2>Dados da nota</h2><div class=g><div><b>Operação</b>{h(str(n['operacao'] or '–'))}</div><div><b>Pedido(s)</b>{h(', '.join(n['pedidos']) or '–')}</div><div><b>Condição de pagamento</b>{h(str(n['pagamento'] or '–'))}</div><div><b>Empresa</b>{h(str(n['empresa'] or ''))} · {h(str(n['cnpj_empresa'] or ''))}</div><div><b>Modelo</b>{h(str(n['modelo'] or '–'))}</div><div><b>Quantidade total</b>{num(t['quantidade'])}</div></div>
<div style="margin-top:6px"><b style="font-size:9px;color:#64748b;text-transform:uppercase">Chave de acesso</b><div class=key>{h(str(n['chave'] or '–'))}</div></div></div>
<div class=box><h2>Itens</h2><table><tr><th>#</th><th>Código</th><th>Produto</th><th>NCM</th><th>CFOP</th><th class=r>Quantidade</th><th class=r>Unitário</th><th class=r>Líquido</th></tr>{linhas}</table></div>
<div class=box><h2>Totais</h2><div class=g><div><b>Produtos</b>{brl(t['produtos'])}</div><div><b>Frete</b>{brl(t['frete'])}</div><div><b>Seguro</b>{brl(t['seguro'])}</div><div><b>IPI</b>{brl(t['ipi'])}</div><div><b>ICMS</b>{brl(t['icms'])}</div><div><b>ICMS ST</b>{brl(t['icms_st'])}</div></div>
<div style="font-size:16px;font-weight:bold;margin-top:8px">Total da NF: {brl(t['total'])}</div></div>
{('<div class=box><h2>Pagamentos</h2><table><tr><th>Parcela</th><th>Vencimento</th><th>Forma</th><th class=r>Valor</th></tr>' + pag + '</table></div>') if pag else ''}
</body></html>"""


def nf_pdf(n: dict) -> bytes:
    """PDF da NF pelo Chromium (o mesmo do Relatorio de faturamento); sem ele, levanta erro para o chamador oferecer a pagina de impressao."""
    from playwright.sync_api import sync_playwright
    html = nf_html(n)
    with sync_playwright() as pw:
        try:
            br = pw.chromium.launch()
        except Exception:  # noqa: BLE001
            br = pw.chromium.launch(executable_path=os.getenv('PLAYWRIGHT_CHROMIUM') or None)
        try:
            pg = br.new_page()
            pg.set_content(html)
            return pg.pdf(prefer_css_page_size=True, print_background=True)
        finally:
            br.close()


# ---------------------------------------------------------------- DANFE (PDF oficial) do TOTVS
FISCAL_SWAGGER = f'{BASE}/api/totvsmoda/fiscal/v2/swagger/v1/swagger.json'


def _acha_pdf(obj):
    """Procura em qualquer lugar da resposta um PDF em base64 (comeca com JVBER = '%PDF')."""
    import base64
    if isinstance(obj, str):
        t = obj.strip()
        if t.startswith('JVBER') or t.startswith('data:application/pdf;base64,'):
            try:
                return base64.b64decode(t.split(',', 1)[-1])
            except Exception:  # noqa: BLE001
                return None
    elif isinstance(obj, dict):
        for v in obj.values():
            r = _acha_pdf(v)
            if r:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = _acha_pdf(v)
            if r:
                return r
    return None


def _acha_xml(obj):
    if isinstance(obj, str):
        t = obj.strip()
        if t.startswith('<') and ('nfeProc' in t or 'NFe' in t or 'infNFe' in t):
            return t
    elif isinstance(obj, dict):
        for v in obj.values():
            r = _acha_xml(v)
            if r:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = _acha_xml(v)
            if r:
                return r
    return None


def _danfe_descoberta(chave: str, nota: dict = None) -> tuple:
    """(pdf em bytes ou None, log). Descobre pelo swagger do servico fiscal os endpoints de DANFE/XML e tenta obter o PDF oficial da NF (so leitura)."""
    log = []
    cli = api.TotvsModaClient()
    cli.autenticar()
    paths = {}
    try:
        r = cli.get(FISCAL_SWAGGER, {})
        if r.status_code == 200:
            paths = r.json().get('paths') or {}
            schemas = (r.json().get('components') or {}).get('schemas') or {}
        else:
            schemas = {}
            log.append(f'swagger fiscal: HTTP {r.status_code}')
    except Exception as e:  # noqa: BLE001
        schemas = {}
        log.append(f'swagger fiscal: {e}')

    def resolve(n):
        return schemas.get(((n or {}).get('$ref') or '').split('/')[-1], n or {})

    def props_corpo(op):
        c = resolve((((op.get('requestBody') or {}).get('content') or {}).get('application/json') or {}).get('schema'))
        return c.get('properties') or {}, resolve
    cand = sorted((p for p in paths if 'danfe' in p.lower() or 'xml' in p.lower() or 'pdf' in p.lower()), key=lambda p: (0 if 'xml' in p.lower() and 'danfe' not in p.lower() else 1, p))
    log.append('endpoints candidatos: ' + ', '.join(cand) if cand else 'nenhum endpoint de DANFE/XML no swagger fiscal')
    xml = None
    for path in cand:
        for metodo, op in (paths[path] or {}).items():
            if metodo not in ('get', 'post'):
                continue
            url = BASE + path
            params, corpo = {}, None
            for prm in op.get('parameters') or []:
                nome = str(prm.get('name') or '')
                v = chave if any(k in nome.lower() for k in ('accesskey', 'chave', 'key')) else (EMPRESA if 'branch' in nome.lower() else None)
                if v is None:
                    continue
                if prm.get('in') == 'path':
                    url = url.replace('{' + nome + '}', str(v))
                else:
                    params[nome] = v
            if metodo == 'post':
                corpo = {}
                props, _ = props_corpo(op)

                def preencher(pr, acc, nivel=0):
                    for nome, defn in pr.items():
                        d = resolve(defn)
                        nl = nome.lower()
                        if d.get('type') == 'object' and nivel < 2:
                            sub = {}
                            preencher(d.get('properties') or {}, sub, nivel + 1)
                            acc[nome] = sub
                        elif any(k in nl for k in ('accesskey', 'chave')):
                            acc[nome] = [chave] if d.get('type') == 'array' else chave
                        elif 'xml' in nl and xml:
                            acc[nome] = xml
                        elif 'branch' in nl:
                            acc[nome] = [EMPRESA] if d.get('type') == 'array' else EMPRESA
                preencher(props, corpo)
            if '{' in url:
                continue
            try:
                resp = cli.get(url, params) if metodo == 'get' else cli.post(url, corpo or {})
            except Exception as e:  # noqa: BLE001
                log.append(f'{metodo.upper()} {path}: erro {str(e)[:100]}')
                continue
            log.append(f'{metodo.upper()} {path}: HTTP {resp.status_code}')
            if resp.status_code != 200:
                continue
            if resp.content[:4] == b'%PDF':
                return resp.content, log
            try:
                j = resp.json()
            except ValueError:
                continue
            pdf = _acha_pdf(j)
            if pdf:
                return pdf, log
            xml = xml or _acha_xml(j)
    return None, log


XML_URL = f'{BASE}/api/totvsmoda/fiscal/v2/xml-contents'
DANFE_URL = f'{BASE}/api/totvsmoda/fiscal/v2/danfe-search'


def danfe_totvs(chave: str, nota: dict = None) -> tuple:
    """(pdf, log): DANFE oficial em 2 passos do TOTVS (so leitura):
    1. GET fiscal/v2/xml-contents/{chave} -> mainInvoiceXml (404 = nota nunca autorizada)
    2. POST fiscal/v2/danfe-search {mainInvoiceXml, nfeDocumentType: NFeNormal} -> danfePdfBase64 (leva ~20 s)"""
    import base64
    log = []
    chave = ''.join(c for c in str(chave or '') if c.isdigit())
    if len(chave) != 44:
        return None, [f'chave de acesso invalida ({len(chave)} digitos)']
    cli = api.TotvsModaClient()
    cli.autenticar()
    r = cli.get(f'{XML_URL}/{chave}', {})
    log.append(f'GET xml-contents: HTTP {r.status_code}')
    if r.status_code == 404:
        return None, log + ['NF nunca foi autorizada (sem XML)']
    if r.status_code != 200:
        return None, log + [r.text[:200]]
    j = r.json()
    xml = (j.get('mainInvoiceXml') if isinstance(j, dict) else None) or _acha_xml(j)
    if not xml:
        return None, log + ['resposta sem mainInvoiceXml']
    r = cli.post(DANFE_URL, dict(mainInvoiceXml=xml, nfeDocumentType='NFeNormal'))
    log.append(f'POST danfe-search: HTTP {r.status_code}')
    if r.status_code != 200:
        return None, log + [r.text[:200]]
    j = r.json()
    b64 = j.get('danfePdfBase64') if isinstance(j, dict) else None
    if b64:
        try:
            return base64.b64decode(b64), log
        except Exception as e:  # noqa: BLE001
            return None, log + [f'base64 invalido: {e}']
    pdf = _acha_pdf(j)
    return (pdf, log) if pdf else (None, log + ['resposta sem danfePdfBase64'])
