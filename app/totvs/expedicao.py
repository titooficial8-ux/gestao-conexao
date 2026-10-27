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
EMPRESA = int(os.getenv('PEDIDOS_EMPRESA', '1'))
SALDO = int(os.getenv('EXPEDICAO_SALDO', '1'))
NF_DIAS = int(os.getenv('EXPEDICAO_NF_DIAS', '90'))
INTERVALO_SEG = max(30, int(os.getenv('EXPEDICAO_INTERVALO_SEG', '120')))        # a cada 2 min
COMPLETA_MIN = 20
ARQUIVO = api.OUT_DIR / 'expedicao.json'
ESTADO = dict(rodando=False, ultima=None, erro=None, msg='', erro_notas=None, erro_estoque=None)
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
            data = next((_dia(lt.get(k)) for k in CHAVES_DATA if _dia(lt.get(k))), None)
            visto.setdefault(f'{pc}|{lid}', hoje)
            p = produtos.setdefault(pc, dict(saldo=0.0, lotes=[]))
            p['saldo'] += bal
            p['lotes'].append([lid, round(bal, 2), data or visto[f'{pc}|{lid}'], bool(data)])
    for k in [k for k in visto if k.split('|')[0] not in produtos]:
        visto.pop(k)                                         # produto saiu do estoque
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
    ESTADO.update(rodando=True, erro=None, erro_notas=None, erro_estoque=None, msg='Lendo Expedição no TOTVS...')
    try:
        cli = api.TotvsModaClient()
        cli.autenticar()
        dados = ler()
        for nome, fn in (('estoque', lambda: atualizar_estoque(cli, dados)), ('notas', lambda: atualizar_notas(cli, dados, completa))):
            try:
                fn()
            except Exception as e:  # noqa: BLE001
                traceback.print_exc()
                ESTADO['erro_' + ('estoque' if nome == 'estoque' else 'notas')] = str(e)[:300]
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
    threading.Thread(target=loop, daemon=True, name='totvs-expedicao').start()


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


def painel(dias: int = 30) -> dict:
    d = ler()
    hoje = date.today()
    ini = (hoje - timedelta(days=dias - 1)).isoformat()
    notas = [n for n in (d.get('notas') or {}).values() if n[0] >= ini]
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
    ultimas = sorted(notas, key=lambda n: (n[10], n[1] or 0), reverse=True)[:12]
    fmt = lambda n: dict(dia=n[0], nf=n[1], serie=n[2], situacao=_situacao(n), cliente=n[6], metros=n[7], valor=n[8], operacao=n[9], hora=n[10][11:16])
    # estoque
    est, art = d.get('estoque') or {}, d.get('artigos') or {}
    por_art = {}
    for pc, p in est.items():
        cod, nome = art.get(pc, [pc, ''])
        a = por_art.setdefault(cod, dict(cod=cod, nome=nome, saldo=0.0, lotes=0, dias=0, data_real=True))
        a['saldo'] += p['saldo']
        for lid, bal, data, real in p['lotes']:
            a['lotes'] += 1
            a['dias'] = max(a['dias'], (hoje - date.fromisoformat(data)).days)
            a['data_real'] = a['data_real'] and real
    arts = sorted(por_art.values(), key=lambda a: a['saldo'], reverse=True)
    total = sum(a['saldo'] for a in arts)
    faixas = [dict(faixa=r, artigos=0, metros=0.0) for _, r in FAIXAS]
    for a in arts:
        for i, (lim, _r) in enumerate(FAIXAS):
            if a['dias'] <= lim:
                faixas[i]['artigos'] += 1
                faixas[i]['metros'] += a['saldo']
                break
    m30 = sum(n[7] for n in (d.get('notas') or {}).values() if n[0] >= (hoje - timedelta(days=29)).isoformat() and _situacao(n) == 'Autorizada')
    parados = sorted(arts, key=lambda a: (a['dias'], a['saldo']), reverse=True)
    r2 = lambda v: round(v, 2)
    return dict(
        ok=True, dias=dias, hoje=hoje.isoformat(), notas_em=d.get('notas_em'), estoque_em=d.get('estoque_em'),
        fonte=dict(ultima=ESTADO['ultima'], erro=ESTADO['erro'], erro_notas=ESTADO['erro_notas'], erro_estoque=ESTADO['erro_estoque'], rodando=ESTADO['rodando']),
        notas=dict(total=len(notas), autorizadas=ok['qtd'], valor=r2(ok['valor']), metros=r2(ok['metros']), hoje=sum(1 for n in notas if n[0] == hoje.isoformat() and _situacao(n) == 'Autorizada'),
                   pendentes=len(problemas), ticket=r2(ok['valor'] / ok['qtd']) if ok['qtd'] else 0,
                   por_situacao=sorted((dict(v, valor=r2(v['valor']), metros=r2(v['metros'])) for v in por_sit.values()), key=lambda v: -v['qtd']),
                   por_dia=[dict(x, valor=r2(x['valor']), metros=r2(x['metros'])) for x in sorted(por_dia.values(), key=lambda x: x['dia'])],
                   clientes=[dict(x, valor=r2(x['valor']), metros=r2(x['metros'])) for x in sorted(por_cli.values(), key=lambda x: -x['valor'])[:8]],
                   ultimas=[fmt(n) for n in ultimas], problemas=[fmt(n) for n in problemas[:12]]),
        estoque=dict(total=r2(total), artigos=len(arts), lotes=sum(a['lotes'] for a in arts),
                     top5_pct=round(sum(a['saldo'] for a in arts[:5]) / total * 100) if total else 0,
                     saida_30d=r2(m30), cobertura_dias=round(total / (m30 / 30)) if m30 else None,
                     parado_90=r2(sum(a['saldo'] for a in arts if a['dias'] > 90)), parado_90_pct=round(sum(a['saldo'] for a in arts if a['dias'] > 90) / total * 100) if total else 0,
                     datas_do_totvs=bool(arts) and all(a['data_real'] for a in arts),
                     maiores=[dict(a, saldo=r2(a['saldo'])) for a in arts[:10]], parados=[dict(a, saldo=r2(a['saldo'])) for a in parados[:10]],
                     faixas=[dict(f, metros=r2(f['metros'])) for f in faixas]))
