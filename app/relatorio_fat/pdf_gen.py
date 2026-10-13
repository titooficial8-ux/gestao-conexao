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
    c.setFillColor(HexColor(AZUL_CLARO))
    c.rect(0, 0, W, 0.21 * 72, stroke=0, fill=1)
    _txt(c, 0.4, 7.32, 'www.conexaomalhas.com.br', 9, '#FFFFFF')
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
    c.setFillColor(HexColor(AZUL_CLARO))
    c.rect(0, 0, W, 0.21 * 72, stroke=0, fill=1)


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


def _tabela(c, p):
    _base(c, p['secao'], p['titulo'], p.get('nota'))
    linhas, total, grupos, cab, larg = p['linhas'], p.get('total'), p['grupos'], p['cab'], p['larguras']
    n_lin = 2 + len(linhas) + (1 if total else 0)
    alt = min(0.5, 5.65 / n_lin)
    tam = 12 if alt >= 0.38 else (11 if alt >= 0.33 else 10)
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


def _kpis(c, p):
    _base(c, p['secao'], p['titulo'], None)
    w, h, gap, x0, y0 = 3.95, 2.05, 0.3, 0.5, 1.3
    for i, (rot, val, nota, tom) in enumerate(p['cards']):
        x, y = x0 + (i % 3) * (w + gap), y0 + (i // 3) * (h + gap)
        _card(c, x, y, w, h, '#FFFFFF', '#D6DEE8')
        for k, linha in enumerate(_quebrar(rot, FONTE_B, 11, (w - 0.5) * 72)[:2]):
            _txt(c, x + 0.25, y + 0.2 + k * 0.2, linha, 11, '#6B7280', FONTE_B)
        _txt(c, x + 0.25, y + 0.78, val, 30, TONS_CARD[tom], FONTE_B)
        if nota:
            _txt(c, x + 0.25, y + 1.5, nota, 11, '#6B7280')
    for i, (rot, val) in enumerate(p.get('extra') or []):
        x = x0 + i * 3.1
        _card(c, x, 5.85, 2.9, 0.95, '#EAF2FB', None, 8)
        _txt(c, x + 0.2, 5.95, rot, 10, '#6B7280', FONTE_B)
        _txt(c, x + 0.2, 6.25, val, 20, AZUL_ESCURO, FONTE_B)


def _grafico(c, p):
    _base(c, p['secao'], p['titulo'], p.get('nota'))
    cats, series = p['cats'], p['series']
    x0, y0, w, h = 1.1, 1.45, 11.7, 4.7                                  # area do grafico (pol)
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
        _txt(c, 0.35, yy - 0.08, f'{v:,.0f}'.replace(',', '.'), 10, '#6B7280', FONTE, 'r', 0.65)
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
                _txt(c, bx - 0.15, y0 + h - bh - 0.2, f'{v:,.0f}'.replace(',', '.'), 8, '#10263F', FONTE_B, 'c', bw + 0.3)
        _txt(c, x0 + i * gw, y0 + h + 0.1, cat, 12, CINZA_TEXTO, FONTE, 'c', gw)
    lx = 4.3
    for nome, _, cor in series:
        c.setFillColor(HexColor('#' + cor))
        c.rect(lx * 72, _y(6.62) - 8, 8, 8, stroke=0, fill=1)
        _txt(c, lx + 0.18, 6.52, nome, 12, CINZA_TEXTO)
        lx += 1.7


def gerar(paginas: list[dict]) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(W, H))
    c.setTitle('Relatório de Faturamento')
    for p in paginas:
        {'capa': _capa, 'kpis': _kpis, 'tabela': _tabela, 'grafico': _grafico}[p['tipo']](c, p)
        c.showPage()
    c.save()
    return buf.getvalue()
