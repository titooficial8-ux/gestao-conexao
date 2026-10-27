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

from . import api
from .pedidos import _em_lotes, _paginar

BASE = api.BASE_URL
BATCH_URL = f'{BASE}/api/totvsmoda/product/v2/batch/search'
BALANCES_URL = f'{BASE}/api/totvsmoda/product/v2/balances/search'
PRODUCTS_URL = f'{BASE}/api/totvsmoda/product/v2/products/search'
SALDO_INSPECAO = os.getenv('EXPEDICAO_SALDO_INSPECAO', '').strip()     # codigo do saldo 'aguardando inspecao'; vazio = acha pelo nome (INSPE)
UNIDADE_INSPECAO = os.getenv('EXPEDICAO_UNIDADE_INSPECAO', 'KG').strip().upper()
EMPRESA = int(os.getenv('PEDIDOS_EMPRESA', '1'))
SALDO = int(os.getenv('EXPEDICAO_SALDO', '1'))
NF_DIAS = int(os.getenv('EXPEDICAO_NF_DIAS', '90'))
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
    dados.update(inspecao=kg, inspecao_total_itens=len(saldos), inspecao_em=datetime.now().isoformat(timespec='seconds'))


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
    ini = hoje - timedelta(days=NF_DIAS if (completa or not notas) else 7)
    novos = _buscar_notas(cli, ini, hoje)
    for k in [k for k, v in notas.items() if v[0] >= ini.isoformat()]:
        notas.pop(k)
    for n in novos:
        if not _e_saida(n):
            continue
        e = n.get('eletronic') or {}
        notas[f"{n.get('branchCode')}-{n.get('invoiceSequence')}"] = [
            _dia(n.get('issueDate')) or _dia(n.get('invoiceDate')) or '', n.get('invoiceCode'), n.get('serialCode'), str(n.get('invoiceStatus') or ''),
            str(e.get('electronicInvoiceStatus') or ''), n.get('personCode'), str(n.get('personName') or '')[:60], round(_num(n.get('quantity')), 2),
            round(_num(n.get('totalValue')), 2), str(n.get('operatioName') or n.get('operationName') or '')[:50], str(n.get('issueDate') or '')[:19]]
    corte = (hoje - timedelta(days=NF_DIAS + 5)).isoformat()
    for k in [k for k, v in notas.items() if v[0] < corte]:
        notas.pop(k)
    dados.update(notas=notas, notas_em=datetime.now().isoformat(timespec='seconds'))
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
    ult = ent = sai = None
    for m in movs:
        d = str(m.get('movementDate') or '')[:10]
        if not d:
            continue
        ult = max(ult or d, d)
        if abs(_num(m.get('inQuantity'))) > 0:
            ent = max(ent or d, d)
        if abs(_num(m.get('outQuantity'))) > 0:
            sai = max(sai or d, d)
    return [ult, ent, sai] if ult else None


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


def painel(dias: int = 30) -> dict:
    d = ler()
    mov = ler_mov().get('produtos') or {}
    hoje = date.today()
    ini = (hoje - timedelta(days=dias - 1)).isoformat()
    ini_ant = (hoje - timedelta(days=2 * dias - 1)).isoformat()
    todas = (d.get('notas') or {}).values()
    notas = [n for n in todas if n[0] >= ini]
    anteriores = [n for n in todas if ini_ant <= n[0] < ini and _situacao(n) == 'Autorizada'] if 2 * dias <= NF_DIAS else None
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
    est = {pc: p for pc, p in est.items() if vale(pc)}
    tem_mov = bool(mov)
    por_art = {}
    for pc, p in est.items():
        cod, nome = art.get(pc, [pc, ''])
        a = por_art.setdefault(cod, dict(cod=cod, nome=nome, saldo=0.0, lotes=0, dias=None, ult_entrada=None, segunda=False))
        a['saldo'] += p['saldo']
        a['lotes'] += len(p['lotes'])
        if pc in mov:
            m = mov[pc]
            dias_pc = (hoje - date.fromisoformat(m[0])).days if m else 181          # sem movimento em 180 dias: "mais de 180"
            a['dias'] = dias_pc if a['dias'] is None else min(a['dias'], dias_pc)
            if m and m[1]:
                a['ult_entrada'] = max(a['ult_entrada'] or m[1], m[1])
    arts = sorted(por_art.values(), key=lambda a: a['saldo'], reverse=True)
    total = sum(a['saldo'] for a in arts)
    datados = [a for a in arts if a['dias'] is not None]
    faixas = [dict(faixa=r, artigos=0, metros=0.0) for _, r in FAIXAS]
    for a in datados:
        for i, (lim, _r) in enumerate(FAIXAS):
            if a['dias'] <= lim:
                faixas[i]['artigos'] += 1
                faixas[i]['metros'] += a['saldo']
                break
    m30 = sum(n[7] for n in todas if n[0] >= (hoje - timedelta(days=29)).isoformat() and _situacao(n) == 'Autorizada')
    parados = sorted(datados, key=lambda a: (a['dias'], a['saldo']), reverse=True)
    r2 = lambda v: round(v, 2)
    ins = d.get('inspecao') or {}
    insp = dict(cod=d.get('inspecao_cod'), nome=d.get('inspecao_desc'), unidade=UNIDADE_INSPECAO, kg=r2(sum(v[0] for v in ins.values())), itens=len(ins), outros=max(0, (d.get('inspecao_total_itens') or 0) - len(ins)),
                em=d.get('inspecao_em'), lista=[dict(cod=pc, nome=v[1], saldo=v[0]) for pc, v in sorted(ins.items(), key=lambda kv: -kv[1][0])[:300]])
    p90 = sum(a['saldo'] for a in datados if a['dias'] > 90)
    return dict(
        ok=True, dias=dias, hoje=hoje.isoformat(), notas_em=d.get('notas_em'), estoque_em=d.get('estoque_em'), mov_em=ler_mov().get('atualizado'),
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
                     parados90=[dict(a, saldo=r2(a['saldo'])) for a in parados if a['dias'] > 90][:300],
                     inspecao=insp,
                     maiores=[dict(a, saldo=r2(a['saldo']), pct=round(a['saldo'] / total * 100, 1) if total else 0) for a in arts[:8]],
                     parados=[dict(a, saldo=r2(a['saldo'])) for a in parados[:8]],
                     faixas=[dict(f, metros=r2(f['metros'])) for f in faixas]))
