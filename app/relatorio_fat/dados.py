"""Monta, a partir do slide mensal de receita (app.totvs.slide.montar), a lista de paginas do Relatorio de Faturamento.

Cada pagina e um dict simples; os geradores de PowerPoint e de PDF desenham a mesma lista, entao os dois arquivos saem iguais.
  capa     : titulo, sub
  kpis     : secao, titulo, cards [(rotulo, valor, nota, tom)]
  tabela   : secao, titulo, grupos [(texto, n_colunas)], cab [str], linhas [[celulas]], total [celulas] | None, larguras [pol], nota
  grafico  : secao, titulo, cats, series [(nome, valores, cor)], nota
Celula = texto ou (texto, tom) com tom em 'pos', 'neg', 'zero' (cor do texto das variacoes).
"""
from __future__ import annotations

MESES = ['Janeiro', 'Fevereiro', 'Março', 'Abril', 'Maio', 'Junho', 'Julho', 'Agosto', 'Setembro', 'Outubro', 'Novembro', 'Dezembro']
ABREV = [m[:3] for m in MESES]


def n0(v) -> str:
    """Inteiro com ponto de milhar (pt-BR), sem decimais."""
    return f'{round(v or 0):,}'.replace(',', '.')


def n2(v) -> str:
    return f'{(v or 0):,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')


def mil(v) -> str:
    return n0((v or 0) / 1000)


def pct(v):
    """Variacao como na planilha: +14%, -35%; com a cor do texto."""
    v = v or 0
    t = f'{v * 100:+.0f}%'
    return (t, 'zero' if round(v * 100) == 0 else ('pos' if v > 0 else 'neg'))


def _tem(l, *campos) -> bool:
    return any(abs((l[b].get(c) or 0)) > 1e-9 for b in ('mes', 'acum') for c in campos)


def _tabela_8(secao, titulo, linhas, total, d, unidade, fmt, nota, rot_col='Segmento'):
    """Mes e acumulado com ano anterior, plano, realizado e variacao contra o plano."""
    ano, ant = d['ano'], d['ano_ant']
    lin = []
    for l in linhas:
        if not _tem(l, 'ant', 'plano', 'real'):
            continue
        lin.append([l['rotulo']] + [c for b in ('mes', 'acum') for c in (fmt(l[b]['ant']), fmt(l[b]['plano']), fmt(l[b]['real']), pct(l[b]['v_plano']))])
    tot = None
    if total:
        tot = [total['rotulo']] + [c for b in ('mes', 'acum') for c in (fmt(total[b]['ant']), fmt(total[b]['plano']), fmt(total[b]['real']), pct(total[b]['v_plano']))]
    return dict(tipo='tabela', secao=secao, titulo=titulo,
                grupos=[(f'{MESES[d["mes"] - 1].upper()}/{ano}  {unidade}', 4), (f'ACUMULADO {ano}  {unidade}', 4)],
                cab=[rot_col] + [str(ant), 'Plano', 'Realizado', 'Var.'] * 2, linhas=lin, total=tot,
                larguras=[2.6] + [1.0, 1.0, 1.1, 0.8] * 2, nota=nota)


def montar_paginas(d: dict) -> list[dict]:
    mes, ano, ant = d['mes'], d['ano'], d['ano_ant']
    nome_mes = MESES[mes - 1]
    ref = f'{ABREV[mes - 1]}/{ano}'                                # nos titulos: Set/2026, como no modelo
    paginas = [dict(tipo='capa', titulo='Relatório de Faturamento', sub=f'{nome_mes} de {ano}')]

    c = d['cabecalho']
    tm, ta = d['receita_total']['mes'], d['receita_total']['acum']
    plano_mes = tm['plano']
    atingido = (tm['real'] / plano_mes * 100) if plano_mes else 0
    falta = plano_mes - tm['real']
    pct_dev = (c['devolucao_bruta'] / c['receita_bruta_sem_dev'] * 100) if c['receita_bruta_sem_dev'] else 0
    paginas.append(dict(
        tipo='resumo', secao='RELATÓRIO DE FATURAMENTO', titulo=f'Resumo do mês — {nome_mes}/{ano} (R$ mil)',
        cards=[
            ('RECEITA BRUTA', f'R$ {mil(c["receita_bruta_sem_dev"])}', None, 'azul'),
            ('RECEITA BRUTA (MENOS DEVOLUÇÃO)', f'R$ {mil(c["receita_bruta"])}', None, 'azul'),
            ('DEDUÇÕES (IMPOSTOS)', f'R$ {mil(c["deducoes"])}', f'{c["deducoes_pct"] * 100:.0f}% da bruta', 'laranja'),
            ('DEVOLUÇÕES (BRUTO)', f'R$ {mil(c["devolucao_bruta"])}', f'{pct_dev:.1f}% da bruta'.replace('.', ','), 'vermelho'),
            ('RECEITA LÍQUIDA (DESCONTADAS DEVOLUÇÕES E IMPOSTOS)', f'R$ {mil(c["receita_liquida"])}', f'vs plano: {pct(tm["v_plano"])[0]}', 'verde'),
            ('META DO MÊS SUPERADA' if falta <= 0 else 'FALTA PARA BATER O PLANO DO MÊS', f'R$ {mil(abs(falta))}',
             f'plano: R$ {mil(plano_mes)} · {atingido:.1f}% atingido'.replace('.', ','), 'verde' if falta <= 0 else 'vermelho'),
        ],
        extra=[('ACUMULADO REALIZADO', f'R$ {mil(ta["real"])}'), ('PLANO ACUMULADO', f'R$ {mil(ta["plano"])}'),
               ('% DO PLANO', f'{(ta["real"] / ta["plano"] * 100 if ta["plano"] else 0):.0f}%'), (f'VS {ant}', pct(ta['v_ant'])[0])],
        cats=ABREV, series=[(str(ant), [round(x['ant'] / 1000) for x in d['serie_mensal']], '9AA6BC'), ('Plano', [round(x['plano'] / 1000) for x in d['serie_mensal']], '1F9D55'),
                            (str(ano), [round(x['real'] / 1000) if x['real'] else None for x in d['serie_mensal']], '2E75B6')],
        nota='Valores em milhares de R$ (R$ mil). Fonte: Relatório 150 do TOTVS (realizado) e planilha de planos. Barras do ano atual só até o último mês com notas emitidas.'))

    paginas.append(_tabela_8('FATURAMENTO', f'Receita líquida — Plano x Realizado por Segmento ({ref})', d['receita'], d['receita_total'], d, '(R$ mil)', mil,
                             'Receita líquida em R$ mil. Fonte: Relatório 150 do TOTVS (realizado) e planilha de planos (BPLANO).'))

    sm = d.get('serie_metros')
    if sm:
        tm_ = d['metros_total']['acum']
        paginas.append(dict(
            tipo='grafico', secao='VOLUME', titulo=f'Volume em metros por mês — {ano}', cats=ABREV,
            series=[(str(ant), [round(x['ant']) for x in sm], '9AA6BC'), ('Plano', [round(x['plano']) for x in sm], '1F9D55'),
                    (str(ano), [round(x['real']) if x['real'] else None for x in sm], '2E75B6')],
            extra=[('ACUMULADO REALIZADO', f'{n0(tm_["real"])} m'), ('PLANO ACUMULADO', f'{n0(tm_["plano"])} m'),
                   ('% DO PLANO', f'{(tm_["real"] / tm_["plano"] * 100 if tm_["plano"] else 0):.0f}%'), (f'VS {ant}', pct(tm_['v_ant'])[0])],
            nota='Volume em metros por mês (colchão, automotivo, calçado, colchão M.E. e moveleiro). Fonte: Relatório 150 do TOTVS e planilha de planos.'))
    paginas.append(_tabela_8('VOLUME', f'Volume em metros — Plano x Realizado por Segmento ({ref})', d['metros'], d['metros_total'], d, '(metros)', n0,
                             'Volume em metros. Fonte: Relatório 150 do TOTVS e planilha de planos.'))
    if any(_tem(l, 'ant', 'plano', 'real') for l in d['quilos']):
        paginas.append(_tabela_8('VOLUME', f'Volume em quilos ({ref})', d['quilos'], d['quilos_total'], d, '(kg)', n0, 'Volume em quilos. Fonte: Relatório 150 do TOTVS.'))
    paginas.append(_tabela_8('PREÇO MÉDIO', f'Preço médio líquido por segmento — R$/metro ({ref})', d['preco_medio'], None, d, '(R$/mt)', n2,
                             'Preço médio líquido = receita líquida ÷ volume do segmento.'))
    paginas.append(_tabela_8('REPRESENTANTES', f'Representantes — Volume em Metros de Colchão ({ref})', d['representantes'], d['representantes_total'], d, '(metros)', n0,
                             'Volume em metros de colchão por representante.', rot_col='Representante'))

    # Ortobom: ano anterior x realizado (sem plano por filial)
    lin = [[l['rotulo']] + [c for b in ('mes', 'acum') for c in (n0(l[b]['ant']), n0(l[b]['real']), pct(l[b]['var']))] for l in d['ortobom'] if _tem(l, 'ant', 'real')]
    ot = d['ortobom_total']
    pl = d['ortobom_plano']
    tot_o = ['TOTAL EM METROS'] + [c for b in ('mes', 'acum') for c in (n0(ot[b]['ant']), n0(ot[b]['real']), pct(ot[b]['var']))]
    lin.append(['PLANO'] + [c for b in ('mes', 'acum') for c in ('', n0(pl[b]['real']), pct(pl[b]['var']))])
    paginas.append(dict(tipo='tabela', secao='ORTOBOM', titulo=f'Ortobom por filial — Volume em Metros ({ref})',
                        grupos=[(f'{nome_mes.upper()}/{ano}  (metros)', 3), (f'ACUMULADO {ano}  (metros)', 3)],
                        cab=['Filial'] + [str(ant), 'Realizado', 'Var.'] * 2, linhas=lin, total=tot_o, larguras=[3.2] + [1.35, 1.45, 1.0] * 2,
                        nota='Variação do realizado contra o mesmo período do ano anterior; a linha PLANO compara o total com o plano.'))

    it = d['internacional_total']
    lin = [[l['rotulo']] + [c for b in ('mes', 'acum') for c in (mil(l[b]['plano']), mil(l[b]['real']), pct(l[b]['var']))] for l in d['internacional'] if _tem(l, 'plano', 'real')]
    tot_i = ['TOTAL R$'] + [c for b in ('mes', 'acum') for c in (mil(it[b]['plano']), mil(it[b]['real']), pct(it[b]['var']))]

    def barras(b):
        itens = [(l['rotulo'], l[b]['real'] or 0, l[b]['plano'] or 0) for l in d['internacional'] if _tem(l, 'plano', 'real') and (l[b]['plano'] or l[b]['real'])]
        return sorted(itens, key=lambda x: (-x[2], -x[1]))
    paginas.append(dict(tipo='intl', secao='MERCADO INTERNACIONAL', titulo=f'Mercado internacional — Receita líquida por país ({ref})',
                        grupos=[(f'{nome_mes.upper()}/{ano}  (R$ mil)', 3), (f'ACUMULADO {ano}  (R$ mil)', 3)],
                        cab=['País'] + ['Plano', 'Realizado', 'Var.'] * 2, linhas=lin, total=tot_i, larguras=[1.45] + [0.9, 1.05, 0.8] * 2, tam=11,
                        barras=[(f'{nome_mes} / {ano}', barras('mes')), (f'Acumulado {ano}', barras('acum'))],
                        nota='Receita líquida em R$ mil por país de destino, contra o plano. Barra = realizado ÷ plano; a marca vertical é 100% do plano.'))
    return paginas
