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
from datetime import date, datetime, time as hora_do_dia, timedelta

from . import api

BASE_URL = api.BASE_URL
OPS_URL = f'{BASE_URL}/api/totvsmoda/production-order/v2/orders/search'
MOV_URL = f'{BASE_URL}/api/totvsmoda/production/v2/movements'
TRX_URL = f'{BASE_URL}/api/totvsmoda/general/v2/transactions/search'
BATCH_URL = f'{BASE_URL}/api/totvsmoda/product/v2/batch/search'
BAIXA_FIO = 2000                                              # operacao: baixa do fio consumido na peca (uma transacao por peca, com data e hora)
EMPRESA = 1
LOCAL = 23
NOME_LOCAL = 'MALHARIA PESAGEM'
JANELA_DIAS = 28                                              # a data final da API nao inclui o proprio dia (vale 00:00), por isso busca ate fim + 1 dia
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
    vistos, unicos = set(), []
    for m in movs:                                                 # o mesmo movimento pode vir em duas janelas/consultas: conta uma vez so
        k = (m.get('movementDate'), m.get('sequence'), m.get('originLocationCode'), m.get('destinationLocationCode'), m.get('totalQuantity'), m.get('component'))
        if k not in vistos:
            vistos.add(k)
            unicos.append(m)
    movs = unicos
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


def _metragem_padrao(nome: str) -> bool:
    from app.totvs.artigos import nome_e_metragem_padrao
    return nome_e_metragem_padrao(nome)


def _campos_artigo(o: dict) -> dict:
    """Campos adicionais do produto da OP no TOTVS: GRAMATURA M2, LARGURA PADRAO e METRO IDEAL LOTE (= metro padrao do PCP Hub)."""
    def num(v):
        try:
            return float(str(v).replace(',', '.'))
        except (TypeError, ValueError):
            return None
    saida = {}
    for it in ((o or {}).get('items') or [])[:1]:
        for f in it.get('productAdditionalField') or []:
            nome = str(f.get('name') or '').upper()
            if nome.startswith('GRAMATURA'):
                saida['gramatura'] = num(f.get('value'))
            elif nome.startswith('LARGURA'):
                saida['largura'] = num(f.get('value'))
            elif _metragem_padrao(nome):
                saida['metro_ideal'] = num(f.get('value'))
    return saida


def _fator(desc: str):
    """kg por metro da peca: gramatura (g/m2) x largura (m) / 1000, do nome do artigo (ex.: 240G 2,20L)."""
    g = re.search(r'(\d{2,3})\s*G\b', desc or '', re.I)
    l = re.search(r'(\d+[.,]\d+)\s*L\b', desc or '', re.I)
    return int(g.group(1)) * float(l.group(1).replace(',', '.')) / 1000 if g and l else None


def _dt(txt):
    try:
        return datetime.fromisoformat(str(txt)[:19]) if txt else None
    except ValueError:
        return None


def baixas_do_banco(desde: date):
    """Baixas de fio ja guardadas no banco, no mesmo formato de buscar_baixas ({cliente: [dict(codigo, data, mudou, kg)]}).
    Devolve None se o banco ainda nao cobre o periodo (aí vale a busca na API)."""
    from sqlalchemy import func
    from app import db
    from app.models import BaixaFio
    primeiro = db.session.query(func.min(BaixaFio.mudou)).scalar()
    if not primeiro or primeiro.date() > desde:
        return None
    saida = {}
    for b in BaixaFio.query.filter(BaixaFio.mudou >= datetime.combine(desde, hora_do_dia(0, 0))).all():
        saida.setdefault(b.cliente_cod, []).append(dict(codigo=b.codigo, data=b.data, mudou=b.mudou, kg=b.kg or 0.0))
    return saida


def buscar_baixas(cli, clientes: list, desde: date) -> dict:
    """Transacoes 2000 (baixa de fio, uma por peca) dos clientes das OPs: {cliente: [dict(codigo, data, mudou, kg)]}."""
    saida = {}
    for i in range(0, len(clientes), 20):
        pag = 1
        while True:
            r = cli.post(TRX_URL, dict(branchCodeList=[EMPRESA], operationCodeList=[BAIXA_FIO], customerCodeList=clientes[i:i + 20],
                                       startChangeDate=f'{desde.isoformat()}T00:00:00', endChangeDate=f'{date.today().isoformat()}T23:59:59',
                                       page=pag, pageSize=500))
            if r.status_code != 200:
                raise RuntimeError(f'Baixas de fio: HTTP {r.status_code} {r.text[:160]}')
            j = r.json()
            for t in j.get('items') or []:
                kg = sum(_num(it.get('quantity')) for it in t.get('items') or [] if str(it.get('measuredUnit') or '').strip().upper() == 'KG')
                saida.setdefault(t.get('customerCode'), []).append(dict(codigo=t.get('transactionCode'), data=_data(t.get('transactionDate')),
                                                                         mudou=_dt(t.get('lastchangeDate')), kg=kg))
            if not j.get('hasNext'):
                break
            pag += 1
    return saida


def _buscar_movs(cli, ini: date, fim: date, ops=None) -> list:
    """Movimentos normais do ciclo 13 entre duas datas (paginado). Sem `ops`, traz as OPs de todos."""
    saida, pag = [], 1
    while True:
        corpo = dict(branchCodeList=[EMPRESA], cycleCodeList=[13], movementStartDate=ini.isoformat(), movementEndDate=(fim + timedelta(days=1)).isoformat(),
                     listNormalMovement=True, listFinalizationMovement=False, reversalType=2, page=pag, pageSize=500)
        if ops:
            corpo['productionOrderCodeList'] = ops
        r = cli.post(MOV_URL, corpo)
        if r.status_code != 200:
            raise RuntimeError(f'Movimentos: HTTP {r.status_code} {r.text[:160]}')
        j = r.json()
        saida += j.get('items') or []
        if not j.get('hasNext') or pag >= 400:
            return saida
        pag += 1


def _baixar_janela(cli, ini: date, fim: date) -> int:
    """Baixas de fio com lastchangeDate entre duas datas (inclusive): grava as novas e atualiza as que ja existem. Devolve quantas eram novas."""
    from app import db
    from app.models import BaixaFio
    existentes = {b.codigo: b for b in BaixaFio.query.filter(BaixaFio.mudou >= datetime.combine(ini - timedelta(days=1), hora_do_dia(0, 0)),
                                                              BaixaFio.mudou < datetime.combine(fim + timedelta(days=2), hora_do_dia(0, 0))).all()}
    novas, pag = 0, 1
    while True:
        r = cli.post(TRX_URL, dict(branchCodeList=[EMPRESA], operationCodeList=[BAIXA_FIO], startChangeDate=f'{ini.isoformat()}T00:00:00',
                                   endChangeDate=f'{(fim + timedelta(days=1)).isoformat()}T00:00:00', page=pag, pageSize=500))
        if r.status_code != 200:
            raise RuntimeError(f'Baixas de fio: HTTP {r.status_code} {r.text[:160]}')
        j = r.json()
        for t in j.get('items') or []:
            cod, mudou = t.get('transactionCode'), _dt(t.get('lastchangeDate'))
            if cod is None or not mudou:
                continue
            its = t.get('items') or []
            kg = round(sum(_num(i.get('quantity')) for i in its if str(i.get('measuredUnit') or '').strip().upper() == 'KG'), 3)
            comp = ','.join(sorted({str(i.get('productCode')) for i in its}))[:200]
            b = existentes.get(cod)
            if b is None:
                b = existentes[cod] = BaixaFio(codigo=cod)
                db.session.add(b)
                novas += 1
            b.data, b.mudou, b.cliente_cod, b.kg, b.composicao = _data(t.get('transactionDate')), mudou, t.get('customerCode'), kg, comp
        if not j.get('hasNext') or pag >= 80:
            break
        pag += 1
    db.session.commit()
    return novas


def sincronizar_baixas(cli) -> int:
    """Guarda no banco as baixas de fio (uma por peca pesada). A cada rodada: as ultimas (desde 2 dias antes da mais recente que ja temos)
    e mais um bloco de 15 dias do historico, indo para tras ate DIAS_HISTORICO — assim as pecas recentes aparecem na primeira rodada."""
    from sqlalchemy import func
    from app import db
    from app.models import BaixaFio
    hoje = date.today()
    ultimo = db.session.query(func.max(BaixaFio.mudou)).scalar()
    novas = _baixar_janela(cli, (ultimo.date() - timedelta(days=2)) if ultimo else hoje - timedelta(days=14), hoje)
    primeiro = db.session.query(func.min(BaixaFio.mudou)).scalar()
    limite = hoje - timedelta(days=DIAS_HISTORICO)
    if primeiro and primeiro.date() > limite:
        fim = primeiro.date() - timedelta(days=1)
        novas += _baixar_janela(cli, max(limite, fim - timedelta(days=14)), fim)
    ESTADO.update(baixas_total=BaixaFio.query.count(), baixas_mais_antiga=str(primeiro.date()) if primeiro else None)
    return novas


def _penal_dia(p, b) -> float:
    """Quanto a baixa foge do dia esperado da peca: a baixa costuma ser do dia do movimento ou do dia seguinte (0); fora disso pesa 1."""
    d = (b.mudou.date() - p.data).days
    return 0.0 if d in (0, 1) else 1.0


def casar_globalmente() -> int:
    """Segunda passada: liga as pecas 23->24 que ainda nao tem baixa a baixas livres, pelos fios (composicao) e pelo peso.
    A data do movimento no TOTVS pode ser de 1-2 dias antes da baixa, entao a baixa vale de 1 dia antes ate 2 dias depois.
    Para cada OP acha o par (fios, kg por metro) que mais pecas explicam e liga cada peca a baixa de peso mais exato (0,02%)."""
    from app import db
    from app.models import BaixaFio, PesagemMalharia
    hoje = date.today()
    pend = [p for p in PesagemMalharia.query.filter(PesagemMalharia.trx_codigo.is_(None)).all()
            if p.data and p.metros and p.metros > 0 and (hoje - p.data).days <= 30 and p.hora_origem not in ('vivo', 'visto')]
    if not pend:
        return 0
    usados = {c for (c,) in db.session.query(PesagemMalharia.trx_codigo).filter(PesagemMalharia.trx_codigo.isnot(None)).all()}
    menor = min(p.data for p in pend) - timedelta(days=1)
    livres = [b for b in BaixaFio.query.filter(BaixaFio.mudou >= datetime.combine(menor, hora_do_dia(0, 0))).all()
              if b.codigo not in usados and b.kg and b.kg > 0 and b.composicao and b.mudou]
    from app.models import PesagemResumo
    cliente_da_op = {r.op: r.cliente_cod for r in PesagemResumo.query.all()}
    por_dia = {}
    for b in livres:
        por_dia.setdefault(b.mudou.date(), []).append(b)

    def janela(p):
        # a baixa de fio de uma peca e do mesmo cliente da OP (sem o cliente da OP, nao arrisca ligar)
        cod = cliente_da_op.get(p.op)
        return [b for d in range(-1, 3) for b in por_dia.get(p.data + timedelta(days=d), []) if cod and b.cliente_cod == cod]

    por_op = {}
    for p in pend:
        por_op.setdefault(p.op, []).append(p)
    escolhas = []
    for op, ps in por_op.items():
        cont, razoes = {}, {}
        for p in ps:
            for b in janela(p):
                r = b.kg / p.metros
                k = (b.composicao, round(r / 0.0005))
                cont[k] = cont.get(k, 0) + 1
                razoes.setdefault(k, []).append(r)
        if not cont:
            continue
        pont = lambda k: cont[k] + cont.get((k[0], k[1] - 1), 0) + cont.get((k[0], k[1] + 1), 0)
        melhor = max(cont, key=pont)
        if pont(melhor) < max(2, 0.4 * len(ps)):
            continue
        perto = sorted(r for k2 in ((melhor[0], melhor[1] - 1), melhor, (melhor[0], melhor[1] + 1)) for r in razoes.get(k2, []))
        escolhas.append((pont(melhor) / len(ps), op, melhor[0], perto[len(perto) // 2]))
    novos = 0
    for _, op, comp, r_ok in sorted(escolhas, reverse=True):
        for p in sorted(por_op[op], key=lambda p: (p.data, p.sequencia or 0)):
            esperado = p.metros * r_ok
            cand = [b for b in janela(p) if b.composicao == comp and b.codigo not in usados and abs(b.kg - esperado) <= max(0.0002 * esperado, 0.004)]
            if cand:
                b = min(cand, key=lambda b: abs(b.kg - esperado) / esperado / 0.0002 + _penal_dia(p, b))
                usados.add(b.codigo)
                p.trx_codigo, p.hora_real, p.hora_origem, p.peso_kg = b.codigo, b.mudou, 'trx', b.kg
                novos += 1
    # terceira passada: o que sobrou, pelos kg/m que ja se conhecem de cada composicao de fios (pares ja ligados), com tolerancia de 0,05%
    ratios = {}
    cods = {b.codigo: b for b in BaixaFio.query.filter(BaixaFio.mudou >= datetime.combine(menor, hora_do_dia(0, 0))).all()}
    for p in PesagemMalharia.query.filter(PesagemMalharia.trx_codigo.isnot(None), PesagemMalharia.metros > 0).all():
        b = cods.get(p.trx_codigo)
        if b and b.composicao and b.kg:
            ratios.setdefault(b.composicao, []).append(b.kg / p.metros)
    ratios = {c: sorted({round(r, 4) for r in rs}) for c, rs in ratios.items()}
    restantes = [p for p in pend if p.trx_codigo is None]
    pares = []
    for p in restantes:
        for b in janela(p):
            if b.codigo in usados or b.composicao not in ratios:
                continue
            r = b.kg / p.metros
            erro = min(abs(r - k) / k for k in ratios[b.composicao])
            if erro <= 0.0005:
                pares.append((erro + 0.0003 * _penal_dia(p, b), p.id, b.codigo, p, b))
    pares.sort(key=lambda x: x[0])
    feitos = set()
    for erro, pid, cod, p, b in pares:
        if pid in feitos or cod in usados:
            continue
        feitos.add(pid)
        usados.add(cod)
        p.trx_codigo, p.hora_real, p.hora_origem, p.peso_kg = b.codigo, b.mudou, 'trx', b.kg
        novos += 1
    # quarta passada: o que ainda sobrou, pelo kg/m da propria OP (mediana das pecas dela ja ligadas), tolerancia de 0,5%
    por_op_ratio = {}
    for p in PesagemMalharia.query.filter(PesagemMalharia.trx_codigo.isnot(None), PesagemMalharia.metros > 0).all():
        b = cods.get(p.trx_codigo)
        if b and b.kg:
            por_op_ratio.setdefault(p.op, []).append(b.kg / p.metros)
    ratio_op = {op: sorted(rs)[len(rs) // 2] for op, rs in por_op_ratio.items() if len(rs) >= 2}
    pares = []
    for p in pend:
        if p.trx_codigo is not None or p.op not in ratio_op:
            continue
        for b in janela(p):
            if b.codigo in usados:
                continue
            erro = abs(b.kg / p.metros - ratio_op[p.op]) / ratio_op[p.op]
            if erro <= 0.005:
                pares.append((erro + 0.002 * _penal_dia(p, b), p.id, b.codigo, p, b))
    pares.sort(key=lambda x: x[0])
    feitos = set()
    for erro, pid, cod, p, b in pares:
        if pid in feitos or cod in usados:
            continue
        feitos.add(pid)
        usados.add(cod)
        p.trx_codigo, p.hora_real, p.hora_origem, p.peso_kg = b.codigo, b.mudou, 'trx', b.kg
        novos += 1
    db.session.commit()
    return novos


def ligar_baixas() -> None:
    """Liga cada baixa ao movimento 23->24 que ja casou com ela (OP, maquina, metros) e estima o artigo das demais pela composicao de fios."""
    from collections import Counter
    from app import db
    from app.models import BaixaFio, PesagemMalharia, PesagemResumo, Sequencia
    resumos = {r.op: r for r in PesagemResumo.query.all()}
    clientes = {}
    for sq in Sequencia.query.all():
        clientes.setdefault(str(sq.op), sq.cliente)
    por_cod = {p.trx_codigo: p for p in PesagemMalharia.query.filter(PesagemMalharia.trx_codigo.isnot(None)).all()}
    baixas = BaixaFio.query.all()
    comp_padrao, comp_desc, cli_nome = {}, {}, {}
    for b in baixas:
        p = por_cod.get(b.codigo)
        if p and resumos.get(p.op):
            r = resumos[p.op]
            b.casada, b.op, b.metros, b.metro_padrao, b.artigo_desc, b.maquina = True, p.op, p.metros, r.metro_padrao or p.metros, r.artigo_desc, r.maquina
            b.gramatura, b.largura = r.gramatura, r.largura
            b.cliente_nome = clientes.get(p.op)
            comp_padrao.setdefault(b.composicao, Counter())[round(b.metro_padrao or 0)] += 1
            comp_desc.setdefault(b.composicao, Counter())[(b.artigo_desc, b.cliente_nome)] += 1
            if b.cliente_nome:
                cli_nome.setdefault(b.cliente_cod, Counter())[b.cliente_nome] += 1
        else:
            b.casada = False
    todos = sorted(v for c in comp_padrao.values() for v in c.elements() if v)
    mediana = todos[len(todos) // 2] if todos else None
    for b in baixas:
        if b.casada:
            continue
        # sem movimento ligado: o artigo e o metro padrao saem das outras pecas com os mesmos fios
        b.op, b.maquina = None, None
        c = comp_padrao.get(b.composicao)
        b.metro_padrao = b.metros = (c.most_common(1)[0][0] if c else mediana) or mediana
        d = comp_desc.get(b.composicao)
        b.artigo_desc, b.cliente_nome = d.most_common(1)[0][0] if d else (None, None)
        if not b.cliente_nome and b.cliente_cod in cli_nome:
            b.cliente_nome = cli_nome[b.cliente_cod].most_common(1)[0][0]
    db.session.commit()


def _hora_confiavel(data_trx: date, mudou: datetime) -> bool:
    """A baixa vale como hora da pesagem se foi feita dentro do dia de producao (06:20 do dia ate 06:19 do dia seguinte)."""
    ini = datetime.combine(data_trx, hora_do_dia(6, 20))
    return ini <= mudou < ini + timedelta(days=1)


def casar_horas(pecas: list, baixas: list, fator, usadas: set) -> dict:
    """Liga cada peca a sua baixa de fio (hora real da pesagem). Devolve {item: baixa}.
    1) Peca vista ao vivo (p['visto'] = momento em que o Gestao a viu aparecer): a baixa e a do mesmo cliente feita segundos antes
       (de 30 s depois ate 1 min antes), de peso proximo do esperado (+-4%). Isso separa baixas de pesos iguais de OPs parecidas.
    2) O resto, pelo peso: o peso da baixa e metros x kg/m, e o kg/m e o mesmo em todas as pecas do artigo (ex.: 0,5291 = 240 g/m2 x 2,20 m).
       Acha esse kg/m (o valor que mais se repete entre pecas e baixas de dias proximos; se o nome do artigo traz gramatura e largura,
       so vale perto do valor teorico) e liga cada peca a baixa de peso mais exato (0,02%), no mesmo dia de producao ou a 1 dia de diferenca."""
    ts = [b for b in baixas if b['codigo'] not in usadas and b['data'] and b['mudou'] and b['kg'] > 0]
    if not pecas or not ts:
        return {}
    por_data = {}
    for b in ts:
        por_data.setdefault(b['data'], []).append(b)

    def vizinhas(dia):
        return [b for d in (dia - timedelta(days=1), dia, dia + timedelta(days=1)) for b in por_data.get(d, [])]

    cont, razoes = {}, []
    for p in pecas:
        if p['metros'] <= 0:
            continue
        for b in vizinhas(_data(p['data'])):
            r = b['kg'] / p['metros']
            if fator is None or abs(r / fator - 1) <= 0.05:
                cont[round(r / 0.0005)] = cont.get(round(r / 0.0005), 0) + 1
                razoes.append(r)
    r_ok = None
    if cont:
        melhor = max(cont, key=lambda k: cont[k] + cont.get(k - 1, 0) + cont.get(k + 1, 0))
        if cont[melhor] + cont.get(melhor - 1, 0) + cont.get(melhor + 1, 0) >= max(2, 0.25 * len(pecas)):
            perto = sorted(r for r in razoes if abs(r / 0.0005 - melhor) <= 1.5)
            r_ok = perto[len(perto) // 2]                      # mediana: o kg/m exato (as baixas tem 3 casas, entao o erro e minimo)
    achados, usadas_aqui = {}, set()
    for p in sorted(pecas, key=lambda p: (str(p['data']), p.get('sequencia') or 0)):       # 1) pecas vistas ao vivo: baixa feita segundos antes
        v = p.get('visto')
        if not v or p['metros'] <= 0:
            continue
        ref = r_ok or fator
        esperado = p['metros'] * ref if ref else None
        cand = [b for b in ts if b['codigo'] not in usadas_aqui and -30 <= (v - b['mudou']).total_seconds() <= 60
                and (esperado is None or abs(b['kg'] - esperado) <= 0.04 * esperado)]
        if esperado is None and len(cand) != 1:
            continue
        if cand:
            b = min(cand, key=lambda b: abs((v - b['mudou']).total_seconds() - 8))
            achados[p['item']] = dict(b, ao_vivo=True)
            usadas_aqui.add(b['codigo'])
    if r_ok is None:
        return achados
    for p in sorted(pecas, key=lambda p: (str(p['data']), p.get('sequencia') or 0)):       # 2) o resto, pelo peso
        if p['item'] in achados or p.get('visto'):                                          # peca vista ao vivo sem baixa: nao arrisca pelo peso
            continue
        esperado = p['metros'] * r_ok
        cand = [b for b in vizinhas(_data(p['data'])) if b['codigo'] not in usadas_aqui and abs(b['kg'] - esperado) <= max(0.0002 * esperado, 0.004)]
        if cand:
            b = min(cand, key=lambda b: abs(b['kg'] - esperado))
            achados[p['item']] = b
            usadas_aqui.add(b['codigo'])
    return achados


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
                                       movementStartDate=ini.isoformat(), movementEndDate=(fim + timedelta(days=1)).isoformat(),
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
        resumo=dict(pecas=len(lista), metros=round(sum(l['metros'] or 0 for l in lista), 3),
                    no_local=round(no_local, 3), saiu=round(saiu, 3), quantidade_op=_num(o.get('quantity')),
                    atualizado_em=datetime.now().isoformat(timespec='seconds')))
    _cache[chave] = (time.time(), resultado)
    return resultado


# ---------------------------------------------------------------------------
# Atualizacao automatica: todas as OPs da Programacao, de uma vez, a cada MOVIMENTOS_INTERVALO_SEG (padrao 10 s)
# ---------------------------------------------------------------------------
import os
import re
import traceback

INTERVALO_SEG = float(os.getenv('MOVIMENTOS_INTERVALO_SEG', '10'))     # a busca leva ~3 s, entao 10 s e seguro
DIAS_HISTORICO = int(os.getenv('MOVIMENTOS_DIAS', '150'))
DIAS_DESCOBERTA = int(os.getenv('MOVIMENTOS_DIAS_DESCOBERTA', '60'))   # historico (dias) para achar OPs fora da Programacao
ESTADO = dict(rodando=False, ultima=None, erro=None, ops=0, pecas=0, duracao=None)
_trava_auto = threading.Lock()
_ULTIMA_BUSCA_ANTIGAS = 0.0
_DESC_OPS = set()                                             # OPs fora da Programacao que ja pesaram no local 23 (achadas na varredura completa)
_DESC_EM = 0.0                                                # quando foi a ultima varredura completa
DESCOBERTA_COMPLETA_SEG = int(os.getenv('MOVIMENTOS_DESCOBERTA_SEG', '900'))
_ULT_CICLO = 0.0                                              # fim do ultimo ciclo completo: peca nova so vale como 'vista ao vivo' se o ciclo anterior foi ha pouco


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
    """Onde a OP esta no TOTVS: local 23 = MALHARIA, 34 = ESTOQUE, local 22 ou status Em andamento = PCP - Fase², aguardando liberacao (sem movimento) = PCP - Fase¹.
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
    if o.get('status') == 20:
        return FASE2                                               # status 20 do TOTVS: em andamento (ainda sem material no local 22)
    return FASE1                                                   # status 10 do TOTVS: aguardando liberacao


def _sincronizar_quantidade(s, o: dict) -> bool:
    """A quantidade da OP no TOTVS manda: se alguem aumentou/diminuiu a quantidade da OP (ex.: 20 -> 24 pecas), a Programacao acompanha.
    qt_solicitada = quantidade da OP (m); total de pecas = quantidade / metro padrao (METRO IDEAL LOTE do artigo). Variacao absurda (fora de 0,2x a 5x) e ignorada."""
    qtd = _num(o.get('quantity'))
    if qtd <= 0:
        return False
    antigo = s.qt_solicitada or 0
    if antigo and not 0.2 <= qtd / antigo <= 5:
        return False
    metro = _campos_artigo(o).get('metro_ideal') or _metro_padrao(s)      # calculado antes de mexer nos valores
    mudou = False
    if int(round(qtd)) != antigo:
        s.qt_solicitada, mudou = int(round(qtd)), True
    if metro:
        pcs = int(round(qtd / metro))
        if pcs > 0 and pcs != (s.total_pcs or 0):
            s.total_pcs, mudou = pcs, True
    return mudou


_trava_sit = threading.Lock()                                  # o ciclo completo e a rodada rapida nao atualizam as situacoes ao mesmo tempo


def atualizar_situacoes(info: dict, pecas_por_op: dict) -> int:
    with _trava_sit:
        return _atualizar_situacoes(info, pecas_por_op)


def _atualizar_situacoes(info: dict, pecas_por_op: dict) -> int:
    """Define a situacao de cada OP da Programacao pelo local dela no TOTVS e faz a troca automatica:
    em cada maquina, a primeira OP da fila que esta na MALHARIA vira PRODUZINDO; quando a pesagem do cru dela termina, a proxima assume."""
    from app import db
    from app.models import HistoricoProducao, Sequencia
    por_maquina, alteradas = {}, 0
    for s in Sequencia.query.all():
        por_maquina.setdefault(s.machine_id, []).append(s)
    for lista in por_maquina.values():
        lista.sort(key=lambda s: (s.sequence_order if s.sequence_order is not None else 10 ** 6, s.id))
        produzindo = False
        for s in lista:
            o = info.get(str(s.op))
            if not o:
                continue                                           # OP que o TOTVS nao devolveu: nao mexe
            if _sincronizar_quantidade(s, o):
                alteradas += 1
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
    return alteradas


LIGACAO_VERSAO = '8'                                          # sobe quando a regra de ligar peca a baixa muda: as ligacoes recentes sao refeitas


def _refazer_ligacoes(app) -> None:
    """Quando a regra de ligacao muda, limpa as ligacoes peca-baixa dos ultimos 30 dias (uma vez) para serem refeitas pela regra nova."""
    import os
    from app import db
    from app.models import PesagemMalharia
    caminho = os.path.join(app.instance_path, 'ligacao_versao.txt')
    try:
        atual = open(caminho).read().strip()
    except OSError:
        atual = ''
    if atual == LIGACAO_VERSAO:
        return
    db.session.query(PesagemMalharia).filter(PesagemMalharia.data >= date.today() - timedelta(days=30)).update(
        dict(trx_codigo=None, hora_real=None, hora_origem=None, peso_kg=None), synchronize_session=False)
    db.session.commit()
    os.makedirs(app.instance_path, exist_ok=True)
    with open(caminho, 'w') as f:
        f.write(LIGACAO_VERSAO)
    print('[movimentos] ligacoes peca-baixa dos ultimos 30 dias refeitas pela regra nova', flush=True)


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
            _refazer_ligacoes(app)
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

                def buscar_info(lista_ops):
                    for i in range(0, len(lista_ops), 100):
                        r = cli.post(OPS_URL, dict(filter=dict(branchCodeList=[EMPRESA], cycleCodeList=[13], orderCodeList=[int(o) for o in lista_ops[i:i + 100]]),
                                                    expand='items,locations', pageSize=500, page=1))
                        if r.status_code != 200:
                            raise RuntimeError(f'OPs: HTTP {r.status_code} {r.text[:160]}')
                        for o in r.json().get('items') or []:
                            info[str(o.get('orderCode'))] = o
                global _DESC_OPS, _DESC_EM
                buscar_info(ops)
                conhecidas = sorted(_DESC_OPS - set(ops), key=int)              # OPs fora da Programacao ja descobertas (finalizadas etc.)
                if conhecidas:
                    buscar_info(conhecidas)
                    conhecidas = [o for o in conhecidas if o in info]
                for o in conhecidas:
                    try:                                                # metro padrao do artigo (Base Estrutura), como o PCP Hub usa
                        from app.main.routes import resolve_metros_from_article
                        padroes[o] = float(str(resolve_metros_from_article(((info[o].get('items') or [{}])[0].get('productName')) or '') or '').replace(',', '.')) or None
                    except ValueError:
                        padroes[o] = None
                    maquinas[o] = info[o].get('machineCode')
                hoje = date.today()
                inicio = min([_data(o.get('insertDate')) for o in info.values() if _data(o.get('insertDate'))] or [hoje - timedelta(days=60)])
                inicio = max(inicio, hoje - timedelta(days=DIAS_HISTORICO))
                movs, fim = [], hoje
                while True:
                    ini = max(inicio, fim - timedelta(days=JANELA_DIAS))
                    movs += _buscar_movs(cli, ini, fim, ops)
                    if ini <= inicio:
                        break
                    fim = ini - timedelta(days=1)
                # OPs que pesaram no local 23 mas nao estao (mais) na Programacao (ex.: ja finalizadas): descobertas pelos movimentos de todas as OPs
                fim2, lim2 = hoje, hoje - timedelta(days=DIAS_DESCOBERTA)       # OPs ja descobertas: filtradas por codigo (em blocos de 100), so os ultimos 60 dias
                while conhecidas and fim2 >= lim2:
                    ini2 = max(lim2, fim2 - timedelta(days=JANELA_DIAS))
                    for i in range(0, len(conhecidas), 100):
                        movs += _buscar_movs(cli, ini2, fim2, conhecidas[i:i + 100])
                    fim2 = ini2 - timedelta(days=1)
                # a varredura de 60 dias de TODAS as OPs e pesada (dezenas de milhares de movimentos): so a cada 15 min; nas rodadas de 10 s olha so os ultimos 2 dias
                completa = (not _DESC_OPS) or time.time() - _DESC_EM > DESCOBERTA_COMPLETA_SEG
                extras_movs, fim = [], hoje
                limite = hoje - timedelta(days=DIAS_DESCOBERTA if completa else 2)
                while fim >= limite:
                    ini = max(limite, fim - timedelta(days=JANELA_DIAS))
                    extras_movs += [m for m in _buscar_movs(cli, ini, fim) if str(m.get('productionOrder')) not in seqs and str(m.get('productionOrder')) not in _DESC_OPS]
                    fim = ini - timedelta(days=1)
                novas_ops = sorted({str(m.get('productionOrder')) for m in extras_movs
                                    if m.get('originLocationCode') == LOCAL and m.get('destinationLocationCode') == 24 and str(m.get('productionOrder')).isdigit()}, key=int)
                ESTADO.update(descoberta=dict(movs_sem_filtro=len(extras_movs), ops_novas=len(novas_ops), dias=DIAS_DESCOBERTA, movs_programacao=len(movs)))
                print(f'[movimentos] descoberta: {len(extras_movs)} movs de OPs fora da Programacao, {len(novas_ops)} OPs novas no local 23', flush=True)
                if novas_ops:
                    buscar_info(novas_ops)
                    novas_ops = [o for o in novas_ops if o in info]
                    movs += [m for m in extras_movs if str(m.get('productionOrder')) in novas_ops]
                    for o in novas_ops:
                        nome = (((info[o].get('items') or [{}])[0].get('productName')) or '')
                        try:                                           # metro padrao do artigo (Base Estrutura), como o PCP Hub usa
                            from app.main.routes import resolve_metros_from_article
                            padroes[o] = float(str(resolve_metros_from_article(nome) or '').replace(',', '.')) or None
                        except ValueError:
                            padroes[o] = None
                        maquinas[o] = info[o].get('machineCode')
                _DESC_OPS |= set(novas_ops)
                if completa:
                    _DESC_EM = time.time()
                ops = sorted(set(ops) | set(novas_ops) | set(conhecidas), key=int)

            por_op = {}
            for m in movs:
                por_op.setdefault(str(m.get('productionOrder')), []).append(m)
            agora = datetime.utcnow()
            # hora = quando o Gestao viu a peca (a API do TOTVS so tem a data). Guarda a de antes; pecas novas de OP ja conhecida ganham a hora de agora
            antigas_db = PesagemMalharia.query.all()
            # cada peca e identificada pelo proprio movimento (OP, data, sequencia, metros): o numero do item muda quando a ordem e refeita pela hora
            ident_db = lambda a: (a.op, a.data.isoformat() if a.data else '', a.sequencia, round(a.metros or 0, 3))
            antigas = {ident_db(a): a.visto_em for a in antigas_db}
            horas_antigas = {ident_db(a): (a.hora_real, a.hora_origem, a.trx_codigo) for a in antigas_db}
            for k, (h, o_, c) in list(horas_antigas.items()):         # regra da hora confiavel mudou: baixa a no maximo 1 dia do movimento vale
                if h and o_ == 'tardia' and abs((h.date() - _data(k[1])).days) <= 1:
                    horas_antigas[k] = (h, 'trx', c)
            pesos_antigos = {ident_db(a): a.peso_kg for a in antigas_db}
            pesos = {}
            trx_usadas = {a.trx_codigo for a in antigas_db if a.trx_codigo and (a.peso_kg or a.hora_origem != 'trx')}     # 'trx' sem peso e refeita (peso entrou depois)
            ops_conhecidas = {r.op for r in PesagemResumo.query.all()}
            agora_local = datetime.now()
            novas, resumos, total_pecas = [], [], 0
            listas = {op: calcular(por_op.get(op, []), op, padroes[op]) for op in ops}

            # hora real da pesagem: baixa de fio (transacao 2000) de cada peca. So busca para pecas ainda sem hora decidida
            pendentes = {}
            for op in ops:
                for l in listas[op][0]:
                    ch = (op, str(l['data']), l['sequencia'], round(l['metros'], 3))
                    if horas_antigas.get(ch, (None, None, None))[1] in (None, 'vivo') or (horas_antigas[ch][1] == 'trx' and not pesos_antigos.get(ch)):
                        pendentes.setdefault(op, []).append(l)
            horas, busca_ok, buscou_antigas = {}, False, False
            global _ULTIMA_BUSCA_ANTIGAS, _ULT_CICLO
            recentes = {op: [l for l in ls if (date.today() - _data(l['data'])).days <= 1] for op, ls in pendentes.items()}
            recentes = {op: ls for op, ls in recentes.items() if ls}
            antigas_pend = any((date.today() - _data(l['data'])).days > 1 for ls in pendentes.values() for l in ls)
            desde_pend = min([_data(l['data']) for ls in pendentes.values() for l in ls] or [date.today()]) - timedelta(days=1)
            do_banco = baixas_do_banco(desde_pend) if pendentes else None
            if do_banco is not None:
                alvo, buscou_antigas = pendentes, False               # baixas ja estao no banco: sem custo de API, tenta todas toda rodada
            elif antigas_pend and time.time() - _ULTIMA_BUSCA_ANTIGAS > 600:
                alvo, buscou_antigas = pendentes, True                # pecas mais velhas: tenta de novo so a cada 10 min (a busca e pesada)
            else:
                alvo = recentes                                        # pecas de hoje/ontem: toda rodada
            if alvo:
                try:
                    clientes_op = {op: (info.get(op) or {}).get('customerCode') for op in alvo}
                    desde = min(_data(l['data']) for ls in alvo.values() for l in ls) - timedelta(days=1)
                    if do_banco is not None:
                        baixas = do_banco
                    else:
                        with _trava:
                            baixas = buscar_baixas(cli, sorted({c for c in clientes_op.values() if c}), desde)
                    for op, ls in alvo.items():
                        for l in ls:                                       # peca vista agora (ao vivo): guarda o momento para escolher a baixa certa entre pesos iguais
                            ch_ = (op, str(l['data']), l['sequencia'], round(l['metros'], 3))
                            if ch_ not in antigas:                                         # peca nova: vale como 'ao vivo' se o ciclo anterior foi ha pouco
                                l['visto'] = agora_local if op in ops_conhecidas and time.time() - _ULT_CICLO <= 90 else None
                            elif horas_antigas.get(ch_, (None, None, None))[1] == 'vivo' and antigas.get(ch_) and 0 <= (agora_local - antigas[ch_]).total_seconds() <= 900:
                                l['visto'] = antigas[ch_]                  # ainda dentro dos 15 min em que a baixa pode aparecer
                            else:
                                l['visto'] = None
                        achados = casar_horas(ls, baixas.get(clientes_op[op], []), _fator(((info.get(op) or {}).get('items') or [{}])[0].get('productName')), trx_usadas)
                        for l in ls:
                            b = achados.get(l['item'])
                            if not b and l.get('visto'):
                                ch_ = (op, str(l['data']), l['sequencia'], round(l['metros'], 3))
                                horas[ch_] = (None, 'vivo' if (agora_local - l['visto']).total_seconds() <= 900 else 'visto', None)
                            if b:
                                trx_usadas.add(b['codigo'])
                                ok = b.get('ao_vivo') or abs((b['mudou'].date() - _data(l['data'])).days) <= 1      # baixa feita bem depois do movimento nao vale como hora da pesagem (a vista ao vivo vale sempre)
                                horas[(op, str(l['data']), l['sequencia'], round(l['metros'], 3))] = (b['mudou'], 'trx' if ok else 'tardia', b['codigo'])
                                pesos[(op, str(l['data']), l['sequencia'], round(l['metros'], 3))] = b['kg']
                    busca_ok = True
                    if buscou_antigas:
                        _ULTIMA_BUSCA_ANTIGAS = time.time()
                except Exception:  # noqa: BLE001  - sem a hora real a peca continua aparecendo (com a hora em que o Gestao a viu)
                    traceback.print_exc()
            for op in ops:
                o = info.get(op)
                lista, saiu = listas[op]
                total_pecas += len(lista)
                resolvidas = []
                for l in lista:
                    chave = (op, str(l['data']), l['sequencia'], round(l['metros'], 3))
                    visto = antigas.get(chave) if chave in antigas else (agora_local if op in ops_conhecidas else None)
                    h_real, h_origem, h_trx = horas.get(chave) or horas_antigas.get(chave, (None, None, None))
                    if h_origem is None and busca_ok and buscou_antigas and (date.today() - _data(l['data'])).days >= 2:
                        h_origem = 'sem'                       # passou o prazo de a baixa aparecer: nao procura mais
                    resolvidas.append((l, visto, h_real, h_origem, h_trx))
                # numero do item = ordem da pesagem (como o Relatorio 0032): pela hora real; peca sem hora fica logo depois da anterior na ordem do movimento
                tempos, ultimo = [], None
                for l, visto, h_real, h_origem, h_trx in resolvidas:
                    if h_real and h_origem == 'trx':
                        ultimo = h_real
                    tempos.append(ultimo)
                primeiro = next((t for t in tempos if t), datetime.min)
                ordem = sorted(range(len(resolvidas)), key=lambda i: ((tempos[i] or primeiro), i))
                for n, i in enumerate(ordem, 1):
                    l, visto, h_real, h_origem, h_trx = resolvidas[i]
                    novas.append(PesagemMalharia(op=op, ciclo=13, item=n, data=_data(l['data']), sequencia=l['sequencia'],
                                                 acerto=l['acerto'], metros=l['metros'], visto_em=visto, hora_real=h_real, hora_origem=h_origem,
                                                 trx_codigo=h_trx, synced_at=agora,
                                                 peso_kg=pesos.get((op, str(l['data']), l['sequencia'], round(l['metros'], 3))) or pesos_antigos.get((op, str(l['data']), l['sequencia'], round(l['metros'], 3)))))
                resumos.append(PesagemResumo(op=op, ciclo=13, encontrada=bool(o), maquina=maquinas[op] if maquinas[op] is not None else (o or {}).get('machineCode'),
                                             artigo_desc=(((o or {}).get('items') or [{}])[0].get('productName') or '')[:200] or None,
                                             gramatura=_campos_artigo(o).get('gramatura'), largura=_campos_artigo(o).get('largura'),
                                             metro_ideal=_campos_artigo(o).get('metro_ideal'),
                                             metro_padrao=_campos_artigo(o).get('metro_ideal') or padroes[op], cliente_cod=(o or {}).get('customerCode'), pecas=len(lista),
                                             metros=round(sum(l['metros'] or 0 for l in lista), 3),
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
            ESTADO.update(qtd_alteradas=atualizar_situacoes(info, {r.op: r.pecas for r in resumos}))
            try:                                                   # pecas = baixas de fio (uma por peca pesada, com hora e kg exatos)
                with _trava:
                    novas_baixas = sincronizar_baixas(cli)
                ESTADO.update(casadas_pela_composicao=casar_globalmente())
                ligar_baixas()
                ESTADO.update(baixas_novas=novas_baixas, erro_baixas=None)
            except Exception as e_baixas:  # noqa: BLE001
                traceback.print_exc()
                db.session.rollback()
                ESTADO.update(erro_baixas=str(e_baixas)[:300])
        _ULT_CICLO = time.time()
        ESTADO.update(ultima=datetime.now().isoformat(timespec='seconds'), ops=len(ops), pecas=total_pecas)
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        ESTADO.update(erro=str(e)[:300])
    finally:
        ESTADO.update(rodando=False, duracao=round(time.time() - t0, 1))
        _trava_auto.release()
    return ESTADO['erro'] is None


SITUACAO_SEG = int(os.getenv('MOVIMENTOS_SITUACAO_SEG', '20'))
_cli_rapido = None
_ULT_FILA = 0.0
FILA_AUTO_DIAS = int(os.getenv('FILA_AUTO_DIAS', '30'))        # OPs 'aguardando liberacao' criadas nos ultimos N dias vao para a Pre-Programacao


def fila_automatica(cli) -> int:
    """OP gerada no TOTVS (PCPFP141) fica 'Aguardando liberacao' (status 10): entra sozinha na Pre-Programacao > Analisar sequencia
    (fila WAITING), a menos que ja esteja numa maquina da Programacao ou na fila. Quando a OP chega numa maquina, o item da fila vira MOVED."""
    from app import db
    from app.main.routes import normalize_client_name, resolve_metros_from_article
    from app.models import ArtigoTotvs, FilaPreProgramacao, Sequencia
    em_maquina = {str(x.op): x for x in Sequencia.query.all() if x.op}
    # itens da fila ja resolvidos pela Programacao
    agora, movidas = datetime.utcnow(), 0
    for f in FilaPreProgramacao.query.filter_by(status='WAITING').all():
        sq = em_maquina.get(str(f.op))
        if sq and sq.machine:
            f.status, f.moved_to_machine, f.moved_at = 'MOVED', str(sq.machine.number), agora
            movidas += 1
    na_fila = {str(f.op) for f in FilaPreProgramacao.query.all() if f.op}
    limite = date.today() - timedelta(days=FILA_AUTO_DIAS)
    candidatas, pag = [], 1
    while True:
        r = cli.post(OPS_URL, dict(filter=dict(branchCodeList=[EMPRESA], cycleCodeList=[13], statusList=[10]), expand='items', pageSize=500, page=pag))
        if r.status_code != 200:
            raise RuntimeError(f'Fila automatica: HTTP {r.status_code} {r.text[:160]}')
        j = r.json()
        for o in j.get('items') or []:
            op = str(o.get('orderCode'))
            ins = _data(o.get('insertDate'))
            if op in em_maquina or op in na_fila or (ins and ins < limite) or _num(o.get('quantity')) <= 0:
                continue
            candidatas.append(o)
        if not j.get('hasNext'):
            break
        pag += 1
    novas = 0
    if candidatas:
        from app.totvs.artigos import codigos_de_artigo
        cod_artigo = codigos_de_artigo(cli, [(o.get('items') or [{}])[0].get('productCode') for o in candidatas])      # productCode -> codigo do artigo
        for o in candidatas:
            op = str(o.get('orderCode'))
            it = (o.get('items') or [{}])[0]
            artigo = cod_artigo.get(str(it.get('productCode'))) or str(it.get('productCode') or '')
            art = ArtigoTotvs.query.filter_by(cod_produto=artigo).first() if artigo else None
            metro = _campos_artigo(o).get('metro_ideal')
            if not metro:
                try:
                    metro = float(str(resolve_metros_from_article(artigo) or '').replace(',', '.'))
                except ValueError:
                    metro = 0
            qtd = _num(o.get('quantity'))
            db.session.add(FilaPreProgramacao(
                op=op, artigo=artigo, cliente=normalize_client_name(o.get('customerName') or ''), requested_quantity=qtd,
                total_pcs=int(round(qtd / metro)) if metro else 0, status='WAITING', cod_segmento=(art.segmento if art else None),
                created_at=agora, synced_at=agora))
            na_fila.add(op)
            novas += 1
    if novas or movidas:
        db.session.commit()
    return novas


def situacoes_rapido(app) -> int:
    """Rodada leve: so le o cabecalho das OPs da Programacao (status, locais, quantidade) e atualiza a situacao (PCP Fase¹/Fase², MALHARIA...) e a quantidade.
    O ciclo completo (pesagens, baixas) e demorado; assim uma OP que muda de local no TOTVS aparece aqui em poucos segundos."""
    from app import db
    from app.models import Sequencia
    with app.app_context():
        try:
            ops = sorted({int(x.op) for x in Sequencia.query.all() if x.op and str(x.op).isdigit() and (x.status or '') in ('PRODUZINDO', 'EM_SEQUENCIA', 'PCP')})
            global _cli_rapido, _ULT_FILA
            if _cli_rapido is None:                          # cliente proprio: o ciclo completo segura o _cliente() por minutos
                _cli_rapido = api.TotvsModaClient()
                _cli_rapido.autenticar()
            cli = _cli_rapido
            n = 0
            if ops:
                info = {}
                for i in range(0, len(ops), 100):
                    r = cli.post(OPS_URL, dict(filter=dict(branchCodeList=[EMPRESA], cycleCodeList=[13], orderCodeList=ops[i:i + 100]), expand='items,locations', pageSize=500, page=1))
                    if r.status_code != 200:
                        raise RuntimeError(f'OPs: HTTP {r.status_code} {r.text[:160]}')
                    for o in r.json().get('items') or []:
                        info[str(o.get('orderCode'))] = o
                n = atualizar_situacoes(info, {})
            if time.time() - _ULT_FILA >= 55:               # OPs novas 'aguardando liberacao' -> Pre-Programacao, a cada ~1 min
                ESTADO.update(fila_auto_novas=fila_automatica(cli))
                _ULT_FILA = time.time()
            try:
                from app.previsao import calcular as calcular_previsoes
                ESTADO.update(previsao_ops=calcular_previsoes())      # termino pelas voltas/RPM, acompanha a fila e as pecas produzidas
            except Exception as e_prev:  # noqa: BLE001
                db.session.rollback()
                ESTADO.update(previsao_erro=str(e_prev)[:300])
            ESTADO.update(situacao_ultima=datetime.now().isoformat(timespec='seconds'), situacao_erro=None)
            return n
        except Exception as e:  # noqa: BLE001
            db.session.rollback()
            ESTADO.update(situacao_erro=str(e)[:300])
            return 0


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

    def loop_situacao():
        time.sleep(30)
        while True:
            try:
                situacoes_rapido(app)
            except Exception:  # noqa: BLE001
                traceback.print_exc()
            time.sleep(max(SITUACAO_SEG, 5))
    threading.Thread(target=loop_situacao, daemon=True, name='totvs-situacoes').start()
