"""Apresentacao de Vendas em tempo real: abre o PowerPoint-modelo (app/seed_data/vendas_modelo.pptx) e troca os numeros pelos do Gestao (mesmos dados do BI Comercial).

So os slides que a gente consegue calcular sao preenchidos (mantem fonte, cores, posicao e as 'pilulas' verde/vermelha de variacao); os demais ficam exatamente como estao no modelo.
Slides cobertos: 2 Receita, 3 Volume, 4 Preco medio (segmentos), 6 Ortobom, 7 Representantes, 8 a 10 (graficos mensais de receita/volume/receita de colchao), 13 Sucata e 2a qualidade."""
from __future__ import annotations

import copy
import io
import re
import unicodedata
from pathlib import Path

from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor

MODELO = Path(__file__).parent / 'seed_data' / 'vendas_modelo.pptx'
VERDE, VERMELHO = '1B5E20', 'B71C1C'
MESES_ABREV = ['Jan', 'Fev', 'Mar', 'Abr', 'Mai', 'Jun', 'Jul', 'Ago', 'Set', 'Out', 'Nov', 'Dez']


def _k(t) -> str:
    return re.sub(r'\s+', ' ', unicodedata.normalize('NFKD', str(t or '')).encode('ascii', 'ignore').decode().upper()).strip()


def n0(v) -> str:
    return f'{round(v or 0):,}'.replace(',', '.')


def mil(v) -> str:
    return n0((v or 0) / 1000)


def n2(v) -> str:
    return f'{(v or 0):,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')


def sinal(v) -> str:
    return f'{(v or 0) * 100:+.0f}%'


# ---------------------------------------------------------------- texto (mantem a formatacao do 1o trecho)
def _set_par(par, texto: str, ref=None):
    runs = par.runs
    if runs:
        runs[0].text = texto
        for r in runs[1:]:
            r._r.getparent().remove(r._r)
        return
    modelo = (ref.runs[0]._r if ref is not None and ref.runs else None)
    if modelo is None:
        par.add_run().text = texto
        return
    novo = copy.deepcopy(modelo)
    fim = par._p.find('{http://schemas.openxmlformats.org/drawingml/2006/main}endParaRPr')
    (fim.addprevious(novo) if fim is not None else par._p.append(novo))
    par.runs[0].text = texto


def _cell(cell, texto: str, ref=None):
    _set_par(cell.text_frame.paragraphs[0], texto, ref.text_frame.paragraphs[0] if ref is not None else None)


# ---------------------------------------------------------------- pilulas de variacao
def _caixa_tabela(tb):
    return tb.left, tb.top, tb.left + tb.width, tb.top + tb.height


def _pilulas_da_tabela(slide, tb):
    l, t, r, b = _caixa_tabela(tb)
    return [s for s in slide.shapes if s.name.startswith('Rounded Rectangle') and s.has_text_frame and s.width < 800000 and l <= s.left <= r and t <= s.top <= b]


def _nova_pilula(slide, modelo_xml, texto, cor, cx, top):
    el = copy.deepcopy(modelo_xml)
    slide.shapes._spTree.append(el)
    sh = slide.shapes[-1]
    ids = [int(x.shape_id) for x in slide.shapes if str(x.shape_id).isdigit()]
    el.nvSpPr.cNvPr.set('id', str(max(ids) + 1))
    el.nvSpPr.cNvPr.set('name', f'Rounded Rectangle {max(ids) + 1}')
    sh.width = 127000 + 86614 * len(texto)
    sh.left = int(cx - sh.width / 2)
    sh.top = int(top)
    sh.fill.solid()
    sh.fill.fore_color.rgb = RGBColor.from_string(cor)
    _set_par(sh.text_frame.paragraphs[0], texto)
    return sh


_PILULA = {}                                                            # prototipo da pilula (tirado da 1a pagina que tem uma)


def _tabela(slide, tb, linhas: dict, cols: list, total=None, vazio='+0%'):
    """linhas: {rotulo normalizado: dict}. cols: [(indice da coluna, funcao(linha)->valor, 'n'|'v', formatador)].
    Coluna 'v' = variacao: vira pilula (verde/vermelha) quando ha numero; senao texto simples ('+0%' nas linhas, '-' no total)."""
    t = tb.table
    pil = _pilulas_da_tabela(slide, tb)
    if pil and 'x' not in _PILULA:
        _PILULA['x'] = copy.deepcopy(pil[0]._element)
    for p in pil:
        p._element.getparent().remove(p._element)
    larg = [c.width for c in t.columns]
    alt = [r.height for r in t.rows]
    novas = []
    for ri, row in enumerate(t.rows):
        if ri < 2:
            continue
        rot = _k(row.cells[0].text)
        eh_total = rot.startswith('TOTAL')
        L = total if eh_total else linhas.get(rot)
        if L is None:
            continue
        ref_txt = row.cells[1]
        for ci, fn, tipo, fmt in cols:
            c = row.cells[ci]
            v = fn(L)
            if tipo == 'n':
                _cell(c, fmt(v), ref_txt)
                continue
            if eh_total or not v:
                _cell(c, '-' if (eh_total and not v) else (sinal(v) if eh_total else vazio), ref_txt)
                continue
            _cell(c, '', ref_txt)                                       # a pilula desenhada por cima faz o papel do texto
            cx = tb.left + sum(larg[:ci]) + larg[ci] / 2 + 33020
            topo = tb.top + sum(alt[:ri]) + (alt[ri] - 216535) / 2
            novas.append((sinal(v), VERDE if v > 0 else VERMELHO, cx, topo))
    modelo = _PILULA.get('x')
    for texto, cor, cx, topo in novas:
        if modelo is not None:
            _nova_pilula(slide, modelo, texto, cor, cx, topo)


def _cabecalho(tb, mes, ano):
    mon = MESES_ABREV[mes - 1].upper()
    for ri in (0, 1):
        for c in tb.table.rows[ri].cells:
            t = c.text
            if not t.strip():
                continue
            n = re.sub(r'\b[A-ZÇ]{3}/\d{4}', f'{mon}/{ano}', t).replace('2026', '§A').replace('2025', '§B').replace('§A', str(ano)).replace('§B', str(ano - 1))
            if n != t:
                _cell(c, n)


def _textos(slide, mes, ano, agora):
    ref = f'({MESES_ABREV[mes - 1]}/{ano})'
    for sh in slide.shapes:
        if not sh.has_text_frame:
            continue
        for par in sh.text_frame.paragraphs:
            t = ''.join(r.text for r in par.runs)
            n = re.sub(r'\([A-Za-zç]{3}/\d{4}\)', ref, t)
            if 'Fonte:' in t:
                n = re.sub(r'Fonte:.*?(?=Observa|$)', f'Fonte: TOTVS em tempo real (Relatório 150 e planilha de planos), gerado em {agora}. ', n).strip()
            if n != t:
                _set_par(par, n)


def _cartao(slide, inicio: str, linhas: list, cor=None):
    for sh in slide.shapes:
        if sh.has_text_frame and sh.text_frame.text.strip().upper().startswith(inicio.upper()):
            ps = sh.text_frame.paragraphs
            for i, txt in enumerate(linhas):
                if txt is not None and i < len(ps):
                    _set_par(ps[i], txt)
            if cor and len(ps) > 2 and ps[2].runs:
                ps[2].runs[0].font.color.rgb = RGBColor.from_string(cor)
            return True
    return False


def _tb(slide):
    return next(s for s in slide.shapes if s.has_table)


def _por_rotulo(lista):
    return {_k(x['rotulo']): x for x in lista}


def _colunas_8(fmt):
    cs = []
    for base, b in ((1, 'mes'), (6, 'acum')):
        cs += [(base, lambda L, b=b: L[b]['ant'], 'n', fmt), (base + 1, lambda L, b=b: L[b]['plano'], 'n', fmt), (base + 2, lambda L, b=b: L[b]['real'], 'n', fmt),
               (base + 3, lambda L, b=b: L[b]['v_ant'], 'v', None), (base + 4, lambda L, b=b: L[b]['v_plano'], 'v', None)]
    return cs


def _grafico(slide, ant, ano, mes, ant_vals, real_vals, casas=0):
    ch = next(s for s in slide.shapes if s.has_chart).chart
    cd = CategoryChartData()
    cd.categories = MESES_ABREV
    cd.add_series(str(ant), [round(v, casas) if casas else round(v) for v in ant_vals])
    cd.add_series(str(ano), [(round(v, casas) if casas else round(v)) if (m + 1) <= mes else None for m, v in enumerate(real_vals)])
    ch.replace_data(cd)


def gerar(d: dict, agora: str) -> bytes:
    """d = dados do slide mensal (app.totvs.routes.dados_do_slide). Devolve o .pptx preenchido."""
    prs = Presentation(str(MODELO))
    S = prs.slides
    mes, ano, ant = d['mes'], d['ano'], d['ano_ant']
    mon = MESES_ABREV[mes - 1].upper()

    # 2 - Receita por segmento (R$ mil)
    s = S[1]; tb = _tb(s)
    _tabela(s, tb, _por_rotulo(d['receita']), _colunas_8(mil), d['receita_total'])
    _cabecalho(tb, mes, ano); _textos(s, mes, ano, agora)
    tm, ta = d['receita_total']['mes'], d['receita_total']['acum']
    cor = lambda v: VERDE if (v or 0) >= 0 else VERMELHO
    _cartao(s, 'PLANO (OUT', [f'PLANO ({mon})', f'R$ {mil(tm["plano"])}K', f'Realizado: R$ {mil(tm["real"])}K ({sinal(tm["v_plano"])})'], cor(tm['v_plano']))
    _cartao(s, 'PLANO (ACUM', [None, f'R$ {mil(ta["plano"])}K', f'Realizado: R$ {mil(ta["real"])}K ({sinal(ta["v_plano"])})'], cor(ta['v_plano']))

    # 3 - Volume (mil metros)
    s = S[2]; tb = _tb(s)
    _tabela(s, tb, _por_rotulo(d['metros']), _colunas_8(mil), d['metros_total'])
    _cabecalho(tb, mes, ano); _textos(s, mes, ano, agora)

    # 4 - Preco medio por segmento (R$/mt)
    s = S[3]; tb = _tb(s)
    _tabela(s, tb, _por_rotulo(d['preco_medio']), _colunas_8(lambda v: 'R$ ' + n2(v)))
    _cabecalho(tb, mes, ano); _textos(s, mes, ano, agora)

    # 6 - Ortobom por filial (mil metros)
    s = S[5]; tb = _tb(s)
    cs = []
    for base, b in ((1, 'mes'), (4, 'acum')):
        cs += [(base, lambda L, b=b: L[b]['ant'], 'n', mil), (base + 1, lambda L, b=b: L[b]['real'], 'n', mil), (base + 2, lambda L, b=b: L[b]['var'], 'v', None)]
    _tabela(s, tb, _por_rotulo(d['ortobom']), cs, d['ortobom_total'], vazio='-')
    _cabecalho(tb, mes, ano); _textos(s, mes, ano, agora)
    po = d['ortobom_plano']
    _cartao(s, 'META (OUT', [f'META ({mon})', mil(po['mes']['real']), f'{sinal(po["mes"]["var"])} vs. meta'], cor(po['mes']['var']))
    _cartao(s, 'META (ACUM', [None, mil(po['acum']['real']), f'{sinal(po["acum"]["var"])} vs. meta'], cor(po['acum']['var']))

    # 7 - Representantes (mil metros)
    s = S[6]; tb = _tb(s)
    cs = []
    for base, b in ((1, 'mes'), (5, 'acum')):
        cs += [(base, lambda L, b=b: L[b]['ant'], 'n', mil), (base + 1, lambda L, b=b: L[b]['plano'], 'n', mil), (base + 2, lambda L, b=b: L[b]['real'], 'n', mil),
               (base + 3, lambda L, b=b: L[b]['v_ant'], 'v', None)]
    _tabela(s, tb, _por_rotulo(d['representantes']), cs, d['representantes_total'])
    _cabecalho(tb, mes, ano); _textos(s, mes, ano, agora)

    # 8, 9, 10 - graficos mensais (ano anterior x ano atual)
    sm = d['serie_mensal']
    s = S[7]; _grafico(s, ant, ano, mes, [x['ant'] / 1000 for x in sm], [x['real'] / 1000 for x in sm]); _textos(s, mes, ano, agora)
    sc = d.get('serie_colchao_mt')
    if sc:
        s = S[8]; _grafico(s, ant, ano, mes, [x['ant'] / 1000 for x in sc], [x['real'] / 1000 for x in sc], 1); _textos(s, mes, ano, agora)
    sr = d.get('serie_colchao')
    if sr:
        s = S[9]; _grafico(s, ant, ano, mes, [x['ant'] / 1000 for x in sr], [x['real'] / 1000 for x in sr]); _textos(s, mes, ano, agora)

    # 13 - Sucata e segunda qualidade (R$ mil)
    s = S[12]; tb = _tb(s)
    pr = _por_rotulo(d['receita'])
    sel = [pr[k] for k in ('SEGUNDA QUALIDADE', 'SUCATA') if k in pr]
    if sel:
        def soma(b, k):
            return sum(x[b][k] for x in sel)
        from app.totvs.slide import _var
        tot = {b: dict(ant=soma(b, 'ant'), plano=soma(b, 'plano'), real=soma(b, 'real')) for b in ('mes', 'acum')}
        for b in tot:
            tot[b]['v_ant'], tot[b]['v_plano'] = _var(tot[b]['real'], tot[b]['ant']), _var(tot[b]['real'], tot[b]['plano'])
        _tabela(s, tb, _por_rotulo(sel), _colunas_8(mil), dict(rotulo='TOTAL', **tot))
        _cabecalho(tb, mes, ano); _textos(s, mes, ano, agora)

    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()
