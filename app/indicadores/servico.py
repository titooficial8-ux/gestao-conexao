"""Dados da aba Indicadores.

Fontes (todas da API do TOTVS, so leitura):
- Malharia: as pecas pesadas no local 23 (baixas de fio, a mesma base do Controle de Producao).
- Acabamento: transacoes 2030/2031 (entrada da peca acabada), com metros e kg (metros x gramatura x largura / 1000).
- Faturamento: notas de venda (VDA) do Relatorio 150, metros = quantidade total do dia, ticket = valor bruto / metros.
Dia de producao = 06:20 ate 06:20 do dia seguinte; turnos 06:20-14:20, 14:20-22:20 e 22:20-06:20.
"""
from __future__ import annotations

import json
import os
import threading
import time
import traceback
from collections import defaultdict
from datetime import date, datetime, timedelta

from app import db
from app.indicadores.models import IndAcabamento, IndProduto, IndReprovaCru
from app.totvs import api

BASE_URL = api.BASE_URL
TRX_URL = f'{BASE_URL}/api/totvsmoda/general/v2/transactions/search'
PROD_URL = f'{BASE_URL}/api/totvsmoda/product/v2/products/search'
EMPRESA = 1
OPS_ACABAMENTO = [int(x) for x in os.getenv('INDICADORES_OPS_ACABAMENTO', '2030,2031').split(',') if x.strip()]
DESDE = os.getenv('INDICADORES_DESDE', '2026-09-01')
INTERVALO_MIN = float(os.getenv('INDICADORES_INTERVALO_MIN', '10'))
INICIO_DIA = timedelta(hours=6, minutes=20)
ESTADO = dict(ultima=None, erro=None, pecas=0)
_trava = threading.Lock()
_cache: dict = {}


def dia_producao(h: datetime) -> date:
    return (h - INICIO_DIA).date()


def turno(h: datetime) -> int:
    x = h - INICIO_DIA
    return min((x.hour * 3600 + x.minute * 60 + x.second) // 28800, 2)


# ---------------------------------------------------------------- acabamento (TOTVS)
def _num(v):
    try:
        return float(str(v).replace(',', '.'))
    except (TypeError, ValueError):
        return None


def _buscar_trx(cli, ini: date, fim: date) -> list:
    saida = []
    a = ini
    while a <= fim:
        b = min(fim, a + timedelta(days=13))
        pag = 1
        while True:
            r = cli.post(TRX_URL, dict(branchCodeList=[EMPRESA], operationCodeList=OPS_ACABAMENTO, startChangeDate=f'{a.isoformat()}T00:00:00',
                                       endChangeDate=f'{(b + timedelta(days=1)).isoformat()}T00:00:00', page=pag, pageSize=500))
            if r.status_code != 200:
                raise RuntimeError(f'transacoes {a}..{b}: HTTP {r.status_code} {r.text[:160]}')
            j = r.json()
            saida += j.get('items') or []
            if not j.get('hasNext') or pag >= 80:
                break
            pag += 1
        a = b + timedelta(days=1)
    return saida


def _produtos(cli, codigos: list) -> None:
    """Gramatura e largura dos produtos que ainda nao conhecemos."""
    faltam = [c for c in codigos if not db.session.get(IndProduto, c)]
    for i in range(0, len(faltam), 50):
        lote = faltam[i:i + 50]
        r = cli.post(PROD_URL, dict(filter=dict(productCodeList=lote), option=dict(branchInfoCode=EMPRESA), page=1, pageSize=50, expand='additionalFields'))
        if r.status_code != 200:
            raise RuntimeError(f'produtos: HTTP {r.status_code} {r.text[:160]}')
        achados = {}
        for p in r.json().get('items') or []:
            campos = {str(f.get('name') or '').upper(): f.get('value') for f in p.get('additionalFields') or []}
            achados[p['productCode']] = IndProduto(codigo=p['productCode'], nome=(p.get('productName') or '')[:200],
                                                   gramatura=_num(campos.get('GRAMATURA M2')), largura=_num(campos.get('LARGURA PADRAO')))
        for c in lote:
            db.session.add(achados.get(c) or IndProduto(codigo=c, nome='', gramatura=None, largura=None))
        db.session.commit()


def atualizar_acabamento(app) -> bool:
    """Busca as pecas novas do acabamento (so leitura) e calcula o kg de cada uma."""
    if not _trava.acquire(blocking=False):
        return False
    try:
        with app.app_context():
            cli = api.TotvsModaClient()
            cli.autenticar()
            ultimo = db.session.query(db.func.max(IndAcabamento.mudou)).scalar()
            ini = (ultimo.date() - timedelta(days=2)) if ultimo else date.fromisoformat(DESDE)
            hoje = date.today()
            itens = _buscar_trx(cli, ini, hoje)
            existentes = {c for (c,) in db.session.query(IndAcabamento.codigo).filter(IndAcabamento.mudou >= datetime.combine(ini, datetime.min.time())).all()}
            novos = []
            for t in itens:
                cod = t.get('transactionCode')
                if cod in existentes or not t.get('lastchangeDate'):
                    continue
                for it in t.get('items') or []:
                    q = _num(it.get('quantity'))
                    if not q or it.get('productCode') is None:
                        continue
                    novos.append(IndAcabamento(codigo=cod, operacao=t.get('operationCode'), mudou=datetime.fromisoformat(t['lastchangeDate'][:19]), produto=it['productCode'], metros=q))
                    existentes.add(cod)
                    break
            if novos:
                db.session.add_all(novos)
                db.session.commit()
            _produtos(cli, sorted({r.produto for r in IndAcabamento.query.filter(IndAcabamento.kg.is_(None)).all()}))
            fator = {p.codigo: (p.gramatura * p.largura / 1000 if p.gramatura and p.largura else None) for p in IndProduto.query.all()}
            for r in IndAcabamento.query.filter(IndAcabamento.kg.is_(None)).all():
                if fator.get(r.produto):
                    r.kg = round(r.metros * fator[r.produto], 3)
            db.session.commit()
            ESTADO.update(ultima=datetime.now().isoformat(timespec='seconds'), erro=None, pecas=IndAcabamento.query.count())
            _cache.clear()
        return True
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        ESTADO['erro'] = str(e)[:200]
        return False
    finally:
        _trava.release()


def iniciar_agendador(app) -> None:
    def loop():
        time.sleep(60)
        while True:
            try:
                atualizar_acabamento(app)
                atualizar_reprova_cru(app)
            except Exception:  # noqa: BLE001
                traceback.print_exc()
            time.sleep(INTERVALO_MIN * 60)
    threading.Thread(target=loop, daemon=True, name='indicadores-acabamento').start()


# ---------------------------------------------------------------- agregacao por dia de producao e turno
def _vazio():
    return {'t': [dict(pecas=0, metros=0.0, kg=0.0) for _ in range(3)]}


def _fechar(d: dict) -> dict:
    d['total'] = dict(pecas=sum(t['pecas'] for t in d['t']), metros=round(sum(t['metros'] for t in d['t']), 1), kg=round(sum(t['kg'] for t in d['t']), 1))
    for t in d['t']:
        t['metros'] = round(t['metros'], 1)
        t['kg'] = round(t['kg'], 1)
    return d


def malharia_por_dia() -> dict:
    """{data: {t:[3 turnos], total}} das pecas pesadas no local 23 (sem amostras), com a mesma conta do Controle de Producao."""
    from app.main.routes import _api_baixas, _hub_movimentos_json
    from app.models import BaixaFio
    chave = ('malharia', BaixaFio.query.count(), db.session.query(db.func.max(BaixaFio.mudou)).scalar())
    quente = _cache.get(chave)
    if quente and time.time() - quente[0] < 120:
        return quente[1]
    linhas = json.loads('[' + _api_baixas(dict(_hub_movimentos_json())) + ']')
    dias = defaultdict(_vazio)
    for r in linhas:
        if r.get('cod_artigo') and 'amostra' in r['cod_artigo'].lower():
            continue
        h = datetime.fromisoformat(r['dt_real'].replace('Z', '')) - timedelta(hours=3)
        alvo = dias[dia_producao(h)]['t'][turno(h)]
        alvo['pecas'] += 1
        alvo['metros'] += r.get('metro_padrao') or 0
        alvo['kg'] += r.get('qt_pesos') or 0
    saida = {d.isoformat(): _fechar(v) for d, v in dias.items()}
    _cache[chave] = (time.time(), saida)
    return saida


def acabamento_por_dia() -> dict:
    dias = defaultdict(_vazio)
    for r in IndAcabamento.query.order_by(IndAcabamento.mudou).all():
        alvo = dias[dia_producao(r.mudou)]['t'][turno(r.mudou)]
        alvo['pecas'] += 1
        alvo['metros'] += r.metros or 0
        alvo['kg'] += r.kg or 0
    return {d.isoformat(): _fechar(v) for d, v in dias.items()}


def faturamento_por_dia(ano: int, mes: int) -> dict:
    """Metros = quantidade total das vendas (VDA) do dia; ticket = valor bruto / metros (como a planilha FATURAMENTO)."""
    from app.totvs import base, servico
    cols, linhas, _ = servico.linhas_armazem()
    if not cols:
        return {}
    metros, valor = defaultdict(float), defaultdict(float)
    for l in linhas:
        x = base.derivar(dict(zip(cols, l)))
        if x['tipo'] != 'VDA' or x['ano'] != ano or x['mes'] != mes or not x['data']:
            continue
        d = x['data'].date().isoformat() if hasattr(x['data'], 'date') else str(x['data'])[:10]
        metros[d] += x['quant']
        valor[d] += x['bruta']
    return {d: dict(metros=round(metros[d], 1), valor=round(valor[d], 2), ticket=round(valor[d] / metros[d], 3) if metros[d] else 0) for d in sorted(metros)}


def _seguro(fonte, fn, avisos: list, *args):
    """Roda uma fonte de dados; se falhar, devolve vazio e guarda o motivo (a tela mostra o aviso em vez de dar erro 500)."""
    try:
        return fn(*args)
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        avisos.append(f'{fonte}: {type(e).__name__}: {str(e)[:160]}')
        return {}


def _media(serie: dict, ate: str) -> float:
    v = [x['total']['metros'] for d, x in serie.items() if d[:7] == ate[:7] and d <= ate and x['total']['metros'] > 0]
    return round(sum(v) / len(v), 1) if v else 0.0


def reuniao(dia: date) -> dict:
    ch = dia.isoformat()
    avisos = []
    mal, aca = _seguro('Malharia', malharia_por_dia, avisos), _seguro('Acabamento', acabamento_por_dia, avisos)
    fat = _seguro('Faturamento', faturamento_por_dia, avisos, dia.year, dia.month)
    m, a = mal.get(ch) or _fechar(_vazio()), aca.get(ch) or _fechar(_vazio())
    f = fat.get(ch) or dict(metros=0, valor=0, ticket=0)
    tm = sum(x['metros'] for x in fat.values())
    tv = sum(x['valor'] for x in fat.values())
    return dict(
        data=ch, semana=dia.isocalendar()[1],
        malharia=dict(**m, media=_media(mal, ch)),
        acabamento=dict(**a, media=_media(aca, ch)),
        faturamento=dict(metros=f['metros'], ticket_dia=f['ticket'], ticket_mes=round(tv / tm, 3) if tm else 0,
                         pct_prod=round(f['metros'] / m['total']['metros'] - 1, 4) if m['total']['metros'] else None,
                         pct_aca=round(f['metros'] / a['total']['metros'] - 1, 4) if a['total']['metros'] else None),
        reprova_cru=_seguro('Reprova Cru', reprova_cru_dia, avisos, dia) or dict(pecas=0, metros=0, kg=0, linhas=[], turnos=[]),
        atualizado=ESTADO['ultima'], erro=ESTADO['erro'], avisos=avisos)


def mensal(ano: int, mes: int) -> dict:
    ini = date(ano, mes, 1)
    fim = (date(ano + (mes == 12), mes % 12 + 1, 1))
    avisos = []
    mal, aca = _seguro('Malharia', malharia_por_dia, avisos), _seguro('Acabamento', acabamento_por_dia, avisos)
    fat = _seguro('Faturamento', faturamento_por_dia, avisos, ano, mes)
    dias = []
    d = ini
    while d < min(fim, date.today() + timedelta(days=1)):
        ch = d.isoformat()
        dias.append(dict(data=ch, malharia=mal.get(ch) or _fechar(_vazio()), acabamento=aca.get(ch) or _fechar(_vazio()), faturamento=fat.get(ch) or dict(metros=0, valor=0, ticket=0)))
        d += timedelta(days=1)
    return dict(ano=ano, mes=mes, dias=dias, avisos=avisos)


# ---------------------------------------------------------------- reprova cru (NCs do SharePoint, local 24)
DEFEITOS = {
    'R01': 'AGULHA CILINDRO', 'R02': 'BARRAMENTO DE FIO', 'R03': 'BARRAMENTO DE TINTURARIA', 'R04': 'CONTAMINAÇÃO DE FIO', 'R05': 'AGULHA DISCO',
    'R06': 'FIO PUXADO', 'R07': 'FIO QUEBRADO', 'R08': 'MALHA CORRIDA CILINDRO', 'R09': 'MALHA CORRIDA DISCO', 'R10': 'MANCHA DE TINTURARIA',
    'R11': 'RISCO DE AGULHA', 'R12': 'FIO ERRADO', 'R13': 'RISCO DE ÓLEO', 'R14': 'TONALIDADE ERRADA', 'R16': 'SOBREPOSTO', 'R18': 'MANCHA DE ÓLEO',
    'R19': 'ESTOURO', 'R20': 'PLATINA', 'R21': 'FALHA DE DESENHO', 'R23': 'AGULHA PICANDO', 'R25': 'ATUADOR', 'R26': 'AMASSADO',
    'R27': 'PILLING (BOLINHAS)', 'R28': 'REBOBINAR', 'R29': 'SUJEIRA', 'R30': 'FORA DE LARGURA', 'R31': 'RISCO NO VINCO', 'R32': 'PARADA DE MÁQUINA',
    'R34': 'ENCHIMENTO SAINDO NO VINCO', 'R35': 'QUEBRADURA', 'REGULAGEM': 'REGULAGEM'}
_TURNO_NC = {'A': 1, 'B': 2, 'C': 3}
_ult_nc = {'ts': 0.0, 'erro': None}


def _linha_nc(x: dict):
    import re
    d = str(x.get('data1') or '')[:10]
    if not re.match(r'\d\d/\d\d/\d{4}$', d):
        return None
    dia = datetime.strptime(d, '%d/%m/%Y').date()
    metros = _num(x.get('Metros2'))
    if dia.year < 2026 or not metros:
        return None
    codigos = [c for c in re.split(r'\s+', str(x.get('contagemR') or '').strip().upper()) if c]
    return dict(sp_id=int(x['_id']), data=dia, maquina=str(x.get('Maquina') or '').strip().lstrip('0') or None, artigo=str(x.get('field_7') or '').strip()[:30],
                op=str(x.get('Ordemdeproducao') or '').strip()[:20], cliente=str(x.get('field_8') or '').strip()[:120], metros=metros, kg=_num(x.get('Quilos2')) or 0.0,
                turno=_TURNO_NC.get(str(x.get('TURNO') or '').strip().upper()), codigos=','.join(codigos)[:80],
                defeito=DEFEITOS.get(codigos[0], codigos[0]) if codigos else '', emitente=str(x.get('field_2') or '')[:80],
                criado=datetime.fromisoformat(str(x['_criado']).replace('Z', '')) if x.get('_criado') else None)


def atualizar_reprova_cru(app) -> bool:
    """Le as NCs do SharePoint (so leitura) e atualiza a tabela local; so entram NCs de 2026 em diante com metros e data."""
    from app.indicadores import sharepoint
    if not sharepoint.configurado():
        _ult_nc['erro'] = 'SharePoint não configurado no .env: ' + ', '.join(sharepoint.faltando())
        return False
    try:
        linhas = [r for r in (_linha_nc(x) for x in sharepoint.itens(limite=30000)) if r]
        with app.app_context():
            existentes = {r.sp_id: r for r in IndReprovaCru.query.all()}
            for r in linhas:
                alvo = existentes.get(r['sp_id'])
                if alvo is None:
                    db.session.add(IndReprovaCru(**r))
                else:
                    for k, v in r.items():
                        setattr(alvo, k, v)
            vivos = {r['sp_id'] for r in linhas}
            for sp_id, alvo in existentes.items():
                if sp_id not in vivos:                              # NC apagada ou alterada para fora do criterio
                    db.session.delete(alvo)
            db.session.commit()
        _ult_nc.update(ts=time.time(), erro=None)
        _cache.clear()
        return True
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        _ult_nc['erro'] = str(e)[:200]
        return False


def _nc_dict(r) -> dict:
    return dict(data=r.data.isoformat(), maquina=r.maquina, artigo=r.artigo, op=r.op, cliente=r.cliente, metros=round(r.metros, 1), kg=round(r.kg, 2),
                turno=r.turno, codigos=r.codigos, defeito=r.defeito)


def reprova_cru_dia(dia: date) -> dict:
    rs = IndReprovaCru.query.filter_by(data=dia).order_by(IndReprovaCru.criado).all()
    return dict(pecas=len(rs), metros=round(sum(r.metros for r in rs), 1), kg=round(sum(r.kg for r in rs), 1), linhas=[_nc_dict(r) for r in rs],
                turnos=[dict(pecas=sum(1 for r in rs if r.turno == t), metros=round(sum(r.metros for r in rs if r.turno == t), 1), kg=round(sum(r.kg for r in rs if r.turno == t), 1)) for t in (1, 2, 3)])


def reprova_cru_mensal(ano: int, mes: int) -> dict:
    ini = date(ano, mes, 1)
    fim = date(ano + (mes == 12), mes % 12 + 1, 1)
    rs = IndReprovaCru.query.filter(IndReprovaCru.data >= ini, IndReprovaCru.data < fim).order_by(IndReprovaCru.data, IndReprovaCru.criado).all()
    por_def = defaultdict(lambda: [0, 0.0, 0.0])
    por_dia = defaultdict(lambda: [0, 0.0, 0.0])
    for r in rs:
        for alvo in (por_def[r.defeito or 'SEM CÓDIGO'], por_dia[r.data.isoformat()]):
            alvo[0] += 1; alvo[1] += r.metros; alvo[2] += r.kg
    return dict(ano=ano, mes=mes, pecas=len(rs), metros=round(sum(r.metros for r in rs), 1), kg=round(sum(r.kg for r in rs), 1),
                defeitos=sorted(({'defeito': k, 'pecas': v[0], 'metros': round(v[1], 1), 'kg': round(v[2], 1)} for k, v in por_def.items()), key=lambda x: -x['metros']),
                dias=[{'data': k, 'pecas': v[0], 'metros': round(v[1], 1), 'kg': round(v[2], 1)} for k, v in sorted(por_dia.items())],
                linhas=[_nc_dict(r) for r in rs], erro=_ult_nc['erro'])
