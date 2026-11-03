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


CINZA = '64748B'


def _clonar_base(prs, origem, secao: str, titulo: str, nota: str):
    """Slide novo com o mesmo fundo, logo, cabecalho e rodape do slide `origem` (sem o grafico), com titulo e nota proprios."""
    novo = prs.slides.add_slide(origem.slide_layout)
    for sh in list(novo.shapes):
        sh._element.getparent().remove(sh._element)
    for sh in origem.shapes:
        if sh.has_chart:
            continue
        el = copy.deepcopy(sh._element)
        for blip in el.iter('{http://schemas.openxmlformats.org/drawingml/2006/main}blip'):
            rid = blip.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed')
            if rid:
                nova = novo.part.relate_to(origem.part.related_part(rid), RT.IMAGE)
                blip.set('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed', nova)
        novo.shapes._spTree.append(el)
    for sh in novo.shapes:
        if sh.has_text_frame and sh.name == 'TextBox 4':
            _set_par(sh.text_frame.paragraphs[0], secao)
        elif sh.has_text_frame and sh.name == 'TextBox 5':
            _set_par(sh.text_frame.paragraphs[0], titulo)
        elif sh.has_text_frame and sh.name == 'TextBox 9':
            _set_par(sh.text_frame.paragraphs[0], nota)
    return novo


def _cartao_novo(slide, modelo_xml, x, y, rotulo, valor, nota=None, tom=None):
    el = copy.deepcopy(modelo_xml)
    slide.shapes._spTree.append(el)
    sh = slide.shapes[-1]
    ids = [int(x_.shape_id) for x_ in slide.shapes if str(x_.shape_id).isdigit()]
    el.nvSpPr.cNvPr.set('id', str(max(ids) + 1))
    el.nvSpPr.cNvPr.set('name', f'Rounded Rectangle {max(ids) + 1}')
    sh.left, sh.top = int(x), int(y)
    ps = sh.text_frame.paragraphs
    _set_par(ps[0], rotulo)
    _set_par(ps[1], valor)
    _set_par(ps[2], nota or '')
    if ps[2].runs:
        ps[2].runs[0].font.color.rgb = RGBColor.from_string({'verde': VERDE, 'vermelho': VERMELHO}.get(tom, CINZA))
    return sh


def _grafico_novo(slide, x, y, w, h, cats, series):
    cd = CategoryChartData()
    cd.categories = cats
    for nome, vals, _ in series:
        cd.add_series(nome, vals)
    ch = slide.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, Emu(int(x)), Emu(int(y)), Emu(int(w)), Emu(int(h)), cd).chart
    ch.has_legend, ch.legend.position, ch.legend.include_in_layout = True, XL_LEGEND_POSITION.BOTTOM, False
    ch.legend.font.size = Pt(10)
    ch.category_axis.tick_labels.font.size = Pt(10)
    ch.value_axis.tick_labels.font.size = Pt(9)
    ch.value_axis.major_gridlines.format.line.color.rgb = RGBColor.from_string('E2E8F0')
    ch.value_axis.format.line.fill.background()
    ch.plots[0].gap_width, ch.plots[0].overlap = 60, -5
    for se, (_, _, cor) in zip(ch.plots[0].series, series):
        se.format.fill.solid()
        se.format.fill.fore_color.rgb = RGBColor.from_string(cor)
    return ch


def _slide_resumo(prs, base, cartao_xml, secao, titulo, nota, cards, extras, cats, series):
    s = _clonar_base(prs, base, secao, titulo, nota)
    xs, ys = (320040, 3170040, 6020040), (1051560, 2314560)
    for i, c in enumerate(cards[:6]):
        _cartao_novo(s, cartao_xml, xs[i % 3], ys[i // 3], *c)
    for i, c in enumerate(extras[:4]):
        _cartao_novo(s, cartao_xml, 9144000, 1051560 + i * 1263000, *c)
    _grafico_novo(s, 320040, 3560000, 8620000, 2780000, cats, series)
    return s



def _colocar_fotos(s, fotos: list, lado_a_lado: bool, X=320040, Y=1051560, W=11548872, H=5248440, GAP=120000):
    """Poe as fotos dos paineis do site no slide, sem distorcer: empilhadas ou lado a lado, centralizadas na area util."""
    from PIL import Image
    tam = [Image.open(io.BytesIO(b)).size for b in fotos]
    if lado_a_lado:
        w = (W - GAP * (len(fotos) - 1)) / len(fotos)
        for i, (b, (pw_, ph_)) in enumerate(zip(fotos, tam)):
            esc = min(w / pw_, H / ph_)
            ww, hh = pw_ * esc, ph_ * esc
            s.shapes.add_picture(io.BytesIO(b), Emu(int(X + i * (w + GAP) + (w - ww) / 2)), Emu(int(Y)), Emu(int(ww)), Emu(int(hh)))
        return
    altura = sum(W * p_[1] / p_[0] for p_ in tam) + GAP * (len(fotos) - 1)
    f = min(1.0, H / altura)
    y = Y
    for b, (pw_, ph_) in zip(fotos, tam):
        ww, hh = W * f, W * f * ph_ / pw_
        s.shapes.add_picture(io.BytesIO(b), Emu(int(X + (W - ww) / 2)), Emu(int(y)), Emu(int(ww)), Emu(int(hh)))
        y += hh + GAP * f


def _slide_fotos(prs, base, secao, titulo, nota, fotos: list, lado_a_lado: bool):
    s = _clonar_base(prs, base, secao, titulo, nota)
    _colocar_fotos(s, fotos, lado_a_lado)
    return s


def _trocar_grafico_por_fotos(slide, fotos: list):
    """No slide do modelo, o grafico simples sai e entram os paineis do site (cartoes + grafico com plano e ano anterior), na mesma posicao."""
    for sh in list(slide.shapes):
        if sh.has_chart:
            X, Y, W, H = sh.left, sh.top, sh.width, sh.height
            sh._element.getparent().remove(sh._element)
            _colocar_fotos(slide, fotos, False, X, Y, W, H)
            return True
    return False


def _trocar_tabela_por_foto(slide, foto: bytes, largura_total: bool):
    """Tira a tabela do modelo (com as pilulas, divisorias e bolinhas por cima) e poe a mesma tabela do site, como aparece na tela."""
    from PIL import Image
    tb = next((s for s in slide.shapes if s.has_table), None)
    if tb is None:
        return False
    l, t, r, b = tb.left, tb.top, tb.left + tb.width, tb.top + tb.height
    X, Y = tb.left, tb.top
    W = 11548872 if largura_total else tb.width
    for sh in list(slide.shapes):
        if sh is tb or sh._element is tb._element:
            continue
        dentro = l - 50000 <= sh.left <= r and t - 50000 <= sh.top <= b + 200000
        pequeno = sh.width < 800000 and sh.height < 300000                           # pilulas de variacao e bolinhas de status
        divisoria = sh.width < 40000 and sh.height > 1000000                          # linha vertical entre mes e acumulado
        if dentro and (pequeno or divisoria) and not sh.has_chart and sh.shape_type != 13:
            sh._element.getparent().remove(sh._element)
    tb._element.getparent().remove(tb._element)
    pw_, ph_ = Image.open(io.BytesIO(foto)).size
    esc = min(W / pw_, 5248440 / ph_)
    slide.shapes.add_picture(io.BytesIO(foto), Emu(int(X)), Emu(int(Y)), Emu(int(pw_ * esc)), Emu(int(ph_ * esc)))
    return True


def serie_do_modelo(indice_slide: int, nome: str):
    """Valores (12 meses) de uma serie do grafico de um slide do proprio modelo: usada so para anos antigos que nao estao nas notas do TOTVS (vieram de outra planilha)."""
    prs = Presentation(str(MODELO))
    for sh in prs.slides[indice_slide].shapes:
        if sh.has_chart:
            for pl in sh.chart.plots:
                for se in pl.series:
                    if str(se.name) == str(nome):
                        return [float(v) * 1000 if v is not None else 0.0 for v in se.values]                    # o modelo guarda em R$ mil; o grafico do site usa R$
    return None


def gerar(d: dict, agora: str, fotos: dict | None = None, graficos: dict | None = None) -> bytes:
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
    sv = d.get('serie_metros')                                     # mesma serie do grafico "Volume em mts por mes" do BI Comercial (todos os segmentos em metros)
    if sv:
        s = S[8]
        _grafico(s, ant, ano, mes, [x['ant'] for x in sv], [x['real'] for x in sv], fmt='#,##0')
        for sh in s.shapes:
            if sh.has_text_frame and sh.name == 'TextBox 5':
                _set_par(sh.text_frame.paragraphs[0], 'Volume em mts por mês — 2025 x 2026'.replace('2025', str(ant)).replace('2026', str(ano)))
            elif sh.has_text_frame and sh.name == 'TextBox 9':
                _set_par(sh.text_frame.paragraphs[0], f'Volume em metros por mês (colchão, automotivo, calçado, colchão M.E. e moveleiro), igual ao gráfico do BI Comercial. Fonte: TOTVS em tempo real (Relatório 150 e planilha de planos), gerado em {agora}.')
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

    # 31 - Mercado internacional por pais (R$ mil)
    s = S[30]; tb = _tb(s)
    cs = []
    for base_, b in ((1, 'mes'), (4, 'acum')):
        cs += [(base_, lambda L, b=b: L[b]['plano'], 'n', mil), (base_ + 1, lambda L, b=b: L[b]['real'], 'n', mil), (base_ + 2, lambda L, b=b: L[b]['var'], 'v', None)]
    _tabela(s, tb, _por_rotulo(d['internacional']), cs, d['internacional_total'])
    _cabecalho(tb, mes, ano); _textos(s, mes, ano, agora)
    it = d['internacional_total']
    _cartao(s, 'PLANO (OUT', [f'PLANO ({mon})', f'R$ {mil(it["mes"]["plano"])}K', f'Realizado: R$ {mil(it["mes"]["real"])}K ({sinal(it["mes"]["var"])})'], cor(it['mes']['var']))
    _cartao(s, 'PLANO (ACUM', [None, f'R$ {mil(it["acum"]["plano"])}K', f'Realizado: R$ {mil(it["acum"]["real"])}K ({sinal(it["acum"]["var"])})'], cor(it['acum']['var']))

    base = S[7]
    novos = []
    if fotos:                                                       # indicadores do BI Comercial: fotos dos proprios paineis do site, em tempo real
        sec = 'PAINÉIS DO BI COMERCIAL (SITE)'
        rf = f'Imagens dos painéis do BI Comercial em tempo real (TOTVS), geradas em {agora}.'
        pares = [(('seg_mes', 'seg_acum'), f'Receita por segmento — {MESES_ABREV[mes - 1]}/{ano}', True), (('ext_mes', 'ext_acum'), f'Receita mercado externo por país — {MESES_ABREV[mes - 1]}/{ano}', True)]
        for chaves, tit, lado in pares:
            imgs = [fotos[k] for k in chaves if k in fotos]
            if imgs:
                novos.append(_slide_fotos(prs, base, sec, tit, rf, imgs, lado))
    else:
        # Novos (indicadores do BI Comercial): resumo do mes em R$ e em metros, logo depois da capa
        c, tm_, ta_ = d['cabecalho'], d['receita_total']['mes'], d['receita_total']['acum']
        falta = tm_['plano'] - tm_['real']
        pdev = (c['devolucao_bruta'] / c['receita_bruta_sem_dev'] * 100) if c['receita_bruta_sem_dev'] else 0
        virg = lambda x: f'{x:.1f}'.replace('.', ',')
        cards = [('RECEITA BRUTA', f'R$ {mil(c["receita_bruta_sem_dev"])}', None, None), ('RECEITA BRUTA (MENOS DEVOL.)', f'R$ {mil(c["receita_bruta"])}', None, None),
                 ('DEDUÇÕES (IMPOSTOS)', f'R$ {mil(c["deducoes"])}', f'{c["deducoes_pct"] * 100:.0f}% da bruta', None), ('DEVOLUÇÕES (BRUTO)', f'R$ {mil(c["devolucao_bruta"])}', f'{virg(pdev)}% da bruta', 'vermelho'),
                 ('RECEITA LÍQUIDA', f'R$ {mil(c["receita_liquida"])}', f'vs plano: {sinal(tm_["v_plano"])}', 'verde' if (tm_['v_plano'] or 0) >= 0 else 'vermelho'),
                 ('META DO MÊS SUPERADA' if falta <= 0 else 'FALTA PARA O PLANO DO MÊS', f'R$ {mil(abs(falta))}', f'plano: R$ {mil(tm_["plano"])} · {virg(tm_["real"] / tm_["plano"] * 100 if tm_["plano"] else 0)}% atingido',
                  'verde' if falta <= 0 else 'vermelho')]
        extras = [('ACUMULADO REALIZADO', f'R$ {mil(ta_["real"])}', None, None), ('PLANO ACUMULADO', f'R$ {mil(ta_["plano"])}', None, None),
                  ('% DO PLANO', f'{(ta_["real"] / ta_["plano"] * 100 if ta_["plano"] else 0):.0f}%', None, None), (f'VS {ant}', sinal(ta_['v_ant']), None, 'verde' if (ta_['v_ant'] or 0) >= 0 else 'vermelho')]
        sm = d['serie_mensal']
        ser = [(str(ant), [round(x['ant'] / 1000) for x in sm], '9AA6BC'), ('Plano', [round(x['plano'] / 1000) for x in sm], '1F9D55'),
               (str(ano), [round(x['real'] / 1000) if x['real'] else None for x in sm], '2E75B6')]
        cartao_xml = copy.deepcopy(next(sh for sh in S[1].shapes if sh.has_text_frame and sh.text_frame.text.upper().startswith('PLANO (ACUM'))._element)
        r1 = _slide_resumo(prs, base, cartao_xml, 'VENDAS CONSOLIDADO', f'Resumo do mês — {MESES_ABREV[mes - 1]}/{ano} (R$ mil)',
                           f'Valores em R$ mil. Fonte: TOTVS em tempo real (Relatório 150 e planilha de planos), gerado em {agora}. Barras do ano atual só até o último mês com notas emitidas.', cards, extras, MESES_ABREV, ser)
        cm_, tmt = d.get('cabecalho_metros'), d['metros_total']['acum']
        novos = [r1]
        if cm_ and d.get('serie_metros'):
            fm = cm_['falta']
            cards2 = [('MTS TOTAIS', f'{n0(cm_["totais"])} mts', None, None), ('DEVOLUÇÕES', f'{n0(cm_["devolvidos"])} mts', f'{virg(cm_["pct_dev"] * 100)}% do total', 'vermelho'),
                      ('LÍQUIDO DA DEVOLUÇÃO', f'{n0(cm_["liquido"])} mts', f'vs plano: {sinal(cm_["liquido"] / cm_["plano"] - 1 if cm_["plano"] else 0)}', 'verde' if cm_['liquido'] >= cm_['plano'] else 'vermelho'),
                      ('META DE MTS SUPERADA' if fm <= 0 else 'FALTA PARA O PLANO (MTS)', f'{n0(abs(fm))} mts', f'plano: {n0(cm_["plano"])} mts · {virg(cm_["pct_plano"] * 100)}% atingido', 'verde' if fm <= 0 else 'vermelho')]
            extras2 = [('ACUMULADO REALIZADO', f'{n0(tmt["real"])} mts', None, None), ('PLANO ACUMULADO', f'{n0(tmt["plano"])} mts', None, None),
                       ('% DO PLANO', f'{(tmt["real"] / tmt["plano"] * 100 if tmt["plano"] else 0):.0f}%', None, None), (f'VS {ant}', sinal(tmt['v_ant']), None, 'verde' if (tmt['v_ant'] or 0) >= 0 else 'vermelho')]
            sv = d['serie_metros']
            ser2 = [(str(ant), [round(x['ant'] / 1000, 1) for x in sv], '9AA6BC'), ('Plano', [round(x['plano'] / 1000, 1) for x in sv], '1F9D55'),
                    (str(ano), [round(x['real'] / 1000, 1) if x['real'] else None for x in sv], '2E75B6')]
            novos.append(_slide_resumo(prs, base, cartao_xml, 'VENDAS CONSOLIDADO', f'Volume em metros — resumo do mês ({MESES_ABREV[mes - 1]}/{ano})',
                                       f'Volume em mil metros (colchão, automotivo, calçado, colchão M.E. e moveleiro). Fonte: TOTVS em tempo real, gerado em {agora}.', cards2, extras2, MESES_ABREV, ser2))
    # os slides do modelo ficam na ordem original (2 = Receita, 3 = Volume...); os paineis do site entram no fim, depois do ultimo slide do modelo

    if fotos:                                                       # tabelas dos slides 2, 3, 4, 6, 7 e 31: iguais as tabelas do site
        for idx, chave, larga in ((1, 't_seg', False), (2, 't_mt', True), (3, 't_pm', True), (5, 't_orto', False), (6, 't_rep', True), (30, 't_int', False)):
            if fotos.get(chave):
                _trocar_tabela_por_foto(S[idx], fotos[chave], larga)
    if graficos:                                                    # slides 10, 20, 32 e 33: graficos no estilo do site (series do TOTVS)
        for idx, chave in ((9, 'colchao'), (19, 'mi'), (20, 'mi3'), (31, 'me'), (32, 'me_mt')):
            if graficos.get(chave):
                if idx != 9:
                    _textos(S[idx], mes, ano, agora)
                if chave == 'mi3' and graficos.get('_nota_mi3'):
                    for sh in S[idx].shapes:
                        if sh.has_text_frame and sh.name == 'TextBox 9':
                            _set_par(sh.text_frame.paragraphs[0], graficos['_nota_mi3'].replace('{agora}', agora))
                _trocar_grafico_por_fotos(S[idx], [graficos[chave]])
    if fotos:                                                       # 8 e 9: iguais ao painel do site (cartoes + grafico com plano e ano anterior)
        if fotos.get('graf_receita'):
            _trocar_grafico_por_fotos(S[7], [fotos[k] for k in ('kpi_receita', 'graf_receita') if k in fotos])
        if fotos.get('graf_metros'):
            _trocar_grafico_por_fotos(S[8], [fotos[k] for k in ('kpi_metros', 'graf_metros') if k in fotos])

    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()
