"""Paineis (dashboards) da aba Comercial ME.

Cada painel e calculado na hora a partir das tabelas importadas das planilhas
(app/seed_data/comercial_me) e devolve um JSON generico: filtros, indicadores (kpis),
graficos e tabelas. A tela so desenha o que vem daqui.

Convencoes das planilhas:
  * "DIAS ATRASO vs IDEAL": negativo = a data estimada pelo PCP passou da data ideal (atraso).
  * "TOTAL DUE BALANCE": negativo = valor ainda a receber do cliente.
  * Faturamento (base de origem) e em R$; pro-formas e credito sao em US$.
"""
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import date, timedelta

from .dados import carregar, catalogo_completo, existe

MESES = {
    'pt': ['Jan', 'Fev', 'Mar', 'Abr', 'Mai', 'Jun', 'Jul', 'Ago', 'Set', 'Out', 'Nov', 'Dez'],
    'es': ['Ene', 'Feb', 'Mar', 'Abr', 'May', 'Jun', 'Jul', 'Ago', 'Sep', 'Oct', 'Nov', 'Dic'],
}


# ----------------------------------------------------------------- auxiliares

class Ctx:
    def __init__(self, lang, filtros):
        self.lang = 'es' if lang == 'es' else 'pt'
        self.f = filtros or {}
        self.hoje = date.today()

    def T(self, pt, es):
        return es if self.lang == 'es' else pt

    def mes(self, ym):  # '2026-03' -> 'Mar/26'
        a, m = ym.split('-')
        return f"{MESES[self.lang][int(m) - 1]}/{a[2:]}"

    def data(self, d):
        return d.strftime('%d/%m/%Y') if d else ''


def _d(v):
    m = re.match(r'(\d{2})/(\d{2})/(\d{4})', str(v or ''))
    if not m:
        return None
    try:
        return date(int(m[3]), int(m[2]), int(m[1]))
    except ValueError:
        return None


def _n(v):
    if isinstance(v, bool):
        return 0
    if isinstance(v, (int, float)):
        return v
    if isinstance(v, str):  # numeros digitados como texto: '1.500', '2.093,5'
        t = v.strip()
        if re.fullmatch(r'-?\d{1,3}(\.\d{3})+(,\d+)?', t):
            return float(t.replace('.', '').replace(',', '.'))
        if re.fullmatch(r'-?\d+(,\d+)?', t):
            return float(t.replace(',', '.'))
    return 0


def _int(v):
    try:
        return int(float(str(v).replace(',', '.')))
    except (TypeError, ValueError):
        return None


def _chave(s):
    s = unicodedata.normalize('NFKD', str(s or '')).encode('ascii', 'ignore').decode().upper()
    return re.sub(r'[^A-Z0-9]', '', s)


def _limpo(s):
    return re.sub(r'\s+', ' ', str(s or '')).strip()


class Nomes:
    """Une grafias diferentes do mesmo nome (maiusculas, pontos, espacos) e escolhe a mais comum."""

    def __init__(self):
        self._c = defaultdict(Counter)

    def k(self, s):
        k = _chave(s)
        if k:
            self._c[k][_limpo(s)] += 1
        return k

    def nome(self, k):
        return self._c[k].most_common(1)[0][0] if self._c[k] else k


def _pais(s):
    t = _limpo(s).replace('*', '').strip()
    if not t or t.upper() in ('NADA', '#N/A', 'NA'):
        return None
    return t.title()


def _idx(d):
    return {n.upper(): i for i, n in enumerate(d['colunas'])}


def _c(ix, prefixo):
    p = prefixo.upper()
    if p in ix:
        return ix[p]
    for nome, i in ix.items():
        if nome.startswith(p):
            return i
    raise KeyError(prefixo)


def _tab(id_):
    d = carregar(id_)
    return _idx(d), d['linhas']


def _soma(itens, chave, valor):
    out = defaultdict(float)
    for it in itens:
        k = chave(it)
        if k is not None:
            out[k] += valor(it)
    return out


def _top(dic, n=10):
    return sorted(((k, v) for k, v in dic.items() if v), key=lambda x: -x[1])[:n]


def _sufixo_ano(c, atual):
    return str(atual)


# ------------------------------------------- bases: frequencia de atualizacao (Mary)

_ATUALIZACAO_ES = {'Diária': 'Diaria', 'Mensal': 'Mensual', 'Quando necessário': 'Cuando sea necesario'}


def _dias_uteis(de, ate):
    n, d = 0, de
    while d < ate:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return n


def fontes(c, ids):
    """Uma linha por planilha-base usada no painel: frequencia de atualizacao, metricas e se esta em dia."""
    cat = {t['id']: t for t in catalogo_completo()['tabelas']}
    vistos, out = set(), []
    for id_ in ids:
        t = cat.get(id_)
        if not t or t['arquivo'] in vistos:
            continue
        vistos.add(t['arquivo'])
        salvo = None
        if t.get('salvo_em'):
            try:
                salvo = date.fromisoformat(t['salvo_em'][:10])
            except ValueError:
                pass
        freq = t.get('atualizacao') or ''
        estado, dias = None, None
        if salvo:
            dias = (c.hoje - salvo).days
            if freq == 'Diária':
                estado = 'ok' if _dias_uteis(salvo, c.hoje) <= 1 else 'atrasada'
            elif freq == 'Mensal':
                estado = 'ok' if dias <= 35 else 'atrasada'
        out.append(dict(base=t.get('base') or t['arquivo'], atualizacao=_ATUALIZACAO_ES.get(freq, freq) if c.lang == 'es' else freq,
                        frequencia=freq, metricas=t.get('metricas') or '', salvo_em=c.data(salvo) if salvo else None, dias=dias, estado=estado,
                        arquivo=t['arquivo']))
    return out


# -------------------------------------------------------------- blocos da tela

def kpi(rotulo, valor, fmt='int', nota=None, tom=None, delta=None):
    return dict(rotulo=rotulo, valor=valor, fmt=fmt, nota=nota, tom=tom, delta=delta)


def grafico(id_, titulo, tipo, labels, series, fmt='usd', largura='meia', empilhado=False, cores=None, nota=None):
    return dict(id=id_, titulo=titulo, tipo=tipo, labels=labels, series=series, fmt=fmt,
                largura=largura, empilhado=empilhado, cores=cores, nota=nota)


def tabela(titulo, colunas, linhas, nota=None, vazio='—'):
    """colunas = [(nome, tipo)], tipo: texto | num | usd | brl | m | pct | dias | data | badge.
    Em 'badge' o valor da celula e [texto, tom] (tom: verde, ambar, vermelho, azul, cinza)."""
    return dict(titulo=titulo, colunas=[dict(nome=n, tipo=t) for n, t in colunas], linhas=linhas, nota=nota, vazio=vazio)


def _filtro(id_, rotulo, opcoes, valor):
    return dict(id=id_, rotulo=rotulo, opcoes=[dict(v=str(v), t=str(t)) for v, t in opcoes], valor=str(valor))


def _filtro_ano(c, anos, padrao):
    ano = c.f.get('ano')
    ano = padrao if ano is None else ano
    anos = sorted({a for a in anos if a}, reverse=True)
    valor = ano if (ano == '' or (str(ano).isdigit() and int(ano) in anos)) else padrao
    return _filtro('ano', c.T('Ano', 'Año'), [('', c.T('Todos', 'Todos'))] + [(a, a) for a in anos], valor), (int(valor) if valor else None)


def _filtro_cliente(c, nomes, chaves):
    atual = c.f.get('cliente') or ''
    ops = sorted(((k, nomes.nome(k)) for k in set(chaves)), key=lambda x: x[1].lower())
    if atual not in {k for k, _ in ops}:
        atual = ''
    return _filtro('cliente', c.T('Cliente', 'Cliente'), [('', c.T('Todos', 'Todos'))] + ops, atual), atual


def _filtro_pais(c, nomes, chaves):
    atual = c.f.get('pais') or ''
    ops = sorted(((k, nomes.nome(k)) for k in set(chaves)), key=lambda x: x[1].lower())
    if atual not in {k for k, _ in ops}:
        atual = ''
    return _filtro('pais', c.T('País', 'País'), [('', c.T('Todos', 'Todos'))] + ops, atual), atual


def _filtro_origem(c):
    atual = (c.f.get('origem') or '').upper()
    if atual not in ('BR', 'GT'):
        atual = ''
    return _filtro('origem', c.T('Origem', 'Origen'), [('', c.T('Brasil + Guatemala', 'Brasil + Guatemala')), ('BR', 'Brasil'), ('GT', 'Guatemala')], atual), atual


def _delta(atual, base):
    return round((atual - base) / base * 100, 1) if base else None


def _meses_ordenados(*dicts):
    return sorted({k for d in dicts for k in d})


def _status_cor(s):
    return {'verde': '#1f9d55', 'ambar': '#e0972f', 'vermelho': '#e5484d', 'azul': '#2f7fe0', 'cinza': '#9aa6bc'}[s]


# ============================================================== PEDIDOS (BR)

_STATUS_PROD = {
    'entregado': ('Entregue', 'Entregado', 'verde'), 'finalizado': ('Finalizado', 'Finalizado', 'verde'),
    'produzindo': ('Produzindo', 'Produciendo', 'azul'), 'programado': ('Programado', 'Programado', 'azul'),
    'não iniciado': ('Não iniciado', 'No iniciado', 'cinza'), 'coletar': ('A coletar', 'Por recoger', 'ambar'),
    'atraso': ('Atrasado', 'Atrasado', 'vermelho'), 'mat revenda': ('Material de revenda', 'Material de reventa', 'cinza'),
}


def _status_prod(v):
    k = _limpo(v).lower()
    return _STATUS_PROD.get(k) or (('Sem status', 'Sin estado', 'cinza') if not k else (_limpo(v), _limpo(v), 'cinza'))


def _pedidos_br(c):
    nomes = Nomes()
    ix, rows = _tab('br_detalhes')
    k = {n: _c(ix, p) for n, p in dict(pf='PRO-FORM NBR', cli='CLIENTE', pais='PAIS', dat='DATA PRO-FORMA', mts='MTS',
                                       usd='TOTAL US$', st='STATUS PRODU').items()}
    itens = []
    for r in rows:
        d = _d(r[k['dat']])
        if r[k['pf']] and r[k['cli']] and d:
            itens.append(dict(pf=_limpo(r[k['pf']]), cli=nomes.k(r[k['cli']]), pais=nomes.k(_pais(r[k['pais']]) or 'Não informado'),
                              data=d, mts=_n(r[k['mts']]), usd=_n(r[k['usd']]), st=_status_prod(r[k['st']])))
    fix, frows = _tab('br_followup')
    kf = {n: _c(fix, p) for n, p in dict(pf='PROFORM', cli='CLIENTE', ped='DATA PROFORMA', ideal='DATA IDEAL', totvs='NÚM. PEDIDO',
                                         mts='TOTAL METROS', est='DATA ESTIMADA', atr='DÍAS ATRASO', col='DATA COLETA',
                                         obs='OBSERVAÇÃO').items()}
    abertos, todos = [], []
    for r in frows:
        ideal = _d(r[kf['ideal']])
        if r[kf['cli']] and _d(r[kf['ped']]):
            todos.append(dict(cli=nomes.k(r[kf['cli']]), ped=_d(r[kf['ped']]), est=_d(r[kf['est']])))
        if r[kf['cli']] and ideal:
            est = _d(r[kf['est']])
            atraso = _int(r[kf['atr']])
            if atraso is None and est:
                atraso = (ideal - est).days
            abertos.append(dict(pf=_limpo(r[kf['pf']]), cli=nomes.k(r[kf['cli']]), ped=_d(r[kf['ped']]), ideal=ideal, est=est, atraso=atraso,
                                mts=_n(r[kf['mts']]), col=_limpo(r[kf['col']]), obs=_limpo(r[kf['obs']])))
    return nomes, itens, abertos, todos


def painel_pedidos_br(c):
    nomes, itens, abertos, todos = _pedidos_br(c)
    f_ano, ano = _filtro_ano(c, [i['data'].year for i in itens], str(max((i['data'].year for i in itens), default='')))
    f_cli, cli = _filtro_cliente(c, nomes, [i['cli'] for i in itens])
    sel = [i for i in itens if (not ano or i['data'].year == ano) and (not cli or i['cli'] == cli)]
    ab = [a for a in abertos if (not ano or (a['ped'] and a['ped'].year == ano)) and (not cli or a['cli'] == cli)]

    prod = [(t['est'] - t['ped']).days for t in todos if t['est'] and (not ano or t['ped'].year == ano) and (not cli or t['cli'] == cli)
            and 0 <= (t['est'] - t['ped']).days <= 400]
    com_coleta = [a for a in ab if _d(a['col'])]
    pfs = {i['pf'] for i in sel}
    usd, mts = sum(i['usd'] for i in sel), sum(i['mts'] for i in sel)
    atrasados = [a for a in ab if a['atraso'] is not None and a['atraso'] < 0]
    media_atraso = round(sum(-a['atraso'] for a in atrasados) / len(atrasados)) if atrasados else 0
    no_prazo = round((len(ab) - len(atrasados)) / len(ab) * 100, 1) if ab else None
    kpis = [
        kpi(c.T('Pro-formas', 'Pro-formas'), len(pfs), 'int', c.T('no período filtrado', 'en el período filtrado')),
        kpi(c.T('Valor das pro-formas', 'Valor de las pro-formas'), round(usd), 'usd', f"{len({i['cli'] for i in sel})} " + c.T('clientes', 'clientes')),
        kpi(c.T('Metros pedidos', 'Metros pedidos'), round(mts), 'm', c.T('soma dos itens', 'suma de los ítems')),
        kpi(c.T('Preço médio', 'Precio medio'), round(usd / mts, 2) if mts else 0, 'usd2', c.T('US$ por metro', 'US$ por metro')),
        kpi(c.T('Pedidos em aberto', 'Pedidos abiertos'), len(ab), 'int', f"{round(sum(a['mts'] for a in ab)):,}".replace(',', '.') + ' m', tom='azul'),
        kpi(c.T('Entregas atrasadas', 'Entregas atrasadas'), len(atrasados), 'int',
            c.T(f'média de {media_atraso} dias de atraso', f'promedio de {media_atraso} días de atraso') if atrasados else c.T('nenhuma', 'ninguna'),
            tom='vermelho' if atrasados else 'verde'),
        kpi(c.T('Entregas no prazo', 'Entregas a tiempo'), no_prazo, 'pct', c.T('dos pedidos em aberto', 'de los pedidos abiertos'),
            tom=('verde' if (no_prazo or 0) >= 70 else 'ambar') if no_prazo is not None else None),
        kpi(c.T('Média de produção', 'Promedio de producción'), round(sum(prod) / len(prod)) if prod else None, 'dias',
            c.T('do pedido à data estimada pelo PCP', 'del pedido a la fecha estimada por el PCP')),
        kpi(c.T('Coletas agendadas', 'Recolecciones programadas'), len(com_coleta), 'int',
            c.T(f'{len(ab) - len(com_coleta)} sem data (TBD / on hold)', f'{len(ab) - len(com_coleta)} sin fecha (TBD / on hold)'), tom='azul'),
    ]
    por_mes_usd = _soma(sel, lambda i: f"{i['data'].year}-{i['data'].month:02d}", lambda i: i['usd'])
    por_mes_mts = _soma(sel, lambda i: f"{i['data'].year}-{i['data'].month:02d}", lambda i: i['mts'])
    meses = _meses_ordenados(por_mes_usd)
    top_cli = _top(_soma(sel, lambda i: i['cli'], lambda i: i['usd']))
    por_pais = _top(_soma(sel, lambda i: i['pais'], lambda i: i['usd']), 8)
    st = Counter()
    for i in sel:
        st[(i['st'][0] if c.lang == 'pt' else i['st'][1], i['st'][2])] += 1
    st_ord = sorted(st.items(), key=lambda x: -x[1])
    abertos_cli = _top(_soma(ab, lambda a: a['cli'], lambda a: a['mts']), 8)
    graficos = [
        grafico('g_mes', c.T('Valor das pro-formas por mês (US$)', 'Valor de pro-formas por mes (US$)'), 'bar',
                [c.mes(m) for m in meses], [dict(nome='US$', dados=[round(por_mes_usd[m]) for m in meses])], 'usd'),
        grafico('g_mts', c.T('Metros pedidos por mês', 'Metros pedidos por mes'), 'line',
                [c.mes(m) for m in meses], [dict(nome='m', dados=[round(por_mes_mts[m]) for m in meses])], 'm'),
        grafico('g_cli', c.T('Top 10 clientes (US$)', 'Top 10 clientes (US$)'), 'hbar',
                [nomes.nome(k) for k, _ in top_cli], [dict(nome='US$', dados=[round(v) for _, v in top_cli])], 'usd'),
        grafico('g_pais', c.T('Valor por país (US$)', 'Valor por país (US$)'), 'doughnut',
                [nomes.nome(k) for k, _ in por_pais], [dict(nome='US$', dados=[round(v) for _, v in por_pais])], 'usd'),
        grafico('g_st', c.T('Situação de produção dos itens', 'Situación de producción de los ítems'), 'doughnut',
                [k[0] for k, _ in st_ord], [dict(nome=c.T('itens', 'ítems'), dados=[v for _, v in st_ord])], 'int',
                cores=[_status_cor(k[1]) for k, _ in st_ord]),
        grafico('g_aberto', c.T('Metros em aberto por cliente', 'Metros abiertos por cliente'), 'hbar',
                [nomes.nome(k) for k, _ in abertos_cli], [dict(nome='m', dados=[round(v) for _, v in abertos_cli])], 'm'),
    ]
    linhas_ab = []
    for a in sorted(ab, key=lambda a: (a['atraso'] if a['atraso'] is not None else 0, a['ideal'])):
        if a['atraso'] is None:
            sit = ['—', 'cinza']
        elif a['atraso'] < 0:
            sit = [c.T(f"{-a['atraso']} dias de atraso", f"{-a['atraso']} días de atraso"), 'vermelho']
        elif a['atraso'] == 0:
            sit = [c.T('No prazo', 'A tiempo'), 'verde']
        else:
            sit = [c.T(f"{a['atraso']} dias adiantado", f"{a['atraso']} días adelantado"), 'azul']
        if 'on hold' in (a['obs'] + ' ' + a['col']).lower():
            sit = ['On hold', 'ambar']
        linhas_ab.append([a['pf'], nomes.nome(a['cli']), round(a['mts']), a['ideal'].strftime('%d/%m/%Y'),
                          a['est'].strftime('%d/%m/%Y') if a['est'] else '', sit, a['col'], a['obs'][:110]])
    maiores = defaultdict(lambda: dict(usd=0, mts=0, cli=None, pais=None, data=None, st=None))
    for i in sel:
        m = maiores[i['pf']]
        m['usd'] += i['usd']; m['mts'] += i['mts']; m['cli'] = i['cli']; m['pais'] = i['pais']; m['data'] = i['data']
        if m['st'] is None or i['st'][0] != 'Sem status':
            m['st'] = i['st']
    linhas_top = [[pf, nomes.nome(m['cli']), nomes.nome(m['pais']), m['data'].strftime('%d/%m/%Y'), round(m['usd']), round(m['mts']),
                   [m['st'][0] if c.lang == 'pt' else m['st'][1], m['st'][2]]]
                  for pf, m in sorted(maiores.items(), key=lambda x: -x[1]['usd'])[:10]]
    tabelas = [
        tabela(c.T('Entregas em aberto (mais atrasadas primeiro)', 'Entregas abiertas (primero las más atrasadas)'),
               [(c.T('Pro-forma', 'Pro-forma'), 'texto'), (c.T('Cliente', 'Cliente'), 'texto'), (c.T('Metros', 'Metros'), 'm'),
                (c.T('Data ideal', 'Fecha ideal'), 'data'), (c.T('Estimada PCP', 'Estimada PCP'), 'data'), (c.T('Situação', 'Situación'), 'badge'),
                (c.T('Coleta', 'Recolección'), 'texto'), (c.T('Observação', 'Observación'), 'texto')],
               linhas_ab, nota=c.T('Atraso = data estimada pelo PCP depois da data ideal de entrega (30 dias após o pedido).',
                                   'Atraso = fecha estimada por el PCP posterior a la fecha ideal de entrega (30 días tras el pedido).'),
               vazio=c.T('Nenhum pedido em aberto neste filtro.', 'Ningún pedido abierto en este filtro.')),
        tabela(c.T('Maiores pro-formas do período', 'Mayores pro-formas del período'),
               [(c.T('Pro-forma', 'Pro-forma'), 'texto'), (c.T('Cliente', 'Cliente'), 'texto'), (c.T('País', 'País'), 'texto'),
                (c.T('Data', 'Fecha'), 'data'), ('US$', 'usd'), (c.T('Metros', 'Metros'), 'm'), (c.T('Produção', 'Producción'), 'badge')],
               linhas_top),
    ]
    return dict(titulo=c.T('Pedidos e entregas', 'Pedidos y entregas'), filtros=[f_ano, f_cli], kpis=kpis, graficos=graficos, tabelas=tabelas,
                fonte='PEDIDOS BR-FOLLOW-UP')


# ============================================================ CREDITO (BR)

def _credito(c):
    nomes = Nomes()
    ix, rows = _tab('cred_gestao')
    k = {n: _c(ix, p) for n, p in dict(inv='INVOICE', cli='CUSTOMER', ped='DATA PROFORM', venc='DATA VENCIMENTO', tot='TOTAL DUE PAYMENT',
                                       pago='ADVANCE PAYMENT', saldo='TOTAL DUE BALANCE', st='STATUS PGTO', termos='PAYMENT TERMS',
                                       obs='OBSERV').items()}
    out = []
    for r in rows:
        if not (r[k['inv']] and r[k['cli']]):
            continue
        out.append(dict(inv=_limpo(r[k['inv']]), cli=nomes.k(r[k['cli']]), ped=_d(r[k['ped']]), venc=_d(r[k['venc']]),
                        tot=_n(r[k['tot']]), pago=_n(r[k['pago']]), saldo=_n(r[k['saldo']]),
                        st=_limpo(r[k['st']]).lower() or 'sem status', termos=_limpo(r[k['termos']]), obs=_limpo(r[k['obs']])))
    return nomes, out


def _termos(t):
    t = t.lower()
    if 'pre' in t and ('50%' in t or '30%' in t or 'counter' in t or 'after' in t):
        return ('Misto (adiantado + saldo)', 'Mixto (anticipo + saldo)')
    if 'pre' in t:
        return ('Pré-pagamento', 'Pago anticipado')
    m = re.search(r'(\d+)\s*day', t)
    if m:
        return (f'{m[1]} dias', f'{m[1]} días')
    return ('Outros', 'Otros')


def painel_credito(c):
    nomes, inv = _credito(c)
    f_ano, ano = _filtro_ano(c, [i['ped'].year for i in inv if i['ped']], '')
    f_cli, cli = _filtro_cliente(c, nomes, [i['cli'] for i in inv])
    sel = [i for i in inv if (not ano or (i['ped'] and i['ped'].year == ano)) and (not cli or i['cli'] == cli)]
    hoje = c.hoje
    aberto = [i for i in sel if i['saldo'] < 0]
    a_receber = -sum(i['saldo'] for i in aberto)
    vencido = -sum(i['saldo'] for i in aberto if i['venc'] and i['venc'] < hoje)
    a_vencer30 = -sum(i['saldo'] for i in aberto if i['venc'] and hoje <= i['venc'] <= hoje + timedelta(days=30))
    total, pago = sum(i['tot'] for i in sel), sum(i['pago'] for i in sel)
    kpis = [
        kpi(c.T('Total em invoices', 'Total en invoices'), round(total), 'usd', f'{len(sel)} invoices'),
        kpi(c.T('Recebido', 'Cobrado'), round(pago), 'usd', f'{round(pago / total * 100) if total else 0}% ' + c.T('do total', 'del total'), tom='verde'),
        kpi(c.T('A receber (invoices em aberto)', 'Por cobrar (invoices abiertas)'), round(a_receber), 'usd',
            c.T(f'{len(aberto)} invoices · líquido de créditos dos clientes: US$ {round(-sum(i["saldo"] for i in sel)):,}'.replace(',', '.'),
                f'{len(aberto)} invoices · neto de créditos de clientes: US$ {round(-sum(i["saldo"] for i in sel)):,}'.replace(',', '.')), tom='azul'),
        kpi(c.T('Vencido', 'Vencido'), round(vencido), 'usd', c.T('saldo com vencimento já passado', 'saldo con vencimiento pasado'),
            tom='vermelho' if vencido else 'verde'),
        kpi(c.T('Vence nos próximos 30 dias', 'Vence en los próximos 30 días'), round(a_vencer30), 'usd', c.T('a receber', 'por cobrar'), tom='ambar'),
    ]
    rot_st = {'futura': ('Futura', 'Futura'), 'parcial': ('Pagamento parcial', 'Pago parcial'), 'pendente': ('Pendente', 'Pendiente'),
              'on hold': ('On hold', 'On hold'), 'nc': ('Nota de crédito', 'Nota de crédito'), 'paga': ('Paga', 'Pagada')}
    por_st = _top(_soma(aberto, lambda i: i['st'], lambda i: -i['saldo']))
    faixas = [(c.T('Vencido há mais de 90 dias', 'Vencido hace más de 90 días'), lambda d: d < -90, 'vermelho'),
              (c.T('Vencido 31–90 dias', 'Vencido 31–90 días'), lambda d: -90 <= d < -30, 'vermelho'),
              (c.T('Vencido 1–30 dias', 'Vencido 1–30 días'), lambda d: -30 <= d < 0, 'ambar'),
              (c.T('Vence em 0–30 dias', 'Vence en 0–30 días'), lambda d: 0 <= d <= 30, 'azul'),
              (c.T('Vence em 31–60 dias', 'Vence en 31–60 días'), lambda d: 30 < d <= 60, 'azul'),
              (c.T('Vence em 61+ dias', 'Vence en 61+ días'), lambda d: d > 60, 'azul')]
    vals = [0.0] * len(faixas)
    sem_data = 0.0
    for i in aberto:
        if not i['venc']:
            sem_data += -i['saldo']; continue
        dias = (i['venc'] - hoje).days
        for n, (_, fn, _) in enumerate(faixas):
            if fn(dias):
                vals[n] += -i['saldo']; break
    labels = [f[0] for f in faixas]; cores = [_status_cor(f[2]) for f in faixas]
    if sem_data:
        labels.append(c.T('Sem vencimento', 'Sin vencimiento')); vals.append(sem_data); cores.append(_status_cor('cinza'))
    por_cli = _top(_soma(aberto, lambda i: i['cli'], lambda i: -i['saldo']), 10)
    por_mes_t, por_mes_p = defaultdict(float), defaultdict(float)
    for i in sel:
        if i['ped']:
            m = f"{i['ped'].year}-{i['ped'].month:02d}"
            por_mes_t[m] += i['tot']; por_mes_p[m] += i['pago']
    meses = _meses_ordenados(por_mes_t)
    termos = Counter()
    for i in sel:
        if i['termos']:
            termos[_termos(i['termos'])[0 if c.lang == 'pt' else 1]] += i['tot']
    graficos = [
        grafico('g_aging', c.T('Saldo a receber por vencimento (US$)', 'Saldo por cobrar por vencimiento (US$)'), 'bar', labels,
                [dict(nome='US$', dados=[round(v) for v in vals])], 'usd', cores=cores),
        grafico('g_st', c.T('Saldo a receber por situação (US$)', 'Saldo por cobrar por situación (US$)'), 'doughnut',
                [rot_st.get(k, (k.capitalize(),) * 2)[0 if c.lang == 'pt' else 1] for k, _ in por_st],
                [dict(nome='US$', dados=[round(v) for _, v in por_st])], 'usd'),
        grafico('g_cli', c.T('Quem mais deve (US$)', 'Quién más debe (US$)'), 'hbar', [nomes.nome(k) for k, _ in por_cli],
                [dict(nome='US$', dados=[round(v) for _, v in por_cli])], 'usd'),
        grafico('g_mes', c.T('Invoices x recebido por mês da pro-forma (US$)', 'Invoices x cobrado por mes de la pro-forma (US$)'), 'bar',
                [c.mes(m) for m in meses],
                [dict(nome=c.T('Invoices', 'Invoices'), dados=[round(por_mes_t[m]) for m in meses]),
                 dict(nome=c.T('Recebido', 'Cobrado'), dados=[round(por_mes_p[m]) for m in meses])], 'usd', largura='inteira'),
        grafico('g_termos', c.T('Condições de pagamento (valor das invoices)', 'Condiciones de pago (valor de invoices)'), 'doughnut',
                list(termos.keys()), [dict(nome='US$', dados=[round(v) for v in termos.values()])], 'usd'),
    ]
    linhas = []
    for i in sorted(aberto, key=lambda i: (i['venc'] or date(2999, 1, 1), -i['saldo'])):
        if not i['venc']:
            sit = [c.T('Sem vencimento', 'Sin vencimiento'), 'cinza']
        else:
            dias = (i['venc'] - hoje).days
            sit = ([c.T(f'Vencida há {-dias} dias', f'Vencida hace {-dias} días'), 'vermelho'] if dias < 0 else
                   [c.T(f'Vence em {dias} dias', f'Vence en {dias} días'), 'ambar' if dias <= 30 else 'azul'])
        linhas.append([i['inv'], nomes.nome(i['cli']), c.data(i['venc']), sit, round(i['tot']), round(i['pago']), round(-i['saldo']),
                       rot_st.get(i['st'], (i['st'].capitalize(),) * 2)[0 if c.lang == 'pt' else 1], i['obs'][:90]])
    conc = defaultdict(lambda: [0.0, 0.0, 0.0, 0])
    for i in sel:
        m = conc[i['cli']]
        m[0] += i['tot']; m[1] += i['pago']; m[2] += i['saldo']; m[3] += 1
    linhas_conc = [[nomes.nome(kk), m[3], round(m[0]), round(m[1]), round(-m[2]) if m[2] < 0 else 0, round(m[1] / m[0] * 100, 1) if m[0] else None]
                   for kk, m in sorted(conc.items(), key=lambda x: (x[1][2], -x[1][0])) if m[0] or m[2]]
    tabelas = [tabela(c.T('Cobranças em aberto (por vencimento)', 'Cobros abiertos (por vencimiento)'),
                      [('Invoice', 'texto'), (c.T('Cliente', 'Cliente'), 'texto'), (c.T('Vencimento', 'Vencimiento'), 'data'),
                       (c.T('Situação', 'Situación'), 'badge'), (c.T('Valor da invoice', 'Valor de la invoice'), 'usd'),
                       (c.T('Recebido', 'Cobrado'), 'usd'), (c.T('A receber', 'Por cobrar'), 'usd'),
                       (c.T('Status', 'Estado'), 'texto'), (c.T('Observação', 'Observación'), 'texto')],
                      linhas, nota=c.T(f'Posição em {c.data(hoje)}. Saldos negativos na planilha = valor a receber do cliente.',
                                       f'Posición al {c.data(hoje)}. Saldos negativos en la planilla = valor por cobrar del cliente.'),
                      vazio=c.T('Nenhuma cobrança em aberto neste filtro.', 'Ningún cobro abierto en este filtro.')),
               tabela(c.T('Conciliação de contas por cliente', 'Conciliación de cuentas por cliente'),
                      [(c.T('Cliente', 'Cliente'), 'texto'), ('Invoices', 'int'), (c.T('Total faturado', 'Total facturado'), 'usd'), (c.T('Recebido', 'Cobrado'), 'usd'),
                       (c.T('A receber', 'Por cobrar'), 'usd'), (c.T('% recebido', '% cobrado'), 'pct')], linhas_conc[:30],
                      nota=c.T('Ordenado pelo maior valor a receber.', 'Ordenado por mayor valor por cobrar.'))]
    return dict(titulo=c.T('Crédito e cobrança', 'Crédito y cobranza'), filtros=[f_ano, f_cli], kpis=kpis, graficos=graficos, tabelas=tabelas,
                fonte='Gestao de Credito Clientes')


# ========================================================= FATURAMENTO (BR)

def _faturamento(c):
    nomes = Nomes()
    ix, rows = _tab('vol_origem')
    k = {n: _c(ix, p) for n, p in dict(mes='MÊS', ano='ANO', cli='CLIENTE', pais='PAIS', art='Descrição', sku='Artigo', valor='Valor', mt='MT', kg='KG',
                                       fat='Fatura').items()}
    out = []
    for r in rows:
        ano, mes = _int(r[k['ano']]), _int(r[k['mes']])
        if not (ano and mes and r[k['cli']]):
            continue
        out.append(dict(ano=ano, mes=mes, cli=nomes.k(r[k['cli']]), pais=nomes.k(_pais(r[k['pais']]) or 'Não informado'),
                        art=_limpo(r[k['art']]).split('-[')[0][:42], sku=_limpo(r[k['sku']]), valor=_n(r[k['valor']]), mt=_n(r[k['mt']]), kg=_n(r[k['kg']]),
                        fat=_limpo(r[k['fat']])))
    return nomes, out


def painel_faturamento(c):
    nomes, base = _faturamento(c)
    ult_ano = max(i['ano'] for i in base)
    ult_mes = max(i['mes'] for i in base if i['ano'] == ult_ano)
    f_ano, ano = _filtro_ano(c, [i['ano'] for i in base], '')
    f_cli, cli = _filtro_cliente(c, nomes, [i['cli'] for i in base])
    f_pais, pais = _filtro_pais(c, nomes, [i['pais'] for i in base])
    sel = [i for i in base if (not cli or i['cli'] == cli) and (not pais or i['pais'] == pais)]   # sem filtro de ano
    ref = ano or ult_ano
    lim = ult_mes if ref == ult_ano else 12
    ate = lambda a: [i for i in sel if i['ano'] == a and i['mes'] <= lim]
    atual, anterior = ate(ref), ate(ref - 1)
    v_atual, v_ant = sum(i['valor'] for i in atual), sum(i['valor'] for i in anterior)
    mt_atual, mt_ant = sum(i['mt'] for i in atual), sum(i['mt'] for i in anterior)
    periodo = f"{MESES[c.lang][0]}–{MESES[c.lang][lim - 1]}"
    kpis = [
        kpi(c.T(f'Faturamento {ref} ({periodo})', f'Facturación {ref} ({periodo})'), round(v_atual), 'brl',
            c.T(f'vs {ref - 1}, mesmo período', f'vs {ref - 1}, mismo período'), tom='azul', delta=_delta(v_atual, v_ant)),
        kpi(c.T(f'Metros {ref}', f'Metros {ref}'), round(mt_atual), 'm', c.T(f'vs {ref - 1}, mesmo período', f'vs {ref - 1}, mismo período'),
            delta=_delta(mt_atual, mt_ant)),
        kpi(c.T('Preço médio', 'Precio medio'), round(v_atual / mt_atual, 2) if mt_atual else 0, 'brl2', c.T('R$ por metro', 'R$ por metro'),
            delta=_delta(v_atual / mt_atual if mt_atual else 0, v_ant / mt_ant if mt_ant else 0)),
        kpi(c.T('Clientes ativos', 'Clientes activos'), len({i['cli'] for i in atual}), 'int', f"{len({i['cli'] for i in anterior})} em {ref - 1}"),
        kpi(c.T('Notas fiscais', 'Facturas'), len({i['fat'] for i in atual if i['fat']}), 'int', c.T(f'em {ref}', f'en {ref}')),
        kpi(c.T('Países atendidos', 'Países atendidos'), len({i['pais'] for i in atual}), 'int', c.T(f'em {ref}', f'en {ref}')),
    ]
    anos_linha = [a for a in (ref - 2, ref - 1, ref) if any(i['ano'] == a for i in sel)]
    por_am = defaultdict(float)
    for i in sel:
        por_am[(i['ano'], i['mes'])] += i['valor']
    graf_mensal = grafico('g_mensal', c.T('Faturamento mensal por ano (R$)', 'Facturación mensual por año (R$)'), 'line',
                          MESES[c.lang], [dict(nome=str(a), dados=[round(por_am[(a, m)]) if (a, m) in por_am else None for m in range(1, 13)])
                                          for a in anos_linha], 'brl', largura='inteira')
    por_ano = defaultdict(float)
    for i in sel:
        por_ano[i['ano']] += i['valor']
    anos = sorted(por_ano)
    graf_ano = grafico('g_ano', c.T('Faturamento por ano (R$)', 'Facturación por año (R$)'), 'bar', [str(a) + (' *' if a == ult_ano else '') for a in anos],
                       [dict(nome='R$', dados=[round(por_ano[a]) for a in anos])], 'brl',
                       nota=c.T(f'* {ult_ano}: até {MESES[c.lang][ult_mes - 1]}', f'* {ult_ano}: hasta {MESES[c.lang][ult_mes - 1]}'))
    top_cli = _top(_soma(atual, lambda i: i['cli'], lambda i: i['valor']))
    por_pais = _top(_soma(atual, lambda i: i['pais'], lambda i: i['valor']), 8)
    top_art = _top(_soma(atual, lambda i: i['art'] or None, lambda i: i['mt']))
    top6 = [k for k, _ in _top(_soma(sel, lambda i: i['cli'], lambda i: i['valor'] if i['ano'] in anos_linha else 0), 6)]
    series = []
    for k in top6:
        series.append(dict(nome=nomes.nome(k), dados=[round(sum(i['valor'] for i in sel if i['cli'] == k and i['ano'] == a and i['mes'] <= (lim if a == ref else 12)))
                                                       for a in anos_linha]))
    outros = [round(sum(i['valor'] for i in sel if i['cli'] not in top6 and i['ano'] == a and i['mes'] <= (lim if a == ref else 12))) for a in anos_linha]
    series.append(dict(nome=c.T('Outros', 'Otros'), dados=outros))
    graficos = [
        graf_mensal, graf_ano,
        grafico('g_cli', c.T(f'Top 10 clientes em {ref} (R$)', f'Top 10 clientes en {ref} (R$)'), 'hbar', [nomes.nome(k) for k, _ in top_cli],
                [dict(nome='R$', dados=[round(v) for _, v in top_cli])], 'brl'),
        grafico('g_pais', c.T(f'Faturamento por país em {ref} (R$)', f'Facturación por país en {ref} (R$)'), 'doughnut', [nomes.nome(k) for k, _ in por_pais],
                [dict(nome='R$', dados=[round(v) for _, v in por_pais])], 'brl'),
        grafico('g_art', c.T(f'Top 10 artigos em {ref} (metros)', f'Top 10 artículos en {ref} (metros)'), 'hbar', [k for k, _ in top_art],
                [dict(nome='m', dados=[round(v) for _, v in top_art])], 'm'),
        grafico('g_evol', c.T('Clientes principais ano a ano (R$, mesmo período)', 'Clientes principales año a año (R$, mismo período)'), 'bar',
                [str(a) for a in anos_linha], series, 'brl', empilhado=True),
    ]
    clientes = {i['cli'] for i in sel if i['ano'] in (ref - 2, ref - 1, ref)}
    linhas = []
    for k in clientes:
        cada = [sum(i['valor'] for i in sel if i['cli'] == k and i['ano'] == a and (i['mes'] <= lim)) for a in (ref - 2, ref - 1, ref)]
        if any(cada):
            linhas.append([nomes.nome(k), *[round(v) for v in cada], (round(_delta(cada[2], cada[1])) if cada[1] else None),
                           round(cada[2] / v_atual * 100, 1) if v_atual else 0])
    linhas.sort(key=lambda r: -(r[3] or 0))
    tabelas = [tabela(c.T(f'Clientes ano a ano — mesmo período ({periodo})', f'Clientes año a año — mismo período ({periodo})'),
                      [(c.T('Cliente', 'Cliente'), 'texto'), (str(ref - 2), 'brl'), (str(ref - 1), 'brl'), (str(ref), 'brl'),
                       (c.T(f'Variação vs {ref - 1}', f'Variación vs {ref - 1}'), 'pct_delta'), (c.T('Participação', 'Participación'), 'pct')],
                      linhas[:25])]
    skus = defaultdict(lambda: dict(art='', mt=0.0, v=0.0, v_ant=0.0))
    for i in sel:
        if i['sku'] and i['mes'] <= lim:
            m = skus[i['sku']]
            if i['ano'] == ref:
                m['mt'] += i['mt']; m['v'] += i['valor']; m['art'] = m['art'] or i['art']
            elif i['ano'] == ref - 1:
                m['v_ant'] += i['valor']; m['art'] = m['art'] or i['art']
    tabelas.append(tabela(c.T(f'Por SKU (artigo) — {ref}, {periodo}', f'Por SKU (artículo) — {ref}, {periodo}'),
                          [('SKU', 'texto'), (c.T('Descrição', 'Descripción'), 'texto'), (c.T('Metros', 'Metros'), 'm'), ('R$', 'brl'),
                           (c.T(f'Variação vs {ref - 1}', f'Variación vs {ref - 1}'), 'pct_delta')],
                          [[sku, m['art'], round(m['mt']), round(m['v']), round(_delta(m['v'], m['v_ant'])) if m['v_ant'] else None]
                           for sku, m in sorted(skus.items(), key=lambda x: -x[1]['v'])[:15] if m['v']]))
    return dict(titulo=c.T('Faturamento', 'Facturación'), filtros=[f_ano, f_cli, f_pais], kpis=kpis, graficos=graficos, tabelas=tabelas,
                fonte='Volume Vendas x Clientes')


# ======================================================== AMOSTRAS (BR / GT)

_ST_AMOSTRA = {
    'cotado': ('Cotado (aguardando cliente)', 'Cotizado (esperando cliente)', 'ambar'),
    'aprovado': ('Aprovado', 'Aprobado', 'verde'), 'amostra liberada': ('Amostra liberada', 'Muestra liberada', 'verde'),
    '3d liberado': ('3D liberado', '3D liberado', 'azul'), 'app': ('Solicitado via App', 'Solicitado vía App', 'azul'),
    'lanzado': ('Lançado', 'Lanzado', 'verde'), 'produção': ('Em produção', 'En producción', 'azul'),
    'eliminado': ('Eliminado', 'Eliminado', 'cinza'), 'cancelado': ('Cancelado', 'Cancelado', 'cinza'),
    'reprovado': ('Reprovado', 'Reprobado', 'vermelho'), 'na': ('Sem status', 'Sin estado', 'cinza'),
}
_APROVADOS = ('aprovado', 'amostra liberada', 'lanzado', 'produção')


def painel_amostras(c):
    nomes = Nomes()
    cot = []
    for orig, id_ in (('BR', 'am_cotacao_br'), ('GT', 'am_cotacao_gt')):
        if existe(id_):
            cot += _cotacoes(c, nomes, orig, id_)
    f_orig, orig = _filtro_origem(c)
    cot = [i for i in cot if not orig or i['orig'] == orig]
    return _amostras_resto(c, nomes, cot, f_orig, orig)


def _cotacoes(c, nomes, orig, id_):
    ix, rows = _tab(id_)
    k = {n: _c(ix, p) for n, p in dict(cli='CLIENTE', dat='DATA SOLICITAÇÃO', st='STATUS', cost='COST', sol='SOLICITAÇÃO', app='ID APP', gsm='GSM',
                                       larg='ANCHO', comp='COMPOSICI').items()}
    cot = []
    for r in rows:
        d = _d(r[k['dat']])
        if r[k['cli']]:
            cot.append(dict(orig=orig, cli=nomes.k(r[k['cli']]), data=d, st=_limpo(r[k['st']]).lower() or 'na', cost=_n(r[k['cost']]), sol=_limpo(r[k['sol']]), app=_limpo(r[k['app']]),
                            gsm=_n(r[k['gsm']]) or None, larg=_n(r[k['larg']]) or None, comp=_limpo(r[k['comp']])[:36]))
    return cot


def _amostras_resto(c, nomes, cot, f_orig, orig):
    f_ano, ano = _filtro_ano(c, [i['data'].year for i in cot if i['data']], str(max(i['data'].year for i in cot if i['data'])))
    f_cli, cli = _filtro_cliente(c, nomes, [i['cli'] for i in cot])
    sel = [i for i in cot if (not ano or (i['data'] and i['data'].year == ano)) and (not cli or i['cli'] == cli)]
    n = len(sel)
    cont = Counter(i['st'] for i in sel)
    aprov = sum(cont[s] for s in _APROVADOS)
    em_aberto = cont['cotado'] + cont['app'] + cont['3d liberado']
    custos = [i['cost'] for i in sel if i['cost']]
    pe_ix, pe_rows = _tab('am_pedidos')
    pk = {n_: _c(pe_ix, p) for n_, p in dict(cli='CLIENTE', dat='DATA SOLICITAÇÃO', q='QUANT').items()}
    ped = [dict(cli=nomes.k(r[pk['cli']]), data=_d(r[pk['dat']]), q=_n(r[pk['q']])) for r in pe_rows if r[pk['cli']]] if orig != 'GT' else []
    ped = [p for p in ped if (not ano or (p['data'] and p['data'].year == ano)) and (not cli or p['cli'] == cli)]
    kpis = [
        kpi(c.T('Cotações de amostras', 'Cotizaciones de muestras'), n, 'int', c.T('no filtro', 'en el filtro')),
        kpi(c.T('Aprovadas', 'Aprobadas'), aprov, 'int', c.T('aprovado + liberada + lançado + produção', 'aprobado + liberada + lanzado + producción'), tom='verde'),
        kpi(c.T('Taxa de aprovação', 'Tasa de aprobación'), round(aprov / n * 100, 1) if n else None, 'pct', c.T('aprovadas ÷ cotações', 'aprobadas ÷ cotizaciones')),
        kpi(c.T('Aguardando cliente', 'Esperando cliente'), em_aberto, 'int', c.T('cotado + App + 3D', 'cotizado + App + 3D'), tom='ambar'),
        kpi(c.T('Custo médio cotado', 'Costo medio cotizado'), round(sum(custos) / len(custos), 2) if custos else 0, 'usd2', c.T('US$ por metro', 'US$ por metro')),
        kpi(c.T('Clientes', 'Clientes'), len({i['cli'] for i in sel}), 'int', c.T('com cotação', 'con cotización')),
    ]
    if orig != 'GT':
        kpis.append(kpi(c.T('Pedidos de amostra', 'Pedidos de muestra'), len(ped), 'int', f"{round(sum(p['q'] for p in ped)):,}".replace(',', '.') + ' ' + c.T('metros', 'metros')))
    st_ord = sorted(cont.items(), key=lambda x: -x[1])
    rot = lambda s: _ST_AMOSTRA.get(s, (s.capitalize(), s.capitalize(), 'cinza'))
    por_mes, por_mes_ap = Counter(), Counter()
    for i in sel:
        if not i['data']:
            continue
        m = f"{i['data'].year}-{i['data'].month:02d}"
        por_mes[m] += 1
        if i['st'] in _APROVADOS:
            por_mes_ap[m] += 1
    meses = sorted(por_mes)
    top_cli = Counter(i['cli'] for i in sel).most_common(10)
    graficos = [
        grafico('g_st', c.T('Cotações por status', 'Cotizaciones por estado'), 'doughnut', [rot(s)[0 if c.lang == 'pt' else 1] for s, _ in st_ord],
                [dict(nome=c.T('cotações', 'cotizaciones'), dados=[v for _, v in st_ord])], 'int', cores=[_status_cor(rot(s)[2]) for s, _ in st_ord]),
        grafico('g_mes', c.T('Cotações por mês (total x aprovadas)', 'Cotizaciones por mes (total x aprobadas)'), 'bar', [c.mes(m) for m in meses],
                [dict(nome=c.T('Cotações', 'Cotizaciones'), dados=[por_mes[m] for m in meses]),
                 dict(nome=c.T('Aprovadas', 'Aprobadas'), dados=[por_mes_ap[m] for m in meses])], 'int'),
        grafico('g_cli', c.T('Clientes com mais cotações', 'Clientes con más cotizaciones'), 'hbar', [nomes.nome(kk) for kk, _ in top_cli],
                [dict(nome=c.T('cotações', 'cotizaciones'), dados=[v for _, v in top_cli])], 'int'),
    ]
    taxa_cli = []
    for kk, qtd in Counter(i['cli'] for i in sel).most_common(10):
        ap = sum(1 for i in sel if i['cli'] == kk and i['st'] in _APROVADOS)
        taxa_cli.append((nomes.nome(kk), round(ap / qtd * 100, 1)))
    graficos.append(grafico('g_taxa', c.T('Taxa de aprovação dos clientes principais', 'Tasa de aprobación de los clientes principales'), 'hbar',
                            [a for a, _ in taxa_cli], [dict(nome='%', dados=[b for _, b in taxa_cli])], 'pct'))
    recentes = sorted((i for i in sel if i['data']), key=lambda i: i['data'], reverse=True)[:15]
    tabelas = [tabela(c.T('Últimas cotações', 'Últimas cotizaciones'),
                      [(c.T('Data', 'Fecha'), 'data'), (c.T('Cliente', 'Cliente'), 'texto'), (c.T('Origem', 'Origen'), 'texto'), ('ID App', 'texto'), (c.T('Solicitação', 'Solicitud'), 'texto'),
                       ('GSM', 'texto'), (c.T('Largura cm', 'Ancho cm'), 'texto'), (c.T('Composição / estrutura', 'Composición / estructura'), 'texto'),
                       (c.T('Status', 'Estado'), 'badge'), ('Custo US$/m', 'usd2')],
                      [[c.data(i['data']), nomes.nome(i['cli']), i['orig'], i['app'], i['sol'][:60], i['gsm'], i['larg'], i['comp'],
                        [rot(i['st'])[0 if c.lang == 'pt' else 1], rot(i['st'])[2]], i['cost'] or None] for i in recentes])]
    return dict(titulo=c.T('Amostras e cotações', 'Muestras y cotizaciones'), filtros=[f_orig, f_ano, f_cli], kpis=kpis, graficos=graficos, tabelas=tabelas,
                fonte='PEDIDOS E COTAÇOES DE AMOSTRAS')


# ============================================================ DOCUMENTACAO (BR)

_ETAPAS = ['Roma- neio', 'Invoice', 'PL', 'C.O (rascunho)', 'Aprovação docs', 'Faturamento (Xml)', 'C.O (Assinado)',
           'Due / Draft(se necessario)', 'Crt/Awb /Booking', 'Chapas', 'FT da carga', 'Docs Luciano']
_ETAPA_ROT = {'Roma- neio': 'Romaneio', 'Due / Draft(se necessario)': 'Due / Draft', 'Crt/Awb /Booking': 'CRT / AWB / Booking',
              'Faturamento (Xml)': 'Faturamento (XML)', 'C.O (rascunho)': 'C.O (rascunho)', 'Docs Luciano': 'Docs Luciano'}


def painel_documentacao(c):
    nomes = Nomes()
    ix, rows = _tab('doc_pedidos')
    k = {n: _c(ix, p) for n, p in dict(ped='Pedido', cli='Cliente', mts='Mts', st='Status', tr='Transport', col='Coleta').items()}
    et = {e: ix[e.upper()] for e in _ETAPAS if e.upper() in ix}
    peds = []
    for r in rows:
        if not (r[k['ped']] and r[k['cli']]):
            continue
        peds.append(dict(ped=_limpo(r[k['ped']]), cli=nomes.k(r[k['cli']]), mts=_n(r[k['mts']]), st=_limpo(r[k['st']]), tr=_limpo(r[k['tr']]),
                         col=_limpo(r[k['col']]), ok={e: str(r[i] or '').strip().lower().startswith('ok') for e, i in et.items()}))
    pronto = lambda p: p['st'].lower().startswith('docs ok ped enviado')
    concl = [p for p in peds if pronto(p)]
    # etapa "aplicavel" = maioria dos pedidos concluidos a cumpriu (evita cobrar etapas opcionais)
    aplic = [e for e in et if concl and sum(p['ok'][e] for p in concl) / len(concl) >= 0.7]
    abertos = [p for p in peds if not pronto(p)]
    pend = {p['ped']: [e for e in aplic if not p['ok'][e]] for p in abertos}
    pend_et = Counter(e for lst in pend.values() for e in lst)
    st = Counter()
    for p in peds:
        st[('Docs concluídos' if pronto(p) else (p['st'] or 'Sem status'))] += 1
    ex_ix, ex_rows = _tab('doc_enviados')
    ek = {n: _c(ex_ix, p) for n, p in dict(ped='Pedido', cli='Cliente', col='Data coleta', prev='Prev', tr='Transportadora', modal='Modal', chk='Check').items()}
    env = []
    for r in ex_rows:
        if r[ek['ped']]:
            m = _chave(r[ek['modal']])
            modal = ('Aéreo', 'Aéreo') if m.startswith(('AERE', 'AIR')) else ('Marítimo', 'Marítimo') if m.startswith(('MAR', 'SEA')) else ('Rodoviário', 'Terrestre') if m.startswith('ROD') else ('Outros', 'Otros')
            env.append(dict(ped=_limpo(r[ek['ped']]), cli=_limpo(r[ek['cli']]), col=_d(r[ek['col']]), prev=_d(r[ek['prev']]), tr=_limpo(r[ek['tr']]),
                            modal=modal, chk=_limpo(r[ek['chk']]).lower().startswith('ok')))
    prazos = [(e['prev'] - e['col']).days for e in env if e['col'] and e['prev'] and e['prev'] >= e['col']]
    kpis = [
        kpi(c.T('Pedidos acompanhados', 'Pedidos en seguimiento'), len(peds), 'int', c.T('na planilha de processos', 'en la planilla de procesos')),
        kpi(c.T('Documentação concluída', 'Documentación concluida'), len(concl), 'int', f"{round(len(concl) / len(peds) * 100) if peds else 0}% " + c.T('dos pedidos', 'de los pedidos'), tom='verde'),
        kpi(c.T('Em andamento', 'En curso'), len(abertos), 'int', c.T('com etapas pendentes', 'con etapas pendientes'), tom='ambar' if abertos else 'verde'),
        kpi(c.T('Pedidos enviados', 'Pedidos enviados'), len(env), 'int', f"{sum(e['chk'] for e in env)} " + c.T('com recebimento confirmado', 'con recepción confirmada'), tom='azul'),
        kpi(c.T('Prazo médio de entrega', 'Plazo medio de entrega'), round(sum(prazos) / len(prazos)) if prazos else None, 'dias', c.T('da coleta à previsão de entrega', 'de la recolección a la entrega prevista')),
    ]
    modais = Counter(e['modal'][0 if c.lang == 'pt' else 1] for e in env)
    trans = Counter(re.sub(r'\s*\(\d+\)', '', e['tr']).split('/')[0].strip() for e in env if e['tr'])
    graficos = [
        grafico('g_st', c.T('Situação da documentação', 'Situación de la documentación'), 'doughnut', list(st.keys()),
                [dict(nome=c.T('pedidos', 'pedidos'), dados=list(st.values()))], 'int'),
        grafico('g_pend', c.T('Etapas mais pendentes (pedidos em andamento)', 'Etapas más pendientes (pedidos en curso)'), 'hbar',
                [_ETAPA_ROT.get(e, e) for e, _ in pend_et.most_common()], [dict(nome=c.T('pedidos', 'pedidos'), dados=[v for _, v in pend_et.most_common()])], 'int'),
        grafico('g_modal', c.T('Pedidos enviados por modal', 'Pedidos enviados por modal'), 'doughnut', list(modais.keys()),
                [dict(nome=c.T('envios', 'envíos'), dados=list(modais.values()))], 'int'),
        grafico('g_trans', c.T('Envios por transportadora', 'Envíos por transportista'), 'hbar', [a for a, _ in trans.most_common(8)],
                [dict(nome=c.T('envios', 'envíos'), dados=[b for _, b in trans.most_common(8)])], 'int'),
    ]
    linhas = [[p['ped'], nomes.nome(p['cli']), round(p['mts']), [p['st'] or 'Sem status', 'ambar'], ', '.join(_ETAPA_ROT.get(e, e) for e in pend[p['ped']]) or '—',
               p['tr'] or '—', p['col'] or '—'] for p in abertos]
    tabelas = [tabela(c.T('Pedidos com documentação em andamento', 'Pedidos con documentación en curso'),
                      [(c.T('Pedido', 'Pedido'), 'texto'), (c.T('Cliente', 'Cliente'), 'texto'), (c.T('Metros', 'Metros'), 'm'), (c.T('Status', 'Estado'), 'badge'),
                       (c.T('Etapas pendentes', 'Etapas pendientes'), 'texto'), (c.T('Transportadora', 'Transportista'), 'texto'), (c.T('Coleta', 'Recolección'), 'texto')],
                      linhas, nota=c.T('Etapas pendentes consideram só as etapas que a maioria dos pedidos concluídos cumpriu.',
                                       'Las etapas pendientes consideran solo las que cumplió la mayoría de pedidos concluidos.'))]
    return dict(titulo=c.T('Documentação e envios', 'Documentación y envíos'), filtros=[], kpis=kpis, graficos=graficos, tabelas=tabelas, fonte='Processos documentação de pedidos')


# ============================================================== PEDIDOS (GT)

def _gt_base(c):
    nomes = Nomes()
    ix, rows = _tab('gt_followup')
    k = {n: _c(ix, p) for n, p in dict(pf='PRO-FORM', cli='CLIENTE', pais='PAIS', dat='DATA PRO-FORMA', ped='PEDIDO TOTVS', env='Data max',
                                       mts='TOTAL MTS', usd='TOTAL US$', obs='OBSERV').items()}
    base = []
    for r in rows:
        d = _d(r[k['dat']])
        if r[k['cli']] and d:
            base.append(dict(pf=_limpo(r[k['pf']]), cli=nomes.k(r[k['cli']]), pais=nomes.k(_pais(r[k['pais']]) or 'Não informado'), data=d,
                             env=_d(r[k['env']]), mts=_n(r[k['mts']]), usd=_n(r[k['usd']]), obs=_limpo(r[k['obs']])))
    return nomes, base


def painel_pedidos_gt(c):
    nomes, base = _gt_base(c)
    f_ano, ano = _filtro_ano(c, [i['data'].year for i in base], str(max(i['data'].year for i in base)))
    f_pais, pais = _filtro_pais(c, nomes, [i['pais'] for i in base])
    f_cli, cli = _filtro_cliente(c, nomes, [i['cli'] for i in base])
    sel = [i for i in base if (not ano or i['data'].year == ano) and (not pais or i['pais'] == pais) and (not cli or i['cli'] == cli)]
    usd, mts = sum(i['usd'] for i in sel), sum(i['mts'] for i in sel)
    kpis = [
        kpi(c.T('Pro-formas', 'Pro-formas'), len(sel), 'int', c.T('no período filtrado', 'en el período filtrado')),
        kpi(c.T('Valor das pro-formas', 'Valor de las pro-formas'), round(usd), 'usd', f"{len({i['cli'] for i in sel})} " + c.T('clientes', 'clientes')),
        kpi(c.T('Metros / jardas', 'Metros / yardas'), round(mts), 'int', c.T('soma do follow-up', 'suma del seguimiento')),
        kpi(c.T('Preço médio', 'Precio medio'), round(usd / mts, 2) if mts else 0, 'usd2', c.T('US$ por metro/jarda', 'US$ por metro/yarda')),
        kpi(c.T('Ticket médio', 'Ticket medio'), round(usd / len(sel)) if sel else 0, 'usd', c.T('por pro-forma', 'por pro-forma')),
        kpi(c.T('Países', 'Países'), len({i['pais'] for i in sel}), 'int', c.T('atendidos', 'atendidos')),
    ]
    por_mes = _soma(sel, lambda i: f"{i['data'].year}-{i['data'].month:02d}", lambda i: i['usd'])
    meses = sorted(por_mes)
    top_cli = _top(_soma(sel, lambda i: i['cli'], lambda i: i['usd']))
    por_pais = _top(_soma(sel, lambda i: i['pais'], lambda i: i['usd']), 8)
    dix, drows = _tab('gt_detalhes')
    dk = {n: _c(dix, p) for n, p in dict(dat='DATA /', cli='CLIENTE', pais='PAIS', desc='DESCRIP', usd='TOTAL US$').items()}
    desc = defaultdict(float)
    for r in drows:
        d = _d(r[dk['dat']])
        if r[dk['cli']] and d and (not ano or d.year == ano) and (not pais or nomes.k(_pais(r[dk['pais']]) or '') == pais) and (not cli or nomes.k(r[dk['cli']]) == cli):
            desc[re.sub(r'\s+', ' ', _limpo(r[dk['desc']]))[:46]] += _n(r[dk['usd']])
    top_desc = _top(desc, 10)
    graficos = [
        grafico('g_mes', c.T('Valor das pro-formas por mês (US$)', 'Valor de pro-formas por mes (US$)'), 'bar', [c.mes(m) for m in meses],
                [dict(nome='US$', dados=[round(por_mes[m]) for m in meses])], 'usd', largura='inteira'),
        grafico('g_cli', c.T('Top 10 clientes (US$)', 'Top 10 clientes (US$)'), 'hbar', [nomes.nome(kk) for kk, _ in top_cli],
                [dict(nome='US$', dados=[round(v) for _, v in top_cli])], 'usd'),
        grafico('g_pais', c.T('Valor por país (US$)', 'Valor por país (US$)'), 'doughnut', [nomes.nome(kk) for kk, _ in por_pais],
                [dict(nome='US$', dados=[round(v) for _, v in por_pais])], 'usd'),
        grafico('g_desc', c.T('Produtos mais vendidos (US$)', 'Productos más vendidos (US$)'), 'hbar', [a for a, _ in top_desc],
                [dict(nome='US$', dados=[round(v) for _, v in top_desc])], 'usd', largura='inteira'),
    ]
    rec = sorted(sel, key=lambda i: i['data'], reverse=True)[:15]
    tabelas = [tabela(c.T('Pro-formas mais recentes', 'Pro-formas más recientes'),
                      [('Pro-forma', 'texto'), (c.T('Cliente', 'Cliente'), 'texto'), (c.T('País', 'País'), 'texto'), (c.T('Data', 'Fecha'), 'data'),
                       (c.T('Envio máx.', 'Envío máx.'), 'data'), (c.T('Metros', 'Metros'), 'int'), ('US$', 'usd'), (c.T('Observação', 'Observación'), 'texto')],
                      [[i['pf'], nomes.nome(i['cli']), nomes.nome(i['pais']), c.data(i['data']), c.data(i['env']), round(i['mts']), round(i['usd']), i['obs'][:80]] for i in rec])]
    return dict(titulo=c.T('Pedidos', 'Pedidos'), filtros=[f_ano, f_pais, f_cli], kpis=kpis, graficos=graficos, tabelas=tabelas, fonte='PEDIDOS - CLIENTES GUATEMALA')


# =============================================================== VISAO GERAL

def _sem_filtros(c, **fixos):
    return Ctx(c.lang, fixos)


def painel_visao(c):
    fat = painel_faturamento(_sem_filtros(c))
    ped = painel_pedidos_br(_sem_filtros(c))
    cre = painel_credito(_sem_filtros(c))
    amo = painel_amostras(_sem_filtros(c))
    doc = painel_documentacao(_sem_filtros(c))
    nomes_gt, gt = _gt_base(c)
    ano = fat['filtros'][0]['valor'] or str(c.hoje.year)
    ano = int(ano) if str(ano).isdigit() else c.hoje.year
    gt_ano = [i for i in gt if i['data'].year == ano]
    nomes_br, itens_br, _, _ = _pedidos_br(c)
    br_ano = [i for i in itens_br if i['data'].year == ano]
    usd_br, usd_gt = sum(i['usd'] for i in br_ano), sum(i['usd'] for i in gt_ano)
    kpis = [
        fat['kpis'][0], fat['kpis'][1],
        kpi(c.T(f'Pro-formas {ano} — Brasil', f'Pro-formas {ano} — Brasil'), round(usd_br), 'usd', f"{len({i['pf'] for i in br_ano})} pro-formas", tom='azul'),
        kpi(c.T(f'Pro-formas {ano} — Guatemala', f'Pro-formas {ano} — Guatemala'), round(usd_gt), 'usd', f"{len(gt_ano)} pro-formas", tom='azul'),
        ped['kpis'][4], ped['kpis'][5], cre['kpis'][2], cre['kpis'][3], amo['kpis'][2], doc['kpis'][2],
    ]
    kpis[4]['rotulo'] = c.T('Pedidos em aberto (Brasil)', 'Pedidos abiertos (Brasil)')
    mes = lambda i: f"{i['data'].year}-{i['data'].month:02d}"
    m_br, m_gt = _soma(itens_br, mes, lambda i: i['usd']), _soma(gt, mes, lambda i: i['usd'])
    meses = [m for m in _meses_ordenados(m_br, m_gt) if m >= f'{ano - 1}-01']
    graf_pf = grafico('g_pf', c.T('Pro-formas por mês: Brasil x Guatemala (US$)', 'Pro-formas por mes: Brasil x Guatemala (US$)'), 'bar',
                      [c.mes(m) for m in meses], [dict(nome='Brasil', dados=[round(m_br.get(m, 0)) for m in meses]),
                                                   dict(nome='Guatemala', dados=[round(m_gt.get(m, 0)) for m in meses])], 'usd', largura='inteira')
    graficos = [fat['graficos'][0], graf_pf, fat['graficos'][2], ped['graficos'][4], cre['graficos'][0], amo['graficos'][0]]
    graficos[0]['titulo'] = c.T('Faturamento mensal (R$)', 'Facturación mensual (R$)')
    graficos[2]['titulo'] = c.T('Top 10 clientes no ano (R$)', 'Top 10 clientes del año (R$)')
    graficos[3]['titulo'] = c.T('Situação de produção dos itens (Brasil)', 'Situación de producción de los ítems (Brasil)')
    graficos[5]['titulo'] = c.T('Cotações de amostras por status (Brasil + Guatemala)', 'Cotizaciones de muestras por estado (Brasil + Guatemala)')
    tabelas = [ped['tabelas'][0], cre['tabelas'][0]]
    tabelas[0]['titulo'] = c.T('Atenção: entregas em aberto (Brasil)', 'Atención: entregas abiertas (Brasil)')
    tabelas[1]['titulo'] = c.T('Atenção: cobranças em aberto', 'Atención: cobros abiertos')
    tabelas[0]['linhas'] = tabelas[0]['linhas'][:8]
    tabelas[1]['linhas'] = tabelas[1]['linhas'][:8]
    return dict(titulo=c.T('Visão geral', 'Resumen'), filtros=[], kpis=kpis, graficos=graficos, tabelas=tabelas, fonte='Exportação')


# ================================================================== registro

_PAINEIS = [
    ('visao', 'Visão geral', 'Resumen', painel_visao,
     ['br_detalhes', 'br_followup', 'gt_followup', 'cred_gestao', 'vol_origem', 'am_cotacao_br', 'am_cotacao_gt', 'am_pedidos', 'doc_pedidos', 'doc_enviados']),
    ('pedidos_br', 'Pedidos e entregas — Brasil', 'Pedidos y entregas — Brasil', painel_pedidos_br, ['br_detalhes', 'br_followup']),
    ('pedidos_gt', 'Pedidos — Guatemala', 'Pedidos — Guatemala', painel_pedidos_gt, ['gt_followup', 'gt_detalhes']),
    ('credito', 'Crédito e cobrança', 'Crédito y cobranza', painel_credito, ['cred_gestao']),
    ('faturamento', 'Faturamento', 'Facturación', painel_faturamento, ['vol_origem']),
    ('amostras', 'Amostras e cotações', 'Muestras y cotizaciones', painel_amostras, ['am_cotacao_br', 'am_cotacao_gt', 'am_pedidos']),
    ('documentacao', 'Documentação e envios', 'Documentación y envíos', painel_documentacao, ['doc_pedidos', 'doc_enviados']),
]


def disponiveis(lang):
    return [dict(id=id_, titulo=es if lang == 'es' else pt) for id_, pt, es, _, deps in _PAINEIS if all(existe(d) for d in deps)]


def montar(nome, lang, filtros):
    c = Ctx(lang, filtros)
    for id_, _, _, fn, deps in _PAINEIS:
        if id_ == nome:
            p = fn(c)
            p['id'] = nome
            p['fontes'] = fontes(c, deps)
            p['posicao'] = c.data(c.hoje)
            return p
    raise KeyError(nome)
