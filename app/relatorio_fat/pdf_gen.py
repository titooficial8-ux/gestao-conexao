"""Gera o Relatorio de Faturamento em PDF (reportlab), com as mesmas paginas do PowerPoint: 13,33 x 7,5 pol, logo da Conexao em todas."""
from __future__ import annotations

import io
from pathlib import Path

from reportlab.lib.colors import HexColor, white
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

ASSETS = Path(__file__).parent / 'assets'
W, H = 13.333 * 72, 7.5 * 72
AZUL_ESCURO, AZUL_MEDIO, AZUL_CLARO, CINZA_LINHA, CINZA_TEXTO = '#1F4E79', '#2E75B6', '#008EC9', '#F4F6F8', '#454C52'
TONS_TXT = {'pos': '#1B7F3B', 'neg': '#C62828', 'zero': '#6B7280'}
TONS_CARD = {'azul': '#1F4E79', 'laranja': '#B7791F', 'vermelho': '#C62828', 'verde': '#1B7F3B'}
CTX = {'gerado': '', 'pag': 0, 'total': 0}
FONTE, FONTE_B, FONTE_I = 'Helvetica', 'Helvetica-Bold', 'Helvetica-Oblique'


def _y(pol):
    """Coordenada do PowerPoint (polegadas, de cima para baixo) -> reportlab (pontos, de baixo para cima)."""
    return H - pol * 72


def _img(c, nome, x, y, w, h):
    c.drawImage(ImageReader(str(ASSETS / nome)), x * 72, _y(y + h), w * 72, h * 72, mask='auto')


def _txt(c, x, y, txt, tam, cor, fonte=FONTE, alinhar='l', largura=None):
    c.setFillColor(HexColor(cor))
    c.setFont(fonte, tam)
    px, py = x * 72, _y(y) - tam * 0.8
    if alinhar == 'r':
        c.drawRightString(px + (largura or 0) * 72, py, txt)
    elif alinhar == 'c':
        c.drawCentredString(px + (largura or 0) * 72 / 2, py, txt)
    else:
        c.drawString(px, py, txt)


def _quebrar(txt, fonte, tam, largura_pt):
    palavras, linhas, atual = txt.split(), [], ''
    for p in palavras:
        t = (atual + ' ' + p).strip()
        if stringWidth(t, fonte, tam) <= largura_pt or not atual:
            atual = t
        else:
            linhas.append(atual)
            atual = p
    return linhas + ([atual] if atual else [])


def _rodape(c, nota):
    """Faixa azul com o site e, a direita, o titulo do relatorio, a data/hora em que foi gerado e a pagina."""
    CTX['pag'] += 1
    c.setFillColor(HexColor(AZUL_CLARO))
    c.rect(0, 0, W, 0.21 * 72, stroke=0, fill=1)
    _txt(c, 0.4, 7.32, 'www.gestaoconexao.com.br', 9, '#FFFFFF')
    _txt(c, 5.4, 7.32, f'Relatório de Faturamento  ·  Gerado em {CTX["gerado"]}  ·  {CTX["pag"]}/{CTX["total"]}', 9, '#FFFFFF', FONTE, 'r', 7.55)
    if nota:
        _txt(c, 0.35, 6.98, nota, 9, '#6B7280', FONTE_I)


def _base(c, secao, titulo, nota):
    c.drawImage(ImageReader(str(ASSETS / 'conteudo_fundo.jpg')), 0, 0, W, H)
    _img(c, 'logo_colorida.png', 10.55, 0.18, 1.88, 0.68)                    # logo da Conexao em toda pagina
    _txt(c, 0.5, 0.22, secao, 13, AZUL_CLARO, FONTE_B)
    tam = 23 if len(titulo) <= 52 else (20 if len(titulo) <= 60 else 18)
    _txt(c, 0.5, 0.5, titulo, tam, AZUL_ESCURO, FONTE_B)
    _rodape(c, nota)


def _capa(c, p):
    c.drawImage(ImageReader(str(ASSETS / 'capa_fundo.jpg')), 0, 0, W, H)
    # a logo branca da Conexao ja faz parte da imagem de fundo da capa (no mesmo lugar do modelo)
    t = p['titulo'].upper()
    c.setFillColor(white)
    c.setFont(FONTE_B, 44)
    c.drawString(0.4 * 72, _y(1.32) - 36, t, charSpace=3) if False else None
    to = c.beginText(0.4 * 72, _y(1.32) - 40)
    to.setFont(FONTE_B, 44)
    to.setCharSpace(3)
    to.textLine(t)
    c.drawText(to)
    c.setStrokeColor(HexColor(AZUL_CLARO))
    c.setLineWidth(1.5)
    c.line(0, _y(2.23), 3.41 * 72, _y(2.23))
    to = c.beginText(0.4 * 72, _y(2.5) - 22)
    to.setFont(FONTE_B, 24)
    to.setCharSpace(1.5)
    to.textLine(p['sub'].upper())
    c.drawText(to)
    _rodape(c, None)


def _cel(c, x, y, w, h, valor, tam, cor, fundo, negrito=False, alinhar='r'):
    txt, tom = (valor if isinstance(valor, tuple) else (valor, None))
    c.setFillColor(HexColor(fundo))
    c.rect(x * 72, _y(y + h), w * 72, h * 72, stroke=0, fill=1)
    c.setStrokeColor(HexColor('#FFFFFF'))
    c.setLineWidth(0.5)
    c.rect(x * 72, _y(y + h), w * 72, h * 72, stroke=1, fill=0)
    forte = negrito or bool(tom and tom != 'zero')
    cc = TONS_TXT[tom] if tom and fundo not in (AZUL_ESCURO, AZUL_MEDIO) else cor
    fonte = FONTE_B if forte else FONTE
    py = _y(y + h / 2) - tam * 0.35
    c.setFillColor(HexColor(cc))
    c.setFont(fonte, tam)
    if alinhar == 'l':
        c.drawString(x * 72 + 5, py, str(txt))
    elif alinhar == 'c':
        c.drawCentredString((x + w / 2) * 72, py, str(txt))
    else:
        c.drawRightString((x + w) * 72 - 5, py, str(txt))


def _tabela(c, p, base=True):
    if base:
        _base(c, p['secao'], p['titulo'], p.get('nota'))
    linhas, total, grupos, cab, larg = p['linhas'], p.get('total'), p['grupos'], p['cab'], p['larguras']
    n_lin = 2 + len(linhas) + (1 if total else 0)
    alt = min(0.5, 5.65 / n_lin)
    tam = p.get('tam') or (12 if alt >= 0.38 else (11 if alt >= 0.33 else 10))
    x0, y0 = 0.35, 1.15
    xs = [x0]
    for w in larg:
        xs.append(xs[-1] + w)
    _cel(c, x0, y0, larg[0], alt, '', tam, '#FFFFFF', AZUL_ESCURO, True, 'l')
    j = 1
    for txt, n in grupos:
        _cel(c, xs[j], y0, xs[j + n] - xs[j], alt, txt, tam, '#FFFFFF', AZUL_ESCURO, True, 'c')
        j += n
    for j, h in enumerate(cab):
        _cel(c, xs[j], y0 + alt, larg[j], alt, h, tam, '#FFFFFF', AZUL_MEDIO, True, 'l' if j == 0 else 'r')
    for i, lin in enumerate(linhas):
        fundo = '#FFFFFF' if i % 2 else CINZA_LINHA
        for j, v in enumerate(lin):
            _cel(c, xs[j], y0 + alt * (2 + i), larg[j], alt, v, tam, CINZA_TEXTO, fundo, False, 'l' if j == 0 else 'r')
    if total:
        for j, v in enumerate(total):
            _cel(c, xs[j], y0 + alt * (n_lin - 1), larg[j], alt, v[0] if isinstance(v, tuple) else v, tam, '#FFFFFF', AZUL_ESCURO, True, 'l' if j == 0 else 'r')


def _card(c, x, y, w, h, fundo, borda=None, raio=10):
    c.setFillColor(HexColor(fundo))
    if borda:
        c.setStrokeColor(HexColor(borda))
        c.setLineWidth(0.75)
    c.roundRect(x * 72, _y(y + h), w * 72, h * 72, raio, stroke=1 if borda else 0, fill=1)


def _grafico_area(c, p, x0, y0, w, h):
    cats, series = p['cats'], p['series']
    maximo = max([v or 0 for _, vals, _ in series for v in vals] + [1])
    passo = 1
    for mult in (1, 10, 100, 1000, 10000, 100000, 1000000):                # passo "redondo" do eixo (1.000, 2.000, 2.500...)
        achou = next((b * mult for b in (1, 2, 2.5, 5) if maximo / (b * mult) <= 6), None)
        if achou:
            passo = achou
            break
    n = int(maximo // passo) + 1
    topo = passo * n
    for k in range(0, n + 1):
        v = passo * k
        yy = y0 + h - h * v / topo
        c.setStrokeColor(HexColor('#E3E8EF'))
        c.setLineWidth(0.5)
        c.line(x0 * 72, _y(yy), (x0 + w) * 72, _y(yy))
        _txt(c, x0 - 0.75, yy - 0.07, f'{v:,.0f}'.replace(',', '.'), 9, '#6B7280', FONTE, 'r', 0.65)
    gw = w / len(cats)
    bw = gw * 0.78 / len(series)
    for i, cat in enumerate(cats):
        for k, (_, vals, cor) in enumerate(series):
            v = vals[i]
            if not v:
                continue
            bh = h * v / topo
            bx = x0 + i * gw + gw * 0.11 + k * bw
            c.setFillColor(HexColor('#' + cor))
            c.rect(bx * 72, _y(y0 + h), bw * 72, bh * 72, stroke=0, fill=1)
            if k == len(series) - 1:
                _txt(c, bx - 0.15, y0 + h - bh - 0.18, f'{v:,.0f}'.replace(',', '.'), 8, '#10263F', FONTE_B, 'c', bw + 0.3)
        _txt(c, x0 + i * gw, y0 + h + 0.08, cat, 11, CINZA_TEXTO, FONTE, 'c', gw)
    lx = x0 + w / 2 - 2.4
    for nome, _, cor in series:
        c.setFillColor(HexColor('#' + cor))
        c.rect(lx * 72, _y(y0 + h + 0.5) - 8, 8, 8, stroke=0, fill=1)
        _txt(c, lx + 0.18, y0 + h + 0.38, nome, 11, CINZA_TEXTO)
        lx += 1.7


def _resumo(c, p):
    _base(c, p['secao'], p['titulo'], p.get('nota'))
    w, h, gap, x0, y0 = 2.0, 1.3, 0.12, 0.35, 1.15
    for i, (rot, val, nota, tom) in enumerate(p['cards']):
        x = x0 + i * (w + gap)
        _card(c, x, y0, w, h, '#FFFFFF', '#D6DEE8', 8)
        for k, linha in enumerate(_quebrar(rot, FONTE_B, 8, (w - 0.24) * 72)[:2]):
            _txt(c, x + 0.12, y0 + 0.1 + k * 0.15, linha, 8, '#6B7280', FONTE_B)
        _txt(c, x + 0.12, y0 + 0.5, val, 17, TONS_CARD[tom], FONTE_B)
        if nota:
            for k, linha in enumerate(_quebrar(nota, FONTE, 8, (w - 0.24) * 72)[:2]):
                _txt(c, x + 0.12, y0 + 0.92 + k * 0.14, linha, 8, '#6B7280')
    for i, (rot, val) in enumerate(p.get('extra') or []):
        x = x0 + i * 3.17
        _card(c, x, 2.62, 3.05, 0.5, '#EAF2FB', None, 8)
        _txt(c, x + 0.15, 2.76, rot, 9, '#6B7280', FONTE_B)
        _txt(c, x + 1.6, 2.74, val, 14, AZUL_ESCURO, FONTE_B, 'r', 1.3)
    _grafico_area(c, p, 1.05, 3.3, 11.9, 2.75)


def _grafico_pag(c, p):
    _base(c, p['secao'], p['titulo'], p.get('nota'))
    topo = 1.15
    cards = p.get('cards')
    if cards:
        n, gap = len(cards), 0.15
        w = (12.6 - gap * (n - 1)) / n
        for i, (rot, val, nota, tom) in enumerate(cards):
            x = 0.35 + i * (w + gap)
            _card(c, x, topo, w, 1.2, '#FFFFFF', '#D6DEE8', 8)
            _txt(c, x + 0.14, topo + 0.1, rot, 9, '#6B7280', FONTE_B)
            _txt(c, x + 0.14, topo + 0.47, val, 20, TONS_CARD[tom], FONTE_B)
            if nota:
                _txt(c, x + 0.14, topo + 0.9, nota, 9, '#6B7280')
        topo += 1.35
    for i, (rot, val) in enumerate(p.get('extra') or []):
        x = 0.35 + i * 3.17
        _card(c, x, topo, 3.05, 0.45, '#EAF2FB', None, 8)
        _txt(c, x + 0.12, topo + 0.14, rot, 8, '#6B7280', FONTE_B)
        _txt(c, x + 1.3, topo + 0.1, val, 13, AZUL_ESCURO, FONTE_B, 'r', 1.65)
    topo += 0.6
    _grafico_area(c, p, 1.05, topo + 0.1, 11.9, 6.2 - topo - 0.55)


def _cor_pct(v):
    return '#1F9D55' if v >= 1 else ('#D9922E' if v >= 0.8 else '#E04A4F')


def _intl(c, p):
    """Tabela por pais (esquerda) e, junto, as barras de realizado x plano do mes e do acumulado (direita)."""
    _base(c, p['secao'], p['titulo'], p.get('nota'))
    _tabela(c, p, base=False)
    x0, w, h = 7.45, 5.5, 2.75
    for k, (chip, itens) in enumerate(p['barras']):
        y = 1.15 + k * (h + 0.15)
        _card(c, x0, y, w, h, '#FFFFFF', '#D6DEE8', 8)
        _txt(c, x0 + 0.2, y + 0.14, 'Receita mercado externo — por país', 11, AZUL_ESCURO, FONTE_B)
        _card(c, x0 + w - 1.65, y + 0.1, 1.5, 0.28, '#DCEBFA' if k == 0 else AZUL_ESCURO, None, 7)
        _txt(c, x0 + w - 1.65, y + 0.17, chip, 9, AZUL_ESCURO if k == 0 else '#FFFFFF', FONTE_B, 'c', 1.5)
        n = max(len(itens), 1)
        lh = min(0.3, (h - 0.6) / n)
        for i, (pais, real, plano) in enumerate(itens):
            yy = y + 0.55 + i * lh
            _txt(c, x0 + 0.2, yy + lh / 2 - 0.07, pais, 9, AZUL_ESCURO, FONTE_B)
            tx, tw = x0 + 1.35, 2.2
            _card(c, tx, yy + lh / 2 - 0.05, tw, 0.1, '#E8ECF2', None, 3)
            frac = (real / plano) if plano else 0
            if plano:
                f = max(min(frac, 1.2) / 1.2, 0.012)
                _card(c, tx, yy + lh / 2 - 0.05, tw * f, 0.1, _cor_pct(frac), None, 3)
                c.setFillColor(HexColor('#10263F'))
                c.rect((tx + tw / 1.2) * 72, _y(yy + lh / 2 + 0.1), 1.5, 0.2 * 72, stroke=0, fill=1)
            else:
                _card(c, tx, yy + lh / 2 - 0.05, 0.03, 0.1, '#C3CAD6', None, 1)
            _txt(c, x0 + 3.65, yy + lh / 2 - 0.07, f'R$ {real / 1000:,.0f} mil'.replace(',', '.'), 9, AZUL_ESCURO, FONTE_B, 'r', 1.1)
            _txt(c, x0 + 4.5, yy + lh / 2 - 0.07, (f'{frac * 100:.0f}%' if plano else '–'), 10, _cor_pct(frac) if plano else '#6B7280', FONTE_B, 'r', 0.8)
        _txt(c, x0 + 0.2, y + h - 0.22, '>=100% do plano   80-99%   <80%   | = plano', 7, '#6B7280')


def gerar(paginas: list[dict], gerado_em: str = '') -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(W, H))
    c.setTitle('Relatório de Faturamento')
    CTX.update(gerado=gerado_em, pag=0, total=len(paginas))
    for p in paginas:
        {'capa': _capa, 'resumo': _resumo, 'tabela': _tabela, 'intl': _intl, 'grafico': _grafico_pag}[p['tipo']](c, p)
        c.showPage()
    c.save()
    return buf.getvalue()
