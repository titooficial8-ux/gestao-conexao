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
from pptx.enum.chart import XL_CHART_TYPE, XL_LEGEND_POSITION
from pptx.opc.constants import RELATIONSHIP_TYPE as RT
from pptx.util import Emu, Pt

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
    """Titulos com o mes de referencia (Out/2026) e a hora do 'gerado em'; o resto do texto fica como no modelo."""
    ref = f'({MESES_ABREV[mes - 1]}/{ano})'
    for sh in slide.shapes:
        if not sh.has_text_frame:
            continue
        for par in sh.text_frame.paragraphs:
            t = ''.join(r.text for r in par.runs)
            n = re.sub(r'\([A-Za-zç]{3}/\d{4}\)', ref, t)
            n = re.sub(r'gerado em \d{2}/\d{2}/\d{4} \d{2}:\d{2}', f'gerado em {agora}', n)
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


def _grafico(slide, ant, ano, mes, ant_vals, real_vals, casas=0, fmt=None):
    ch = next(s for s in slide.shapes if s.has_chart).chart
    cd = CategoryChartData(number_format=fmt) if fmt else CategoryChartData()
    cd.categories = MESES_ABREV
    cd.add_series(str(ant), [round(v, casas) if casas else round(v) for v in ant_vals])
    cd.add_series(str(ano), [(round(v, casas) if casas else round(v)) if (m + 1) <= mes else None for m, v in enumerate(real_vals)])
    ch.replace_data(cd)
    if fmt:                                                         # rotulos e eixo no mesmo formato (o PowerPoint em portugues mostra o ponto de milhar)
        for pl in ch.plots:
            if pl.has_data_labels:
                pl.data_labels.number_format, pl.data_labels.number_format_is_linked = fmt, False
        ch.value_axis.tick_labels.number_format, ch.value_axis.tick_labels.number_format_is_linked = fmt, False



def _n1(v) -> str:
    return f'{v:.1f}'.replace('.', ',')


def _pct1(v) -> str:
    return f'{v * 100:+.1f}%'.replace('.', ',')


def _agora_txt(slide, agora):
    for sh in slide.shapes:
        if sh.has_text_frame:
            for par in sh.text_frame.paragraphs:
                t = ''.join(r.text for r in par.runs)
                n = re.sub(r'gerado em \d{2}/\d{2}/\d{4} \d{2}:\d{2}', f'gerado em {agora}', t)
                if n != t:
                    _set_par(par, n)


def _anos_txt(slide, ano, ant):
    """Troca 2026/2025 pelos anos do periodo nos textos do slide (titulos, subtitulos e chips)."""
    if (ano, ant) == (2026, 2025):
        return
    for sh in slide.shapes:
        if sh.has_text_frame:
            for par in sh.text_frame.paragraphs:
                t = ''.join(r.text for r in par.runs)
                n = t.replace('2026', '§A').replace('2025', '§B').replace('§A', str(ano)).replace('§B', str(ant))
                if n != t:
                    _set_par(par, n)


def _txt_apos(slide, rotulo, novo, cor=None, rotulo_novo=None, nota=None, nota_cor=None, exato=False):
    """Acha o texto-rotulo do cartao e troca o valor (texto seguinte) e, se houver, a nota (texto logo abaixo do valor)."""
    shapes = list(slide.shapes)
    for i, sh in enumerate(shapes):
        t = sh.text_frame.text.strip().upper() if sh.has_text_frame else ''
        if sh.has_text_frame and sh.top > 1000000 and sh.height < 400000 and (t == rotulo.upper() if exato else t.startswith(rotulo.upper())):      # so rotulos de cartao/chip (nao o titulo do slide nem as notas)
            if rotulo_novo:
                _set_par(sh.text_frame.paragraphs[0], rotulo_novo)
            val = shapes[i + 1]
            _set_par(val.text_frame.paragraphs[0], novo)
            if cor and val.text_frame.paragraphs[0].runs:
                val.text_frame.paragraphs[0].runs[0].font.color.rgb = RGBColor.from_string(cor)
            if nota is not None and i + 2 < len(shapes):
                n = shapes[i + 2]
                if n.has_text_frame and n.left == val.left and n.top > val.top:
                    _set_par(n.text_frame.paragraphs[0], nota)
                    if nota_cor and n.text_frame.paragraphs[0].runs:
                        n.text_frame.paragraphs[0].runs[0].font.color.rgb = RGBColor.from_string(nota_cor)
            return i
    return None


def _barra_cartao(slide, rotulo, cor):
    """Tarja colorida no topo do cartao (retangulo fino logo antes do rotulo)."""
    shapes = list(slide.shapes)
    for i, sh in enumerate(shapes):
        if sh.has_text_frame and sh.top > 1000000 and sh.text_frame.text.strip().upper().startswith(rotulo.upper()):
            for j in range(i - 1, max(i - 3, 0), -1):
                r = shapes[j]
                if r.shape_type == 1 and r.height < 60000:
                    r.fill.solid()
                    r.fill.fore_color.rgb = RGBColor.from_string(cor)
            return


def _grafico_kpi(slide, serie, mes, escala, casas):
    """Troca os dados do grafico combinado (barras do ano, linha do ano anterior e do plano) mantendo a formatacao do modelo."""
    ch = next(sh for sh in slide.shapes if sh.has_chart).chart
    nomes = [se.name for pl in ch.plots for se in pl.series]
    r = lambda v: round(v / escala, casas)
    cd = CategoryChartData()
    cd.categories = MESES_ABREV
    cd.add_series(nomes[0], [r(x['real']) if x['mes'] <= mes and x['real'] else None for x in serie])
    cd.add_series(nomes[1], [r(x['ant']) for x in serie])
    cd.add_series(nomes[2], [r(x['plano']) for x in serie])
    ch.replace_data(cd)


def _kpi_receita(slide, c, serie, mes, ano, ant, agora):
    br = lambda v: f'R$ {mil(v)} mil'
    _anos_txt(slide, ano, ant)
    _txt_apos(slide, 'RECEITA BRUTA', br(c['receita_bruta_sem_dev']))
    _txt_apos(slide, 'RECEITA BRUTA (MENOS', br(c['receita_bruta']))
    _txt_apos(slide, 'DEDUÇÕES', br(c['deducoes']), nota=f'{c["deducoes_pct"] * 100:.0f}% da bruta')
    _txt_apos(slide, 'DEVOLUÇÕES (BRUTO)', br(c['devolucao_bruta']), nota=f'{_n1(c["devolucao_bruta"] / c["receita_bruta_sem_dev"] * 100 if c["receita_bruta_sem_dev"] else 0)}% da bruta')
    _txt_apos(slide, 'RECEITA LÍQUIDA', br(c['receita_liquida']), nota=f'após devoluções e impostos · vs plano: {sinal(c["v_plano"])}')
    falta = c['plano'] - c['receita_liquida']
    cor = VERMELHO if falta > 0 else VERDE
    rot = 'FALTA PARA BATER O PLANO DO MÊS' if falta > 0 else 'SUPEROU O PLANO DO MÊS'
    _txt_apos(slide, 'FALTA PARA BATER' if falta > 0 else 'SUPEROU O PLANO', br(abs(falta)), cor=cor, rotulo_novo=rot,
              nota=f'plano: {br(c["plano"])} · {_n1(c["receita_liquida"] / c["plano"] * 100 if c["plano"] else 0)}% atingido', nota_cor=None)
    _barra_cartao(slide, rot, cor)
    _chips(slide, serie, mes, br, ant)
    _grafico_kpi(slide, serie, mes, 1000, 3)
    _agora_txt(slide, agora)


def _chips(slide, serie, mes, fmt, ant):
    ate = [x for x in serie if x['mes'] <= mes]
    real, plano, ant = (sum(x[k] or 0 for x in ate) for k in ('real', 'plano', 'ant'))
    pc = real / plano * 100 if plano else 0
    va = (real / ant - 1) if ant else 0
    _txt_apos(slide, 'ACUMULADO REALIZADO', fmt(real))
    _txt_apos(slide, 'PLANO ACUMULADO', fmt(plano))
    _txt_apos(slide, '% DO PLANO', f'{pc:.0f}%', cor=VERDE if pc >= 100 else VERMELHO)
    _txt_apos(slide, f'VS {ant}', _pct1(va), cor=VERDE if va >= 0 else VERMELHO, exato=True)


def _kpi_metros(slide, m, serie, mes, ano, ant, agora):
    mt = lambda v: f'{n0(v)} mts'
    _anos_txt(slide, ano, ant)
    _txt_apos(slide, 'MTS TOTAIS', mt(m['totais']))
    _txt_apos(slide, 'DEVOLUÇÕES', (f'-{mt(m["devolvidos"])}' if m['devolvidos'] > 0 else mt(m['devolvidos'])), nota=f'{_n1(m["pct_dev"] * 100)}% do total')
    _txt_apos(slide, 'LÍQUIDO DA DEVOLUÇÃO', mt(m['liquido']), nota=f'vs plano: {sinal(m["liquido"] / m["plano"] - 1 if m["plano"] else 0)}')
    falta = m['falta']
    cor = VERMELHO if falta > 0 else VERDE
    rot = 'FALTA PARA BATER O PLANO DO MÊS (MTS)' if falta > 0 else 'SUPEROU O PLANO DO MÊS (MTS)'
    _txt_apos(slide, 'FALTA PARA BATER' if falta > 0 else 'SUPEROU O PLANO', mt(abs(falta)), cor=cor, rotulo_novo=rot,
              nota=f'plano: {mt(m["plano"])} · {_n1(m["pct_plano"] * 100)}% atingido')
    _barra_cartao(slide, rot, cor)
    _chips(slide, serie, mes, mt, ant)
    _grafico_kpi(slide, serie, mes, 1000, 3)
    _agora_txt(slide, agora)


def gerar(d: dict, agora: str, *_ignorado) -> bytes:
    """d = dados do slide mensal (app.totvs.routes.dados_do_slide). Abre o PowerPoint-modelo e troca os numeros; slides/blocos sem fonte de dados ficam como estao."""
    prs = Presentation(str(MODELO))
    S = prs.slides
    mes, ano, ant = d['mes'], d['ano'], d['ano_ant']
    mon = MESES_ABREV[mes - 1].upper()
    cor = lambda v: VERDE if (v or 0) >= 0 else VERMELHO

    # 2 - Receita por segmento (R$ mil)
    s = S[1]; tb = _tb(s)
    _tabela(s, tb, _por_rotulo(d['receita']), _colunas_8(mil), d['receita_total'])
    _cabecalho(tb, mes, ano); _textos(s, mes, ano, agora)
    tm, ta = d['receita_total']['mes'], d['receita_total']['acum']
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
    for base_, b in ((1, 'mes'), (4, 'acum')):
        cs += [(base_, lambda L, b=b: L[b]['ant'], 'n', mil), (base_ + 1, lambda L, b=b: L[b]['real'], 'n', mil), (base_ + 2, lambda L, b=b: L[b]['var'], 'v', None)]
    _tabela(s, tb, _por_rotulo(d['ortobom']), cs, d['ortobom_total'], vazio='-')
    _cabecalho(tb, mes, ano); _textos(s, mes, ano, agora)
    po = d['ortobom_plano']
    _cartao(s, 'META (OUT', [f'META ({mon})', mil(po['mes']['real']), f'{sinal(po["mes"]["var"])} vs. meta'], cor(po['mes']['var']))
    _cartao(s, 'META (ACUM', [None, mil(po['acum']['real']), f'{sinal(po["acum"]["var"])} vs. meta'], cor(po['acum']['var']))

    # 7 - Representantes (mil metros)
    s = S[6]; tb = _tb(s)
    cs = []
    for base_, b in ((1, 'mes'), (5, 'acum')):
        cs += [(base_, lambda L, b=b: L[b]['ant'], 'n', mil), (base_ + 1, lambda L, b=b: L[b]['plano'], 'n', mil), (base_ + 2, lambda L, b=b: L[b]['real'], 'n', mil),
               (base_ + 3, lambda L, b=b: L[b]['v_ant'], 'v', None)]
    _tabela(s, tb, _por_rotulo(d['representantes']), cs, d['representantes_total'])
    _cabecalho(tb, mes, ano); _textos(s, mes, ano, agora)

    # 18 - Sucata e segunda qualidade (R$ mil)
    s = S[17]; tb = _tb(s)
    pr = _por_rotulo(d['receita'])
    sel = [pr[k] for k in ('SEGUNDA QUALIDADE', 'SUCATA') if k in pr]
    if sel:
        from app.totvs.slide import _var
        tot = {b: dict(ant=sum(x[b]['ant'] for x in sel), plano=sum(x[b]['plano'] for x in sel), real=sum(x[b]['real'] for x in sel)) for b in ('mes', 'acum')}
        for b in tot:
            tot[b]['v_ant'], tot[b]['v_plano'] = _var(tot[b]['real'], tot[b]['ant']), _var(tot[b]['real'], tot[b]['plano'])
        _tabela(s, tb, _por_rotulo(sel), _colunas_8(mil), dict(rotulo='TOTAL', **tot))
        _cabecalho(tb, mes, ano); _textos(s, mes, ano, agora)

    # 39 - Mercado internacional por pais (R$ mil)
    s = S[38]; tb = _tb(s)
    cs = []
    for base_, b in ((1, 'mes'), (4, 'acum')):
        cs += [(base_, lambda L, b=b: L[b]['plano'], 'n', mil), (base_ + 1, lambda L, b=b: L[b]['real'], 'n', mil), (base_ + 2, lambda L, b=b: L[b]['var'], 'v', None)]
    _tabela(s, tb, _por_rotulo(d['internacional']), cs, d['internacional_total'])
    _cabecalho(tb, mes, ano); _textos(s, mes, ano, agora)
    it = d['internacional_total']
    _cartao(s, 'PLANO (OUT', [f'PLANO ({mon})', f'R$ {mil(it["mes"]["plano"])}K', f'Realizado: R$ {mil(it["mes"]["real"])}K ({sinal(it["mes"]["var"])})'], cor(it['mes']['var']))
    _cartao(s, 'PLANO (ACUM', [None, f'R$ {mil(it["acum"]["plano"])}K', f'Realizado: R$ {mil(it["acum"]["real"])}K ({sinal(it["acum"]["var"])})'], cor(it['acum']['var']))

    # Slides de indicadores + grafico (cartoes, chips e grafico de barras/linhas): consolidado, colchao, MI e ME
    esc, escm = d.get('cab_escopos') or {}, d.get('cab_mt_escopos') or {}
    if esc.get('total') and d.get('serie_mensal'):
        _kpi_receita(S[7], esc['total'], d['serie_mensal'], mes, ano, ant, agora)
    if escm.get('total') and d.get('serie_metros'):
        _kpi_metros(S[8], escm['total'], d['serie_metros'], mes, ano, ant, agora)
    if esc.get('colchao') and d.get('serie_colchao'):
        _kpi_receita(S[9], esc['colchao'], d['serie_colchao'], mes, ano, ant, agora)
    if esc.get('mi') and d.get('serie_mi'):
        _kpi_receita(S[24], esc['mi'], d['serie_mi'], mes, ano, ant, agora)
    if escm.get('mi') and d.get('serie_mi_mt'):
        _kpi_metros(S[25], escm['mi'], d['serie_mi_mt'], mes, ano, ant, agora)
    if esc.get('me') and d.get('serie_me'):
        _kpi_receita(S[39], esc['me'], d['serie_me'], mes, ano, ant, agora)
    if escm.get('me') and d.get('serie_me_mt'):
        _kpi_metros(S[40], escm['me'], d['serie_me_mt'], mes, ano, ant, agora)

    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()
