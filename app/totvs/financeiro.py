"""Financeiro (so leitura do TOTVS): titulos a receber e a pagar, vencidos, exportacao, regras de pagamento dos clientes (prorrogacao) e verificacao dos recursos do TOTVS (boleto, alteracao de vencimento).

Os endpoints e campos do financeiro sao DESCOBERTOS pelo swagger de cada servico (accounts-receivable, accounts-payable...), nao adivinhados.
Se o swagger nao ajudar, use FIN_RECEBER_URL / FIN_PAGAR_URL no .env. Guarda tudo em instance/totvs/financeiro.json."""
import json
import os
import re
import threading
import time
import traceback
import unicodedata
from datetime import date, datetime, timedelta

from . import api
from .pedidos import _paginar

BASE = api.BASE_URL
EMPRESA = int(os.getenv('PEDIDOS_EMPRESA', '1'))
INTERVALO_MIN = max(2, int(os.getenv('FINANCEIRO_INTERVALO_MIN', '10')))
JANELA_ATRAS = int(os.getenv('FINANCEIRO_DIAS_ATRAS', '540'))          # vencidos ate 18 meses para tras
JANELA_FRENTE = int(os.getenv('FINANCEIRO_DIAS_FRENTE', '120'))
ARQUIVO = api.OUT_DIR / 'financeiro.json'
SERVICOS = ('accounts-receivable', 'accounts-payable', 'financial', 'billing', 'bank-slip', 'cashier', 'fiscal')
ESTADO = dict(rodando=False, ultima=None, erro_receber=None, erro_pagar=None, msg='')
_trava = threading.Lock()
_swagger_cache = {}

# ---- regras de pagamento dos clientes (nao pagam em certos dias): chave = trecho do nome sem acento
REGRAS = [
    dict(chave='MARANHAO', nome='Maranhão', texto='não paga às segundas-feiras', ok=lambda d: d.weekday() != 0),
    dict(chave='PIKOLIN', nome='Pikolin', texto='só paga nos dias 13 e 28', ok=lambda d: d.day in (13, 28)),
    dict(chave='VIEIR', nome='Vieiro', texto='só paga às quintas-feiras', ok=lambda d: d.weekday() == 3),
    dict(chave='PLUMATEX', nome='Plumatex', texto='não paga às segundas-feiras nem nos dias 04, 05, 06, 10, 20 e 25', ok=lambda d: d.weekday() != 0 and d.day not in (4, 5, 6, 10, 20, 25)),
]


def _sem_acento(t) -> str:
    return ''.join(c for c in unicodedata.normalize('NFD', str(t or '')) if unicodedata.category(c) != 'Mn').upper()


def regra_do_cliente(nome: str):
    n = _sem_acento(nome)
    return next((r for r in REGRAS if r['chave'] in n), None)


def proximo_valido(regra: dict, d: date) -> date:
    for i in range(0, 90):
        x = d + timedelta(days=i)
        if regra['ok'](x):
            return x
    return d


# ---------------------------------------------------------------- swagger
def _swagger(cli, svc: str):
    if svc in _swagger_cache:
        return _swagger_cache[svc]
    doc = None
    for ver in ('v2', 'v1'):
        try:
            r = cli.get(f'{BASE}/api/totvsmoda/{svc}/{ver}/swagger/v1/swagger.json', {})
            if r.status_code == 200:
                doc = r.json()
                break
        except Exception:  # noqa: BLE001
            continue
    _swagger_cache[svc] = doc
    return doc


def _resolve(doc, n):
    return ((doc.get('components') or {}).get('schemas') or {}).get(((n or {}).get('$ref') or '').split('/')[-1], n or {})


def _props_filtro(doc, path):
    op = ((doc.get('paths') or {}).get(path) or {}).get('post') or {}
    corpo = _resolve(doc, (((op.get('requestBody') or {}).get('content') or {}).get('application/json') or {}).get('schema'))
    filtro = _resolve(doc, (corpo.get('properties') or {}).get('filter'))
    return list((filtro.get('properties') or {}).keys())


def catalogo(cli) -> dict:
    """Recursos que o TOTVS oferece em cada servico financeiro (so nomes de endpoint e metodo)."""
    saida = {}
    for svc in SERVICOS:
        doc = _swagger(cli, svc)
        if not doc:
            continue
        saida[svc] = sorted((p, sorted(m.upper() for m in ops if m in ('get', 'post', 'put', 'patch', 'delete'))) for p, ops in (doc.get('paths') or {}).items())
    return saida


def verificacao(cli) -> dict:
    cat = catalogo(cli)
    boleto, venc, receber, pagar = [], [], [], []
    for svc, lista in cat.items():
        for p, mets in lista:
            pl = p.lower()
            item = dict(servico=svc, path=p, metodos=mets, escrita=any(m in mets for m in ('PUT', 'PATCH', 'DELETE')) or (('POST' in mets) and not pl.endswith('search')))
            if any(k in pl for k in ('slip', 'boleto', 'billet', 'bank-', 'cnab', 'barcode', 'ourNumber'.lower())):
                boleto.append(item)
            if any(k in pl for k in ('expir', 'due', 'extens', 'prorrog', 'postpone', 'maturity', 'change-date', 'reschedul')):
                venc.append(item)
            if svc == 'accounts-receivable':
                receber.append(item)
            if svc == 'accounts-payable':
                pagar.append(item)
    return dict(servicos=list(cat.keys()), boleto=boleto, vencimento=venc, receber=receber[:60], pagar=pagar[:60], total_endpoints=sum(len(v) for v in cat.values()))


RECEBER_URL = os.getenv('FIN_RECEBER_URL', f'{BASE}/api/totvsmoda/accounts-receivable/v2/documents/search')
PAGAR_URL = os.getenv('FIN_PAGAR_URL', f'{BASE}/api/totvsmoda/accounts-payable/v2/duplicates/search')
SPANS_CHANGE = (3650, 1825, 730, 365, 180, 90)                  # intervalo de ALTERACAO (obrigatorio no TOTVS): tenta o mais largo que a API aceitar


def _p(item: dict, *cands):
    low = {k.lower(): v for k, v in item.items()}
    for c in cands:
        v = low.get(c.lower())
        if v not in (None, ''):
            return v
    return None


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _dia(v):
    try:
        return datetime.fromisoformat(str(v).replace('Z', '')[:19]).date().isoformat()
    except ValueError:
        return None


def _norm_receber(it: dict) -> dict:
    """accounts-receivable/documents: receivableCode (fatura), installmentCode, customerCode, expiredDate, installmentValue, paidValue, bearerName, ourNumber."""
    valor, pago = _num(it.get('installmentValue')), _num(it.get('paidValue'))
    saldo = max(valor - pago, 0.0)                                     # o TOTVS nao manda saldo: em aberto = parcela - pago
    pag = _dia(it.get('paymentDate'))
    return dict(tipo='R', doc=str(it.get('receivableCode') or ''), parcela=str(it.get('installmentCode') or ''), cod=str(it.get('customerCode') or ''), cnpj=str(it.get('customerCpfCnpj') or ''), nome='',
                emissao=_dia(it.get('issueDate')), venc=_dia(it.get('expiredDate')), valor=round(valor, 2), saldo=round(saldo, 2), pago=round(pago, 2), pag_em=pag, status=str(it.get('status') or '')[:30],
                aberto=bool(saldo > 0.005 and not pag), moeda='', banco=str(it.get('bearerName') or it.get('bearerCode') or '')[:24], nosso=str(it.get('ourNumber') or '')[:30],
                tipo_doc=str(it.get('documentType') or '')[:24])


def _norm_pagar(it: dict) -> dict:
    """accounts-payable/duplicates: duplicateCode, supplierCode, installmentCode, dueDate, duplicateValue, paidValue, settlementDate."""
    valor, pago = _num(it.get('duplicateValue')), _num(it.get('paidValue'))
    saldo = max(valor - pago, 0.0)
    pag = _dia(it.get('settlementDate'))
    return dict(tipo='P', doc=str(it.get('duplicateCode') or ''), parcela=str(it.get('installmentCode') or ''), cod=str(it.get('supplierCode') or ''), cnpj=str(it.get('supplierCpfCnpj') or ''), nome='',
                emissao=_dia(it.get('issueDate')), venc=_dia(it.get('dueDate') or it.get('expiredDate')), valor=round(valor, 2), saldo=round(saldo, 2), pago=round(pago, 2), pag_em=pag, status=str(it.get('status') or '')[:30],
                aberto=bool(saldo > 0.005 and not pag), moeda='', banco=str(it.get('bearerCode') or '')[:24], nosso='', tipo_doc='')


def _janelas(ini: date, fim: date, dias: int = 31):
    a = ini
    while a <= fim:
        b = min(fim, a + timedelta(days=dias - 1))
        yield a, b
        a = b + timedelta(days=1)


def _buscar_tipo(cli, tipo: str, dados: dict) -> list:
    """Le os titulos em janelas de vencimento. O filtro `change` (intervalo de alteracao) e obrigatorio: manda sempre, o mais largo que a API aceitar."""
    receber = tipo == 'R'
    url = RECEBER_URL if receber else PAGAR_URL
    hoje = date.today()
    ini = hoje - timedelta(days=JANELA_ATRAS if receber else min(JANELA_ATRAS, 365))
    fim = hoje + timedelta(days=JANELA_FRENTE)
    span = dados.get('change_dias_' + tipo) or SPANS_CHANGE[0]
    abertos = receber                                              # a receber: so em aberto (hasOpenInvoices)
    out, primeiro = [], True
    for a, b in _janelas(ini, fim):
        while True:
            chg = dict(startDate=f'{hoje - timedelta(days=span)}T00:00:00', endDate=f'{hoje}T23:59:59')
            if receber:
                chg['inCheck'] = False
            filtro = dict(branchCodeList=[EMPRESA], change=chg, startExpiredDate=f'{a}T00:00:00', endExpiredDate=f'{b}T23:59:59')
            if abertos:
                filtro['hasOpenInvoices'] = True
            try:
                itens = _paginar(cli, url, dict(filter=filtro), tam=100)
                break
            except RuntimeError as e:
                msg = str(e)
                if abertos and 'hasOpenInvoices' in msg:
                    abertos = False
                    continue
                menor = [x for x in SPANS_CHANGE if x < span]
                if primeiro and 'HTTP 400' in msg and menor:         # intervalo de alteracao largo demais: tenta um menor
                    span = menor[0]
                    continue
                raise
        primeiro = False
        out += itens
        if itens and f'amostra_{tipo}' not in dados:
            dados[f'amostra_{tipo}'] = {k: v for k, v in itens[0].items() if not isinstance(v, (list, dict))}
    dados['change_dias_' + tipo] = span
    dados['url_' + tipo] = url
    return [(_norm_receber if receber else _norm_pagar)(i) for i in out]


def _nomes(cli, titulos: list, dados: dict) -> None:
    """O TOTVS manda so o codigo e o CNPJ: busca o nome fantasia da pessoa (cliente/fornecedor) uma vez e guarda."""
    from .entrada import _fantasias
    cache = dados.setdefault('nomes', {})
    codigos = {int(t['cod']) for t in titulos if t['cod'].isdigit()}
    try:
        _fantasias(cli, codigos, cache)
    except Exception:  # noqa: BLE001
        traceback.print_exc()
    for t in titulos:
        t['nome'] = (cache.get(t['cod']) or t['cnpj'] or t['cod'])[:70]


def _diff(antes: dict, depois: list) -> list:
    """Mudancas nos titulos a receber desde a leitura anterior (vencimento alterado, baixa, novo titulo)."""
    agora = datetime.now().isoformat(timespec='seconds')
    ch = []
    atual = {f"{t['doc']}|{t['parcela']}|{t['cod']}": t for t in depois}
    for k, t in atual.items():
        a = antes.get(k)
        if a is None:
            if antes:
                ch.append(dict(em=agora, tipo='Novo título', doc=t['doc'], parcela=t['parcela'], nome=t['nome'], de='', para=t['venc'], valor=t['valor']))
            continue
        if a['venc'] != t['venc']:
            ch.append(dict(em=agora, tipo='Vencimento alterado', doc=t['doc'], parcela=t['parcela'], nome=t['nome'], de=a['venc'], para=t['venc'], valor=t['valor']))
        if a['aberto'] and not t['aberto']:
            ch.append(dict(em=agora, tipo='Baixado / pago', doc=t['doc'], parcela=t['parcela'], nome=t['nome'], de=a['venc'], para=t['pag_em'] or '', valor=t['valor']))
        elif abs(a['saldo'] - t['saldo']) > 0.01 and t['aberto']:
            ch.append(dict(em=agora, tipo='Saldo alterado', doc=t['doc'], parcela=t['parcela'], nome=t['nome'], de=str(a['saldo']), para=str(t['saldo']), valor=t['valor']))
    return ch


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


def atualizar() -> bool:
    if not _trava.acquire(blocking=False):
        return False
    ESTADO.update(rodando=True, erro_receber=None, erro_pagar=None, msg='Lendo o financeiro no TOTVS...')
    try:
        cli = api.TotvsModaClient()
        cli.autenticar()
        dados = ler()
        antes = {f"{t['doc']}|{t['parcela']}|{t['cod']}": t for t in dados.get('receber', [])}
        for tipo, chave in (('R', 'receber'), ('P', 'pagar')):
            try:
                lista = _buscar_tipo(cli, tipo, dados)
                _nomes(cli, lista, dados)
                dados[chave] = lista
                if tipo == 'R':
                    alt = _diff(antes, lista)
                    dados['alteracoes'] = (alt + dados.get('alteracoes', []))[:300]
            except Exception as e:  # noqa: BLE001
                traceback.print_exc()
                ESTADO['erro_' + chave] = str(e)[:300]
        dados['atualizado'] = datetime.now().isoformat(timespec='seconds')
        _gravar(dados)
        ESTADO.update(ultima=dados['atualizado'], msg='ok')
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        ESTADO.update(erro_receber=str(e)[:300], msg='falha')
    finally:
        ESTADO['rodando'] = False
        _trava.release()
    return not (ESTADO['erro_receber'] or ESTADO['erro_pagar'])


def iniciar_agendador(app=None) -> None:
    from .pedidos import configurado
    if not configurado():
        return

    def loop():
        time.sleep(70)
        while True:
            try:
                atualizar()
            except Exception:  # noqa: BLE001
                traceback.print_exc()
            time.sleep(INTERVALO_MIN * 60)
    threading.Thread(target=loop, daemon=True, name='totvs-financeiro').start()


# ---------------------------------------------------------------- painel
FAIXAS_ATRASO = [(15, '1 a 15 dias'), (30, '16 a 30 dias'), (60, '31 a 60 dias'), (90, '61 a 90 dias'), (10 ** 6, 'mais de 90 dias')]


def _exportacao(t: dict, tabela: dict) -> bool:
    from app.paises import pais_do_cliente
    if t.get('moeda') and t['moeda'].upper() not in ('BRL', 'R$', 'REAL', '1', ''):
        return True
    p = pais_do_cliente(t['nome'], tabela)
    return bool(p and p != 'br')


def painel() -> dict:
    from app.models import ClientePais
    d = ler()
    hoje = date.today()
    tabela = {c.nome_normalizado: c.pais for c in ClientePais.query.all()}
    def prep(lista):
        out = []
        for t in lista:
            if not t['aberto'] or not t['venc']:
                continue
            atraso = (hoje - date.fromisoformat(t['venc'])).days
            out.append(dict(t, atraso=max(atraso, 0), vencido=atraso > 0, export=_exportacao(t, tabela) if t['tipo'] == 'R' else False))
        return out
    rec, pag = prep(d.get('receber', [])), prep(d.get('pagar', []))
    r2 = lambda v: round(v, 2)
    rv, pv = [t for t in rec if t['vencido']], [t for t in pag if t['vencido']]
    aging = [dict(faixa=n, qtd=0, valor=0.0) for _, n in FAIXAS_ATRASO]
    for t in rv:
        for i, (lim, _n) in enumerate(FAIXAS_ATRASO):
            if t['atraso'] <= lim:
                aging[i]['qtd'] += 1
                aging[i]['valor'] += t['saldo']
                break
    dev = {}
    for t in rv:
        c = dev.setdefault(t['nome'] or t['cod'], dict(cliente=t['nome'] or t['cod'], valor=0.0, titulos=0, atraso=0))
        c['valor'] += t['saldo']
        c['titulos'] += 1
        c['atraso'] = max(c['atraso'], t['atraso'])
    sem = []
    for i in range(8):                                                       # fluxo das proximas 8 semanas
        a, b = hoje + timedelta(days=7 * i), hoje + timedelta(days=7 * i + 6)
        sem.append(dict(de=a.isoformat(), ate=b.isoformat(), receber=r2(sum(t['saldo'] for t in rec if a.isoformat() <= t['venc'] <= b.isoformat())), pagar=r2(sum(t['saldo'] for t in pag if a.isoformat() <= t['venc'] <= b.isoformat()))))
    exp = [t for t in rec if t['export']]
    # prorrogacao: titulos a receber de clientes com regra cujo vencimento cai em dia que o cliente nao paga
    pror = []
    for t in rec:
        rg = regra_do_cliente(t['nome'])
        if not rg:
            continue
        v = date.fromisoformat(t['venc'])
        if rg['ok'](v):
            continue
        novo = proximo_valido(rg, v)
        pror.append(dict(cliente=t['nome'], regra=rg['nome'], doc=t['doc'], parcela=t['parcela'], venc=t['venc'], novo=novo.isoformat(), dias=(novo - v).days, valor=t['saldo'], vencido=t['vencido'], deixa_de_ser_atraso=t['vencido'] and novo >= hoje, banco=t['banco'], nosso=t['nosso']))
    pror.sort(key=lambda x: x['venc'])
    return dict(
        ok=True, atualizado=d.get('atualizado'), hoje=hoje.isoformat(),
        fonte=dict(erro_receber=ESTADO['erro_receber'], erro_pagar=ESTADO['erro_pagar'], rodando=ESTADO['rodando'], url_receber=d.get('url_R'), url_pagar=d.get('url_P'), n_receber=len(d.get('receber', [])), n_pagar=len(d.get('pagar', []))),
        kpis=dict(receber=r2(sum(t['saldo'] for t in rec)), receber_n=len(rec), receber_vencido=r2(sum(t['saldo'] for t in rv)), receber_vencido_n=len(rv), atraso_medio=round(sum(t['atraso'] for t in rv) / len(rv)) if rv else 0,
                  receber_30=r2(sum(t['saldo'] for t in rec if not t['vencido'] and t['venc'] <= (hoje + timedelta(days=30)).isoformat())),
                  pagar=r2(sum(t['saldo'] for t in pag)), pagar_n=len(pag), pagar_vencido=r2(sum(t['saldo'] for t in pv)), pagar_vencido_n=len(pv),
                  pagar_30=r2(sum(t['saldo'] for t in pag if not t['vencido'] and t['venc'] <= (hoje + timedelta(days=30)).isoformat())),
                  export=r2(sum(t['saldo'] for t in exp)), export_n=len(exp), export_vencido=r2(sum(t['saldo'] for t in exp if t['vencido']), ),
                  moedas=sorted({t['moeda'] for t in exp if t['moeda']}), pror_n=len(pror)),
        aging=[dict(a, valor=r2(a['valor'])) for a in aging], semanas=sem,
        devedores=[dict(c, valor=r2(c['valor'])) for c in sorted(dev.values(), key=lambda c: -c['valor'])[:10]],
        vencidos_receber=sorted(rv, key=lambda t: -t['atraso'])[:800], vencidos_pagar=sorted(pv, key=lambda t: -t['atraso'])[:800], exportacao=sorted(exp, key=lambda t: t['venc'])[:800],
        prorrogacao=pror[:800], regras=[dict(nome=r['nome'], texto=r['texto']) for r in REGRAS], alteracoes=(d.get('alteracoes') or [])[:100])
