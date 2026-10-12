"""Painel BI Comercial a partir do Relatorio 150 (itens faturados) gravado pelo servico."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, timedelta

from app.comercial_me.paineis import MESES, grafico, kpi, tabela
from app.totvs import servico


def _d(v):
    try:
        return date.fromisoformat(str(v)[:10]) if v else None
    except ValueError:
        return None


def _n(v):
    return float(v) if isinstance(v, (int, float)) else 0.0


def _fmt_data(d):
    return d.strftime('%d/%m/%Y') if d else ''


def _top(dic, n=10):
    return sorted(dic.items(), key=lambda x: -x[1])[:n]


def _filtro(id_, rotulo, opcoes, valor):
    return dict(id=id_, rotulo=rotulo, opcoes=[dict(v=str(v), t=str(t)) for v, t in opcoes], valor=str(valor))


def montar(tag: str, lang: str, f: dict) -> dict:
    T = (lambda pt, es: es if lang == 'es' else pt)
    d = servico.carregar(tag)
    if not d:
        return dict(vazio=True, filtros=[], kpis=[], graficos=[], tabelas=[])
    meta, cols, linhas = d['meta'], d['colunas'], d['linhas']
    ix = {c: i for i, c in enumerate(cols)}
    g = lambda r, c: r[ix[c]] if c in ix else None

    reg = []
    for r in linhas:
        dt = _d(g(r, 'DATA')) or _d(g(r, 'EMISSAO'))
        if not dt:
            continue
        reg.append(dict(dt=dt, emp=g(r, 'Emp'), rep=(g(r, 'REPRE') or '—'), seg=(g(r, 'SEGMENTO') or '—'), cli_cod=g(r, 'COD.CLI'),
                        cli=(g(r, 'FANTASIA') or g(r, 'CLIENTE') or '—'), nf=g(r, 'NFE'), op=(g(r, 'DESC.') or '—'), art=(g(r, 'ARTIGO') or '—'),
                        q=_n(g(r, 'QUANT')), un=(g(r, 'ESP.') or ''), v=_n(g(r, 'TOT.LIQUID')), uf=(g(r, 'UF') or '—')))

    ini0, fim0 = _d(meta['inicio']), _d(meta['fim'])
    di = _d(f.get('data_ini')) or ini0
    df = _d(f.get('data_fim')) or fim0
    if df < di:
        di, df = df, di

    def opcoes(chave, rotulo, rid):
        vals = sorted({x[chave] for x in reg if x[chave] not in (None, '', '—')}, key=lambda s: str(s).lower())
        atual = f.get(rid) or ''
        if atual not in {str(v) for v in vals}:
            atual = ''
        return _filtro(rid, rotulo, [('', T('Todos', 'Todos'))] + [(v, v) for v in vals], atual), atual

    f_emp, emp = opcoes('emp', T('Empresa', 'Empresa'), 'empresa')
    f_rep, rep = opcoes('rep', T('Representante', 'Representante'), 'representante')
    f_seg, seg = opcoes('seg', T('Segmento', 'Segmento'), 'segmento')
    f_cli, cli = opcoes('cli', T('Cliente', 'Cliente'), 'cliente')

    def ok(x, a, b):
        return (a <= x['dt'] <= b and (not emp or str(x['emp']) == emp) and (not rep or x['rep'] == rep)
                and (not seg or x['seg'] == seg) and (not cli or x['cli'] == cli))

    sel = [x for x in reg if ok(x, di, df)]
    dias = (df - di).days + 1
    ant_f, ant_i = di - timedelta(days=1), di - timedelta(days=dias)
    ant = [x for x in reg if ok(x, ant_i, ant_f)] if ini0 <= ant_i else []

    tot = sum(x['v'] for x in sel)
    tot_ant = sum(x['v'] for x in ant)
    nfs = {(x['emp'], x['nf']) for x in sel}
    clientes = {x['cli'] for x in sel}
    un_cont = Counter()
    for x in sel:
        un_cont[x['un']] += x['q']
    un_main = un_cont.most_common(1)[0][0] if un_cont else ''
    q_main = un_cont.get(un_main, 0)
    outras = ', '.join(f'{round(q):,} {u}'.replace(',', '.') for u, q in un_cont.most_common()[1:3] if u)
    delta = round((tot - tot_ant) / tot_ant * 100, 1) if tot_ant else None

    kpis = [
        kpi(T('Faturamento líquido', 'Facturación neta'), round(tot, 2), 'brl', T('no período filtrado', 'en el período filtrado'), tom='azul', delta=delta),
        kpi(T('Notas fiscais', 'Notas fiscales'), len(nfs), 'int', f'{len(sel)} ' + T('itens', 'ítems')),
        kpi(T('Clientes ativos', 'Clientes activos'), len(clientes), 'int', T('com faturamento no período', 'con facturación en el período')),
        kpi(T('Ticket médio por NF', 'Ticket medio por NF'), round(tot / len(nfs), 2) if nfs else 0, 'brl', T('valor líquido por nota', 'valor neto por nota')),
        kpi(T(f'Quantidade ({un_main})', f'Cantidad ({un_main})'), round(q_main, 2), 'int', outras or None),
        kpi(T(f'Preço médio líquido por {un_main}', f'Precio medio neto por {un_main}'),
            round(sum(x['v'] for x in sel if x['un'] == un_main) / q_main, 2) if q_main else 0, 'brl2', T('R$ por unidade', 'R$ por unidad')),
    ]

    por_cli, por_rep, por_seg, por_uf, por_op, por_art = (defaultdict(float) for _ in range(6))
    por_mes, por_dia = defaultdict(float), defaultdict(float)
    cli_nf, cli_q = defaultdict(set), defaultdict(float)
    for x in sel:
        por_cli[x['cli']] += x['v']; por_rep[x['rep']] += x['v']; por_seg[x['seg']] += x['v']
        por_uf[x['uf']] += x['v']; por_op[x['op']] += x['v']; por_art[x['art'][:48]] += x['v']
        por_mes[f"{x['dt'].year}-{x['dt'].month:02d}"] += x['v']; por_dia[x['dt']] += x['v']
        cli_nf[x['cli']].add((x['emp'], x['nf'])); cli_q[x['cli']] += x['q'] if x['un'] == un_main else 0
    mes = lambda m: f"{MESES['es' if lang == 'es' else 'pt'][int(m[5:]) - 1]}/{m[2:4]}"

    graficos = []
    if dias > 62:
        ms = sorted(por_mes)
        graficos.append(grafico('b_tempo', T('Faturamento por mês (R$)', 'Facturación por mes (R$)'), 'bar', [mes(m) for m in ms],
                                [dict(nome='R$', dados=[round(por_mes[m]) for m in ms])], 'brl', largura='inteira'))
    else:
        ds = sorted(por_dia)
        graficos.append(grafico('b_tempo', T('Faturamento por dia (R$)', 'Facturación por día (R$)'), 'bar', [_fmt_data(x)[:5] for x in ds],
                                [dict(nome='R$', dados=[round(por_dia[x]) for x in ds])], 'brl', largura='inteira'))
    for id_, titulo, dic, tipo, n in (('b_cli', T('Top 10 clientes (R$)', 'Top 10 clientes (R$)'), por_cli, 'hbar', 10),
                                      ('b_rep', T('Faturamento por representante (R$)', 'Facturación por representante (R$)'), por_rep, 'hbar', 10),
                                      ('b_seg', T('Por segmento (R$)', 'Por segmento (R$)'), por_seg, 'doughnut', 8),
                                      ('b_uf', T('Por UF (R$)', 'Por UF (R$)'), por_uf, 'doughnut', 8),
                                      ('b_op', T('Por operação (R$)', 'Por operación (R$)'), por_op, 'hbar', 8),
                                      ('b_art', T('Top 10 artigos (R$)', 'Top 10 artículos (R$)'), por_art, 'hbar', 10)):
        top = _top(dic, n)
        if top:
            graficos.append(grafico(id_, titulo, tipo, [k for k, _ in top], [dict(nome='R$', dados=[round(v) for _, v in top])], 'brl'))

    top_cli = _top(por_cli, 30)
    t_cli = tabela(T('Clientes — faturamento no período', 'Clientes — facturación en el período'),
                   [(T('Cliente', 'Cliente'), 'texto'), ('NFs', 'int'), (T(f'Quantidade ({un_main})', f'Cantidad ({un_main})'), 'int'),
                    (T('Faturamento líquido', 'Facturación neta'), 'brl'), (T('% do total', '% del total'), 'pct')],
                   [[k, len(cli_nf[k]), round(cli_q[k]), round(v), round(v / tot * 100, 1) if tot else 0] for k, v in top_cli])
    notas = defaultdict(lambda: dict(v=0.0, cli='', dt=None, op='', uf='', n=0))
    for x in sel:
        n_ = notas[(x['emp'], x['nf'])]
        n_.update(cli=x['cli'], dt=x['dt'], op=x['op'], uf=x['uf']); n_['v'] += x['v']; n_['n'] += 1
    ult = sorted(notas.items(), key=lambda kv: (kv[1]['dt'], str(kv[0][1])), reverse=True)[:40]
    t_nf = tabela(T('Últimas notas fiscais', 'Últimas notas fiscales'),
                  [('NF', 'texto'), (T('Data', 'Fecha'), 'data'), (T('Cliente', 'Cliente'), 'texto'), (T('Operação', 'Operación'), 'texto'), ('UF', 'texto'),
                   (T('Itens', 'Ítems'), 'int'), (T('Valor líquido', 'Valor neto'), 'brl2')],
                  [[str(k[1]), _fmt_data(v['dt']), v['cli'], v['op'], v['uf'], v['n'], round(v['v'], 2)] for k, v in ult])

    return dict(vazio=False, titulo='BI Comercial', filtros=[f_emp, f_rep, f_seg, f_cli], kpis=kpis, graficos=graficos, tabelas=[t_cli, t_nf],
                periodo=dict(inicio=di.isoformat(), fim=df.isoformat(), min=ini0.isoformat(), max=fim0.isoformat()))
