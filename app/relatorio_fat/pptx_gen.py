"""Gera o Relatorio de Faturamento em PowerPoint (.pptx), no modelo da Conexao (13,33 x 7,5 pol)."""
from __future__ import annotations

import io
from pathlib import Path

from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE, XL_LEGEND_POSITION, XL_LABEL_POSITION
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Emu, Inches, Pt

ASSETS = Path(__file__).parent / 'assets'
AZUL_ESCURO, AZUL_MEDIO, AZUL_CLARO, CINZA_LINHA, CINZA_TEXTO = '1F4E79', '2E75B6', '008EC9', 'F4F6F8', '454C52'
TONS_TXT = {'pos': '1B7F3B', 'neg': 'C62828', 'zero': '6B7280'}
TONS_CARD = {'azul': '1F4E79', 'laranja': 'B7791F', 'vermelho': 'C62828', 'verde': '1B7F3B'}


def _rgb(h):
    return RGBColor.from_string(h)


def _texto(slide, x, y, w, h, txt, tam, cor, negrito=False, italico=False, alinhar=PP_ALIGN.LEFT, ancora=MSO_ANCHOR.TOP, espaco=0):
    tb = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = tb.text_frame
    tf.word_wrap = True
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    tf.vertical_anchor = ancora
    p = tf.paragraphs[0]
    p.alignment = alinhar
    r = p.add_run()
    r.text = txt
    f = r.font
    f.name, f.size, f.bold, f.italic = 'Calibri', Pt(tam), negrito, italico
    f.color.rgb = _rgb(cor)
    if espaco:
        r._r.get_or_add_rPr().set('spc', str(espaco))
    return tb


def _retangulo(slide, x, y, w, h, cor, forma=MSO_SHAPE.RECTANGLE, borda=None):
    s = slide.shapes.add_shape(forma, Inches(x), Inches(y), Inches(w), Inches(h))
    s.fill.solid()
    s.fill.fore_color.rgb = _rgb(cor)
    if borda:
        s.line.color.rgb = _rgb(borda)
        s.line.width = Pt(0.75)
    else:
        s.line.fill.background()
    s.shadow.inherit = False
    return s


def _base_conteudo(prs, secao, titulo, nota):
    s = prs.slides.add_slide(prs.slide_layouts[6])
    s.shapes.add_picture(str(ASSETS / 'conteudo_fundo.jpg'), 0, 0, prs.slide_width, prs.slide_height)
    s.shapes.add_picture(str(ASSETS / 'logo_colorida.png'), Inches(10.55), Inches(0.18), Inches(1.88), Inches(0.68))      # logo da Conexao em toda pagina
    _texto(s, 0.5, 0.22, 9.5, 0.25, secao, 13, AZUL_CLARO, negrito=True)
    _texto(s, 0.5, 0.5, 9.9, 0.45, titulo, 23 if len(titulo) <= 52 else (20 if len(titulo) <= 60 else 18), AZUL_ESCURO, negrito=True)
    _retangulo(s, 0, 7.29, 13.333, 0.21, AZUL_CLARO)
    _texto(s, 0.4, 7.32, 6.0, 0.15, 'www.conexaomalhas.com.br', 9, 'FFFFFF')
    if nota:
        _texto(s, 0.35, 6.98, 12.6, 0.2, nota, 9, '6B7280', italico=True)
    return s


def _capa(prs, p):
    s = prs.slides.add_slide(prs.slide_layouts[6])
    s.shapes.add_picture(str(ASSETS / 'capa_fundo.jpg'), 0, 0, prs.slide_width, prs.slide_height)
    # a logo branca da Conexao ja faz parte da imagem de fundo da capa (no mesmo lugar do modelo)
    _texto(s, 0.4, 1.32, 11.5, 0.9, p['titulo'].upper(), 44, 'FFFFFF', negrito=True, espaco=300)
    ln = s.shapes.add_connector(1, Inches(0), Inches(2.23), Inches(3.41), Inches(2.23))
    ln.line.color.rgb = _rgb(AZUL_CLARO)
    ln.line.width = Pt(1.5)
    _texto(s, 0.4, 2.5, 11.5, 0.5, p['sub'].upper(), 24, 'FFFFFF', negrito=True, espaco=150)
    _retangulo(s, 0, 7.29, 13.333, 0.21, AZUL_CLARO)


def _celula(cel, valor, tam, cor, fundo, negrito=False, alinhar=PP_ALIGN.RIGHT):
    txt, tom = (valor if isinstance(valor, tuple) else (valor, None))
    cel.fill.solid()
    cel.fill.fore_color.rgb = _rgb(fundo)
    cel.margin_left = cel.margin_right = Emu(50800)
    cel.margin_top = cel.margin_bottom = Emu(12700)
    cel.vertical_anchor = MSO_ANCHOR.MIDDLE
    tf = cel.text_frame
    tf.text = ''
    p = tf.paragraphs[0]
    p.alignment = alinhar
    r = p.add_run()
    r.text = str(txt)
    r.font.name, r.font.size, r.font.bold = 'Calibri', Pt(tam), negrito or bool(tom and tom != 'zero')
    r.font.color.rgb = _rgb(TONS_TXT[tom] if tom and fundo not in (AZUL_ESCURO, AZUL_MEDIO) else cor)


def _tabela(prs, p):
    s = _base_conteudo(prs, p['secao'], p['titulo'], p.get('nota'))
    linhas, total, grupos, cab, larg = p['linhas'], p.get('total'), p['grupos'], p['cab'], p['larguras']
    n_lin = 2 + len(linhas) + (1 if total else 0)
    disponivel = 5.65
    alt = min(0.5, disponivel / n_lin)
    tam = 12 if alt >= 0.38 else (11 if alt >= 0.33 else 10)
    gf = s.shapes.add_table(n_lin, len(cab), Inches(0.35), Inches(1.15), Inches(sum(larg)), Inches(alt * n_lin))
    t = gf.table
    for i, w in enumerate(larg):
        t.columns[i].width = Inches(w)
    for r in t.rows:
        r.height = Inches(alt)
    # linha 0: grupos (a 1a coluna fica em branco)
    _celula(t.cell(0, 0), '', tam, 'FFFFFF', AZUL_ESCURO, True, PP_ALIGN.LEFT)
    c0 = 1
    for txt, n in grupos:
        t.cell(0, c0).merge(t.cell(0, c0 + n - 1))
        _celula(t.cell(0, c0), txt, tam, 'FFFFFF', AZUL_ESCURO, True, PP_ALIGN.CENTER)
        c0 += n
    for j, h in enumerate(cab):
        _celula(t.cell(1, j), h, tam, 'FFFFFF', AZUL_MEDIO, True, PP_ALIGN.LEFT if j == 0 else PP_ALIGN.RIGHT)
    for i, lin in enumerate(linhas):
        fundo = 'FFFFFF' if i % 2 else CINZA_LINHA
        for j, v in enumerate(lin):
            _celula(t.cell(2 + i, j), v, tam, CINZA_TEXTO, fundo, False, PP_ALIGN.LEFT if j == 0 else PP_ALIGN.RIGHT)
    if total:
        for j, v in enumerate(total):
            txt = v[0] if isinstance(v, tuple) else v
            _celula(t.cell(n_lin - 1, j), txt, tam, 'FFFFFF', AZUL_ESCURO, True, PP_ALIGN.LEFT if j == 0 else PP_ALIGN.RIGHT)


def _kpis(prs, p):
    s = _base_conteudo(prs, p['secao'], p['titulo'], None)
    w, h, gap, x0, y0 = 3.95, 2.05, 0.3, 0.5, 1.3
    for i, (rot, val, nota, tom) in enumerate(p['cards']):
        x, y = x0 + (i % 3) * (w + gap), y0 + (i // 3) * (h + gap)
        _retangulo(s, x, y, w, h, 'FFFFFF', MSO_SHAPE.ROUNDED_RECTANGLE, borda='D6DEE8').adjustments[0] = 0.06
        _texto(s, x + 0.25, y + 0.2, w - 0.5, 0.5, rot, 11, '6B7280', negrito=True)
        _texto(s, x + 0.25, y + 0.78, w - 0.5, 0.6, val, 30, TONS_CARD[tom], negrito=True)
        if nota:
            _texto(s, x + 0.25, y + 1.5, w - 0.5, 0.35, nota, 11, '6B7280')
    for i, (rot, val) in enumerate(p.get('extra') or []):
        x = x0 + i * 3.1
        _retangulo(s, x, 5.85, 2.9, 0.95, 'EAF2FB', MSO_SHAPE.ROUNDED_RECTANGLE).adjustments[0] = 0.12
        _texto(s, x + 0.2, 5.95, 2.5, 0.25, rot, 10, '6B7280', negrito=True)
        _texto(s, x + 0.2, 6.25, 2.5, 0.45, val, 20, AZUL_ESCURO, negrito=True)


def _grafico(prs, p):
    s = _base_conteudo(prs, p['secao'], p['titulo'], p.get('nota'))
    cd = CategoryChartData()
    cd.categories = p['cats']
    for nome, vals, _ in p['series']:
        cd.add_series(nome, vals)
    gf = s.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(0.5), Inches(1.2), Inches(12.3), Inches(5.6), cd)
    ch = gf.chart
    ch.has_legend = True
    ch.legend.position = XL_LEGEND_POSITION.BOTTOM
    ch.legend.include_in_layout = False
    ch.legend.font.size, ch.legend.font.name = Pt(12), 'Calibri'
    ch.font.name, ch.font.size = 'Calibri', Pt(11)
    va = ch.value_axis
    va.has_major_gridlines = True
    va.major_gridlines.format.line.color.rgb = _rgb('E3E8EF')
    va.format.line.fill.background()
    va.tick_labels.number_format, va.tick_labels.number_format_is_linked = '#,##0', False
    va.tick_labels.font.size = Pt(11)
    ch.category_axis.tick_labels.font.size = Pt(12)
    ch.plots[0].gap_width, ch.plots[0].overlap = 60, -5
    for ser, (_, _, cor) in zip(ch.plots[0].series, p['series']):
        ser.format.fill.solid()
        ser.format.fill.fore_color.rgb = _rgb(cor)
    real = ch.plots[0].series[-1]                                 # rotulos so na serie do ano atual
    real.data_labels.show_value = True
    real.data_labels.number_format, real.data_labels.number_format_is_linked = '#,##0', False
    real.data_labels.font.size, real.data_labels.font.bold = Pt(10), True
    real.data_labels.position = XL_LABEL_POSITION.OUTSIDE_END


def gerar(paginas: list[dict]) -> bytes:
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    for p in paginas:
        {'capa': _capa, 'kpis': _kpis, 'tabela': _tabela, 'grafico': _grafico}[p['tipo']](prs, p)
    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()
