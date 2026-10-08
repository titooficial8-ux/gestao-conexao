import re
import unicodedata

from flask import Blueprint, render_template, abort, redirect, url_for, request, flash, jsonify, Response, session
from flask_login import login_required, current_user
from datetime import datetime, timedelta

from app import db
from app.models import (
    Category, Tab, Machine, Sequencia, PedidoRelatorio,
    ArticleClientMapping, ArticleEstruturaMapping, ArticleClienteEspecial,
    FilaPreProgramacao, HistoricoProducao, Report32Movement, ArticleDailyCycle,
    QualitySettings, SavedDrawing, SavedDrawingHistory, SavedDrawingSaveLog,
    BaseEstrutura, GramaturaMetrosRule, NonWorkingDay,
)

main_bp = Blueprint('main', __name__)

ORTOBOM_CLIENT_ALIASES = {
    'AMAZONAS', 'BAIANA', 'BELEM', 'CEARENSE', 'CENTRO OESTE', 'CONTAGEM',
    'CUIABA', 'D JUAN', 'NORTE PARANAENSE', 'OLINDA', 'QUEIMADOS',
    'RIO GRANDENSE', 'RIO SUL',
}


def _strip_accents(s):
    return ''.join(c for c in unicodedata.normalize('NFD', s) if unicodedata.category(c) != 'Mn')


def normalize_client_name(name):
    if not name:
        return ''
    upper = _strip_accents(name.upper()).strip()
    for alias in ORTOBOM_CLIENT_ALIASES:
        if alias in upper:
            return 'ORTOBOM'
    return name


def _find_pedido_por_artigo(artigo):
    """Acha um Pedido cujo cod_produto OU ds_produto comece com o codigo do
    artigo — mesmo criterio de casamento usado no PCP Hub (o campo 'article'
    da Programacao costuma ser so o codigo numerico, e o Pedido guarda a
    descricao completa comecando por esse codigo)."""
    artigo = (artigo or '').strip()
    if not artigo:
        return None
    cod = artigo.split()[0] if artigo.split() else artigo
    return PedidoRelatorio.query.filter(
        db.or_(
            PedidoRelatorio.cod_produto == cod,
            PedidoRelatorio.cod_produto == artigo,
            PedidoRelatorio.ds_produto.ilike(f'{cod}%'),
            PedidoRelatorio.ds_produto.ilike(f'{artigo}%'),
        )
    ).first()


def resolve_client_from_article(artigo):
    """Mesma prioridade do PCP Hub: mapeamento manual > Ortobom > Ecoflex >
    lookup no Relatorio Pedidos (por cod_produto ou inicio do ds_produto)."""
    artigo = (artigo or '').strip()
    if not artigo:
        return None
    cod = artigo.split()[0] if artigo.split() else ''

    for chave in (cod, artigo):
        if not chave:
            continue
        m = ArticleClientMapping.query.filter_by(cod_produto=chave).first()
        if m:
            return normalize_client_name(m.nome_cliente)

    for chave in (cod, artigo):
        if not chave:
            continue
        esp = ArticleClienteEspecial.query.filter_by(cod_produto=chave).first()
        if esp:
            return esp.cliente

    p = _find_pedido_por_artigo(artigo)
    if p and p.nome_cliente:
        return normalize_client_name(p.nome_cliente)
    return None


def resolve_estrutura_from_article(artigo):
    artigo = (artigo or '').strip()
    if not artigo:
        return None
    cod = artigo.split()[0] if artigo.split() else ''
    for chave in (cod.upper(), artigo.upper()):
        if not chave:
            continue
        m = ArticleEstruturaMapping.query.filter(db.func.upper(ArticleEstruturaMapping.article) == chave).first()
        if m:
            return m.estrutura
    p = _find_pedido_por_artigo(artigo)
    if p and p.estrutura:
        return p.estrutura
    return None


def resolve_metros_from_article(artigo):
    """DS.Metros (metragem padrao) do artigo, via Base Estrutura."""
    artigo = (artigo or '').strip()
    if not artigo:
        return None
    cod = artigo.split()[0] if artigo.split() else ''
    for chave in (cod, artigo):
        if not chave:
            continue
        m = BaseEstrutura.query.filter(BaseEstrutura.ds_produto.ilike(f'{chave}%')).first()
        if m:
            return m.ds_metros
    return None


def resolve_cod_segmento_from_article(artigo):
    p = _find_pedido_por_artigo(artigo)
    return p.cod_segmento if p and p.cod_segmento else None


def resolve_dt_cliente_from_pedidos(op, artigo):
    """Cruza OP (exato) com o artigo no Relatorio Pedidos e devolve a menor
    Prev. Faturamento encontrada — mesma logica do sync OP+artigo."""
    op = (op or '').strip()
    if not op:
        return None
    candidatos = PedidoRelatorio.query.filter_by(nr_op=op).all()
    if not candidatos:
        return None
    cod = (artigo or '').split()[0] if artigo else ''

    def bate_artigo(p):
        if not artigo:
            return True
        if p.cod_produto in (cod, artigo):
            return True
        ds = (p.ds_produto or '').upper()
        return ds.startswith(cod.upper()) or ds.startswith(artigo.upper())

    datas = [p.dt_prevfaturamento for p in candidatos if p.dt_prevfaturamento and bate_artigo(p)]
    if not datas:
        datas = [p.dt_prevfaturamento for p in candidatos if p.dt_prevfaturamento]
    return min(datas) if datas else None

MESES_PT = [
    'Janeiro', 'Fevereiro', 'Março', 'Abril', 'Maio', 'Junho',
    'Julho', 'Agosto', 'Setembro', 'Outubro', 'Novembro', 'Dezembro',
]


def mes_label(seq):
    """Rotulo de mes usado nos filtros: production_month (ORTOBOM) ou o
    mes/ano do Termino Malharia, no mesmo formato 'Mes/Ano'."""
    if seq.production_month:
        return seq.production_month.strip().title()
    if seq.termino_malharia:
        return f'{MESES_PT[seq.termino_malharia.month - 1]}/{seq.termino_malharia.year}'
    return None


def sequencia_bate_busca(seq, termo):
    termo = termo.lower()
    campos = [seq.artigo, seq.cliente, seq.op, seq.cod_segmento]
    return any(termo in (c or '').lower() for c in campos)


# ---------------------------------------------------------------------------
# MODELO / EXEMPLO — Gestão Comercial ainda nao tem fonte de dados conectada
# (nenhum ERP/planilha comercial integrado ainda). Os numeros abaixo sao
# ilustrativos, so para mostrar o layout dos indicadores; quando o Comercial
# definir de onde vem o dado real (TOTVS, planilha, etc.) essas constantes
# saem e entram queries de verdade, no mesmo padrao do resto do sistema.
# ---------------------------------------------------------------------------
def _sparkline_svg(valores, largura=112, altura=34, cor='#2f7fe0'):
    """Gera um mini-grafico de tendencia (sparkline) em SVG puro a partir de
    uma lista de numeros — sem depender de nenhuma lib de graficos."""
    minimo, maximo = min(valores), max(valores)
    amplitude = (maximo - minimo) or 1
    passo = largura / (len(valores) - 1)
    pontos = []
    for i, v in enumerate(valores):
        x = round(i * passo, 1)
        y = round(altura - ((v - minimo) / amplitude) * (altura - 6) - 3, 1)
        pontos.append(f'{x},{y}')
    linha = ' '.join(pontos)
    ultimo_x, ultimo_y = pontos[-1].split(',')
    area = f'0,{altura} {linha} {ultimo_x},{altura}'
    return (
        f'<svg viewBox="0 0 {largura} {altura}" class="comercial-spark-svg" preserveAspectRatio="none">'
        f'<polygon points="{area}" fill="{cor}" opacity="0.12"></polygon>'
        f'<polyline points="{linha}" fill="none" stroke="{cor}" stroke-width="2" '
        f'stroke-linecap="round" stroke-linejoin="round"></polyline>'
        f'<circle cx="{ultimo_x}" cy="{ultimo_y}" r="2.6" fill="{cor}"></circle>'
        f'</svg>'
    )


def _ring_svg(percentual, cor, tamanho=52, espessura=5):
    """Anel de progresso (donut) em SVG puro, estilo 'meta atingida %'."""
    raio = (tamanho - espessura) / 2
    centro = tamanho / 2
    perimetro = 2 * 3.14159265 * raio
    preenchido = perimetro * (percentual / 100)
    return (
        f'<svg viewBox="0 0 {tamanho} {tamanho}" class="comercial-ring-svg">'
        f'<circle cx="{centro}" cy="{centro}" r="{raio}" fill="none" stroke="var(--gray-200)" stroke-width="{espessura}"></circle>'
        f'<circle cx="{centro}" cy="{centro}" r="{raio}" fill="none" stroke="{cor}" stroke-width="{espessura}" '
        f'stroke-linecap="round" stroke-dasharray="{preenchido:.1f} {perimetro:.1f}" '
        f'transform="rotate(-90 {centro} {centro})"></circle>'
        f'</svg>'
    )


def _indicador_avancado(label, icone, unidade, historico, meta, meta_fmt, direcao, desc, fmt='{:.1f}'):
    """Monta um indicador avancado completo (valor atual + tendencia + status
    + progresso vs meta) a partir de um historico numerico simples."""
    atual = historico[-1]
    anterior = historico[-2]
    variacao = atual - anterior
    positivo = variacao <= 0 if direcao == 'down' else variacao >= 0
    meta_batida = atual <= meta if direcao == 'down' else atual >= meta
    if direcao == 'down':
        atingimento = 100 if atual <= meta else round(meta / atual * 100)
    else:
        atingimento = round(atual / meta * 100)
    atingimento = max(0, min(atingimento, 100))
    if meta_batida:
        status = 'good'
    elif atingimento >= 80:
        status = 'warning'
    else:
        status = 'critical'
    cor_spark = {'good': '#16a34a', 'warning': '#e0972f', 'critical': '#dc2626'}[status]
    fmt_pt = lambda n: fmt.format(n).replace('.', ',')

    if meta_batida:
        meta_insight = '✓ Meta batida'
    else:
        diff = (atual - meta) if direcao == 'down' else (meta - atual)
        meta_insight = f'{fmt_pt(abs(diff))}{unidade} {"acima" if direcao == "down" else "abaixo"} da meta'

    melhor = min(historico) if direcao == 'down' else max(historico)
    media = sum(historico) / len(historico)

    return {
        'label': label, 'icone': icone, 'unidade': unidade,
        'valor': fmt_pt(atual) + unidade,
        'variacao': ('+' if variacao > 0 else '') + fmt_pt(variacao) + unidade,
        'positivo': positivo,
        'status': status,
        'status_label': {'good': 'Saudável', 'warning': 'Atenção', 'critical': 'Crítico'}[status],
        'meta_label': meta_fmt,
        'meta_insight': meta_insight,
        'meta_batida': meta_batida,
        'atingimento': atingimento,
        'anterior_fmt': fmt_pt(anterior) + unidade,
        'media_fmt': fmt_pt(media) + unidade,
        'melhor_fmt': fmt_pt(melhor) + unidade,
        'sparkline': _sparkline_svg(historico, cor=cor_spark, altura=54),
        'ring': _ring_svg(atingimento, cor_spark),
        'desc': desc,
    }


DASHBOARD_COMERCIAL_MODELO = {
    'kpis': [
        {'label': 'Faturamento do Mês', 'valor': 'R$ 4.286.500', 'variacao': '+8,4%', 'positivo': True, 'icone': '💰'},
        {'label': 'Pedidos em Carteira', 'valor': '312', 'variacao': '+12', 'positivo': True, 'icone': '📦'},
        {'label': 'Ticket Médio', 'valor': 'R$ 13.740', 'variacao': '-2,1%', 'positivo': False, 'icone': '🎯'},
        {'label': 'Taxa de Conversão', 'valor': '38%', 'variacao': '+3 pts', 'positivo': True, 'icone': '📈'},
    ],
    'meta_mes': {'atingido': 4286500, 'meta': 5000000, 'percentual': 86},
    'split_mi_me': [
        {'label': 'MI · Mercado Interno', 'valor': 68, 'cor': '#3fa34d'},
        {'label': 'ME · Exportação', 'valor': 32, 'cor': '#e0972f'},
    ],
    'vendas_por_mes': [
        {'mes': 'Abr', 'valor': 3.1}, {'mes': 'Mai', 'valor': 3.6}, {'mes': 'Jun', 'valor': 3.4},
        {'mes': 'Jul', 'valor': 3.9}, {'mes': 'Ago', 'valor': 4.0}, {'mes': 'Set', 'valor': 4.28},
    ],
    'funil': [
        {'etapa': 'Prospecção', 'valor': 180}, {'etapa': 'Proposta Enviada', 'valor': 120},
        {'etapa': 'Negociação', 'valor': 74}, {'etapa': 'Fechado', 'valor': 41},
    ],
    'top_clientes': [
        {'cliente': 'ORTOBOM', 'valor': 812000}, {'cliente': 'GAZIN', 'valor': 456000},
        {'cliente': 'FLEX DO BRASIL', 'valor': 318000}, {'cliente': 'COLCHONES POLYMAX', 'valor': 274000},
        {'cliente': 'GRUPO LIVING', 'valor': 198000},
    ],
    'pedidos_recentes': [
        {'pedido': '10921', 'cliente': 'COLCHOES GLOBO', 'regiao': 'MI', 'valor': 'R$ 62.400', 'status': 'Aprovado'},
        {'pedido': '10940', 'cliente': 'FLEX DO BRASIL', 'regiao': 'MI', 'valor': 'R$ 38.100', 'status': 'Em análise'},
        {'pedido': '5214', 'cliente': 'ESPUMIX SPA', 'regiao': 'ME', 'valor': 'US$ 24.800', 'status': 'Aprovado'},
        {'pedido': '10757', 'cliente': 'COLCHOES GAZIN', 'regiao': 'MI', 'valor': 'R$ 91.200', 'status': 'Faturado'},
        {'pedido': '5198', 'cliente': 'COLCHONES POLYMAX', 'regiao': 'ME', 'valor': 'US$ 51.300', 'status': 'Em produção'},
    ],
    'grupos_indicadores_avancados': [
        {
            'titulo': 'Risco de Carteira',
            'indicadores': [
                _indicador_avancado(
                    'Churn Rate', '📉', '%', [3.1, 3.4, 3.8, 5.0, 4.8, 4.2], meta=4.0, meta_fmt='Meta: ≤ 4,0%',
                    direcao='down', desc='Taxa de cancelamento de pedidos por clientes no mês.',
                ),
                _indicador_avancado(
                    'Concentração de Carteira', '⚖️', '%', [52, 55, 58, 57, 59, 61], meta=55, meta_fmt='Meta: ≤ 55%',
                    direcao='down',
                    desc='% do faturamento vindo dos 3 maiores clientes (Ortobom, Gazin, Flex do Brasil). Acima de 60% o risco comercial é alto.',
                ),
            ],
        },
        {
            'titulo': 'Operação & Logística',
            'indicadores': [
                _indicador_avancado(
                    'Lead Time Comercial', '⏱️', ' dias', [11.2, 10.8, 10.1, 9.9, 10.5, 9.4], meta=9.0, meta_fmt='Meta: ≤ 9,0 dias',
                    direcao='down', desc='Tempo médio entre o fechamento do pedido e o faturamento/despacho da malha.',
                ),
                _indicador_avancado(
                    'Entrega no Prazo', '🚚', '%', [86, 87, 89, 90, 90, 92], meta=95, meta_fmt='Meta: ≥ 95%',
                    direcao='up', desc='% de pedidos despachados dentro do prazo prometido ao cliente.', fmt='{:.0f}',
                ),
                _indicador_avancado(
                    'Giro de Estoque (Expedição)', '🔄', 'x/mês', [2.6, 2.8, 3.0, 3.1, 3.2, 3.4], meta=3.5, meta_fmt='Meta: ≥ 3,5x/mês',
                    direcao='up', desc='Quantas vezes o estoque de peças prontas para expedição gira por mês.',
                ),
                _indicador_avancado(
                    'Aging de Estoque (Peças Prontas)', '📦', ' dias', [4.9, 5.4, 5.8, 6.1, 6.3, 6.8], meta=5.0, meta_fmt='Meta: ≤ 5,0 dias',
                    direcao='down', desc='Tempo médio que as peças já prontas do cliente ficam paradas no estoque até serem expedidas.',
                ),
            ],
        },
        {
            'titulo': 'Qualidade & Custo',
            'indicadores': [
                _indicador_avancado(
                    'Devolução por Defeito Técnico', '⚠️', '%', [1.2, 1.3, 1.5, 1.4, 1.5, 1.8], meta=1.5, meta_fmt='Meta: ≤ 1,5%',
                    direcao='down', desc='% de malhas devolvidas por barramento, furos ou variação de tonalidade/tingimento. Afeta comissão e retenção comercial.',
                ),
                _indicador_avancado(
                    'Custo por Unidade', '🧮', '', [8.10, 8.25, 8.40, 8.55, 8.75, 8.90], meta=8.50, meta_fmt='Meta: ≤ R$ 8,50',
                    direcao='down', desc='Custo médio comercial (frete + comissão + operação) por peça expedida.', fmt='R$ {:.2f}',
                ),
            ],
        },
    ],
}

PEDIDOS_MI_COLUNAS = ['Pedido', 'Cliente', 'UF', 'Vendedor', 'Data Pedido', 'Valor (R$)', 'Status']
PEDIDOS_MI_MODELO = [
    ['10921', 'COLCHOES GLOBO', 'SP', 'Aline Pinheiro', '12/09/2026', '62.400,00', 'Aprovado'],
    ['10940', 'FLEX DO BRASIL', 'MG', 'Rogério Passos', '13/09/2026', '38.100,00', 'Em análise'],
    ['10757', 'COLCHOES GAZIN', 'PR', 'Homero Vieira', '10/09/2026', '91.200,00', 'Faturado'],
    ['10883', 'ORTOBOM', 'SP', 'Aline Pinheiro', '14/09/2026', '214.600,00', 'Aprovado'],
    ['10802', 'DBS COMÉRCIO', 'RS', 'Julio Cesar', '09/09/2026', '27.850,00', 'Cancelado'],
]

PEDIDOS_ME_COLUNAS = ['Pedido', 'Cliente', 'País', 'Moeda', 'Valor', 'Incoterm', 'Status']
PEDIDOS_ME_MODELO = [
    ['5214', 'ESPUMIX SPA', 'Chile', 'USD', '24.800,00', 'FOB', 'Aprovado'],
    ['5198', 'COLCHONES POLYMAX', 'Argentina', 'USD', '51.300,00', 'CIF', 'Em produção'],
    ['5183', 'GRUPO SUEÑOLAR', 'Paraguai', 'USD', '18.900,00', 'FOB', 'Em análise'],
    ['5170', 'DISTRIBUIDORA CASEROS', 'Argentina', 'USD', '33.400,00', 'CIF', 'Faturado'],
    ['5155', 'FLEX CHILE', 'Chile', 'USD', '46.200,00', 'FOB', 'Aprovado'],
]

# Indicadores de clientes — mesmo modelo/exemplo, aparecem no topo das duas
# telas de Pedidos Comercial (MI e ME).
INDICADORES_CLIENTES_MI = [
    {'label': 'Maior Volume de Pedidos', 'valor': 'ORTOBOM', 'sub': '18 pedidos em carteira', 'icone': '🏆'},
    {'label': 'Produção Já Programada', 'valor': '7 de 12', 'sub': 'clientes com OP alocada em máquina', 'icone': '🛠️'},
    {'label': 'Clientes Ticket Alto', 'valor': '3 clientes', 'sub': '> R$ 100 mil no mês', 'icone': '💎'},
    {'label': 'Clientes Ticket Médio', 'valor': '6 clientes', 'sub': 'R$ 30 mil – R$ 100 mil', 'icone': '📊'},
    {'label': 'Clientes Ticket Baixo', 'valor': '3 clientes', 'sub': '< R$ 30 mil no mês', 'icone': '🪙'},
]

INDICADORES_CLIENTES_ME = [
    {'label': 'Maior Volume de Pedidos', 'valor': 'COLCHONES POLYMAX', 'sub': '6 pedidos em carteira', 'icone': '🏆'},
    {'label': 'Produção Já Programada', 'valor': '3 de 5', 'sub': 'clientes com OP alocada em máquina', 'icone': '🛠️'},
    {'label': 'Clientes Ticket Alto', 'valor': '2 clientes', 'sub': '> US$ 40 mil no mês', 'icone': '💎'},
    {'label': 'Clientes Ticket Médio', 'valor': '2 clientes', 'sub': 'US$ 15 mil – US$ 40 mil', 'icone': '📊'},
    {'label': 'Clientes Ticket Baixo', 'valor': '1 cliente', 'sub': '< US$ 15 mil no mês', 'icone': '🪙'},
]

# Modelo "Pedidos por País" — mesma logica de agrupamento por segmento ja
# usada na Visao Geral (Programacao), so que aqui olhando o Comercial ME.
PEDIDOS_POR_PAIS_ME = [
    {'pais': 'Chile', 'bandeira': '🇨🇱', 'pedidos': 2, 'valor': 71000},
    {'pais': 'Argentina', 'bandeira': '🇦🇷', 'pedidos': 2, 'valor': 84700},
    {'pais': 'Paraguai', 'bandeira': '🇵🇾', 'pedidos': 1, 'valor': 18900},
]

# ---------------------------------------------------------------------------
# MODELO / EXEMPLO — Gestão Estoque (Supply Chain) tambem ainda nao tem fonte
# de dados conectada. Mesmo padrao do Comercial: indicadores ilustrativos so
# pra deixar a tela preenchida ate a integracao de verdade.
# ---------------------------------------------------------------------------
DASHBOARD_ESTOQUE_MODELO = {
    'indicadores': [
        _indicador_avancado(
            'Ruptura de Estoque', '🚫', '%', [5.8, 5.1, 4.6, 4.9, 4.2, 3.2], meta=4.0, meta_fmt='Meta: ≤ 4,0%',
            direcao='down',
            desc='Mede a frequência em que um produto falta na prateleira ou no site no momento da compra.',
        ),
        _indicador_avancado(
            'Cobertura de Estoque', '📅', ' dias', [14, 15, 16, 17, 16, 18], meta=15, meta_fmt='Meta: ≥ 15 dias',
            direcao='up', fmt='{:.0f}',
            desc='Indica por quantos dias o estoque atual consegue atender à demanda dos clientes sem precisar de novas compras.',
        ),
        _indicador_avancado(
            'Tempo de Reposição (Lead Time)', '🚛', ' dias', [16, 15, 14, 13, 12, 12], meta=10, meta_fmt='Meta: ≤ 10 dias',
            direcao='down', fmt='{:.0f}',
            desc='O período total, em dias, entre o momento em que você faz o pedido ao fornecedor e a chegada do produto no depósito.',
        ),
        _indicador_avancado(
            'Acurácia de Inventário', '🎯', '%', [93.5, 94.8, 95.6, 96.2, 96.9, 97.4], meta=98, meta_fmt='Meta: ≥ 98%',
            direcao='up',
            desc='A porcentagem de exatidão entre a quantidade de itens registrada no sistema e a quantidade física real no depósito.',
        ),
        _indicador_avancado(
            'Nível de Estoque Mínimo', '⚠️', ' itens', [12, 9, 15, 11, 8, 6], meta=5, meta_fmt='Meta: ≤ 5 itens abaixo do mínimo',
            direcao='down', fmt='{:.0f}',
            desc='Quantidade de itens que já estão abaixo do estoque de segurança definido, com risco de ruptura se não forem repostos.',
        ),
    ],
}

# ---------------------------------------------------------------------------
# MODELO / EXEMPLO — Controle do Estoque (Almoxarifado): posicao de materia
# prima (fios) por artigo/fornecedor, so pra deixar a tela preenchida ate a
# integracao com o sistema real de almoxarifado.
# ---------------------------------------------------------------------------
MATERIA_PRIMA_MODELO = [
    {'cod_totvs': '40012', 'artigo': '150/48 Cru', 'kg': 32750, 'empenhado': 28500, 'fornecedor': 'Avanti', 'rotatividade': 'alta'},
    {'cod_totvs': '40045', 'artigo': '167/48 Areia', 'kg': 17243, 'empenhado': 15800, 'fornecedor': 'Sinterama', 'rotatividade': 'alta'},
    {'cod_totvs': '40078', 'artigo': '300/72x2 Enchimento', 'kg': 78456, 'empenhado': 42000, 'fornecedor': 'Unifi', 'rotatividade': 'alta'},
    {'cod_totvs': '41002', 'artigo': '220/84 Mescla Azul', 'kg': 523, 'empenhado': 680, 'fornecedor': 'Unifi', 'rotatividade': 'baixa'},
    {'cod_totvs': '40013', 'artigo': '150/48 Brilhante', 'kg': 124, 'empenhado': 90, 'fornecedor': 'Avanti', 'rotatividade': 'alta'},
    {'cod_totvs': '40046', 'artigo': '167/48 Garapa', 'kg': 340, 'empenhado': 210, 'fornecedor': 'Sinterama', 'rotatividade': 'media'},
    {'cod_totvs': '40047', 'artigo': '167/48 Brasil', 'kg': 124, 'empenhado': 150, 'fornecedor': 'Unifi', 'rotatividade': 'media'},
    {'cod_totvs': '40014', 'artigo': '150/48 Brilhante Preto', 'kg': 345, 'empenhado': 200, 'fornecedor': 'Avanti', 'rotatividade': 'baixa'},
    {'cod_totvs': '40015', 'artigo': '150/48 Brilhante Rosa', 'kg': 243, 'empenhado': 260, 'fornecedor': 'Avanti', 'rotatividade': 'baixa'},
]

# Modelo de "maiores saidas" (consumo do mes) — nao ha historico real ainda,
# entao os valores seguem proporcionalmente o volume de estoque de cada fio.
SAIDAS_MATERIA_PRIMA_MODELO = [
    {'artigo': '300/72x2 Enchimento', 'kg': 18200},
    {'artigo': '150/48 Cru', 'kg': 9450},
    {'artigo': '167/48 Areia', 'kg': 6120},
    {'artigo': '220/84 Mescla Azul', 'kg': 1240},
    {'artigo': '167/48 Garapa', 'kg': 480},
]


def _montar_controle_estoque():
    itens = sorted(MATERIA_PRIMA_MODELO, key=lambda x: x['kg'], reverse=True)
    total_kg = sum(i['kg'] for i in itens)

    por_rotatividade = {'alta': [], 'media': [], 'baixa': []}
    for i in itens:
        por_rotatividade[i['rotatividade']].append(i)

    por_fornecedor = {}
    for i in itens:
        info = por_fornecedor.setdefault(i['fornecedor'], {'fornecedor': i['fornecedor'], 'kg': 0, 'itens': 0})
        info['kg'] += i['kg']
        info['itens'] += 1
    ranking_fornecedores = sorted(por_fornecedor.values(), key=lambda x: x['kg'], reverse=True)

    maior_fio = itens[0]
    maior_fornecedor = ranking_fornecedores[0]

    # Disponibilidade vs Empenho: quanto ja foi programado/empenhado em OP
    # (consumo previsto) contra o que realmente tem em estoque de cada
    # materia prima — mostra se vai faltar fio pra atender o que ja foi
    # programado.
    disponibilidade = []
    for i in itens:
        saldo = i['kg'] - i['empenhado']
        if saldo < 0:
            situacao = 'insuficiente'
        elif i['kg'] and (saldo / i['kg']) < 0.15:
            situacao = 'atencao'
        else:
            situacao = 'suficiente'
        disponibilidade.append({
            'cod_totvs': i['cod_totvs'], 'artigo': i['artigo'], 'estoque': i['kg'],
            'empenhado': i['empenhado'], 'saldo': saldo, 'situacao': situacao,
        })
    disponibilidade.sort(key=lambda x: x['saldo'])
    total_empenhado = sum(i['empenhado'] for i in itens)
    materiais_em_deficit = sum(1 for d in disponibilidade if d['situacao'] == 'insuficiente')

    kpis = [
        {'label': 'Total de MP em Estoque', 'valor': f'{total_kg:,}'.replace(',', '.') + ' kg', 'icone': '📦'},
        {'label': 'Fios Cadastrados', 'valor': f'{len(itens)} itens', 'icone': '🧵'},
        {'label': 'Alta Rotatividade', 'valor': f"{len(por_rotatividade['alta'])} fios", 'icone': '🟢'},
        {'label': 'Baixa Rotatividade', 'valor': f"{len(por_rotatividade['baixa'])} fios", 'icone': '🔴'},
        {'label': 'Fio de Maior Volume', 'valor': maior_fio['artigo'], 'icone': '🏆'},
        {'label': 'Fornecedor com Maior Volume', 'valor': maior_fornecedor['fornecedor'], 'icone': '🚚'},
        {'label': 'Total Empenhado em OP', 'valor': f'{total_empenhado:,}'.replace(',', '.') + ' kg', 'icone': '📋'},
        {'label': 'Materiais em Déficit', 'valor': f'{materiais_em_deficit} itens', 'icone': '🚨'},
    ]

    return {
        'itens': itens,
        'total_kg': total_kg,
        'por_rotatividade': por_rotatividade,
        'ranking_fornecedores': ranking_fornecedores,
        'saidas': sorted(SAIDAS_MATERIA_PRIMA_MODELO, key=lambda x: x['kg'], reverse=True),
        'disponibilidade': disponibilidade,
        'kpis': kpis,
    }


DRAWING_SAVE_EMAILS = {
    'gustavo.bonfim@conexaomalhas.com.br', 'gustavo.bonfim@conexaoplanning.com.br',
    'gustavo.bonfim@conexao.com.br',
}
DRAWING_DELETE_EMAILS = {'mauricio@conexaomalhas.com.br', 'christian.yarzon@conexaomalhas.com.br'}


def _can_mark_drawing_saved(user):
    return bool(user.is_admin) or (user.email or '').strip().lower() in DRAWING_SAVE_EMAILS


def _can_delete_drawing(user):
    return bool(user.is_admin) or (user.email or '').strip().lower() in DRAWING_DELETE_EMAILS


def _drawing_hist_key(machine, article):
    artigo_base = re.split(r'[\s-]', (article or '').strip().upper())[0]
    return f"{(machine or '').strip()}|{artigo_base}"


def _sync_saved_drawings():
    """Cruza a Sequencia atual (EM_SEQUENCIA/PRODUZINDO) com a tabela
    saved_drawings: cria automaticamente uma linha pendente pra toda OP nova
    nessas situacoes, e remove as que ja sairam da sequencia (OP mudou de
    maquina, finalizou, foi cancelada etc) — mesma logica do PCP Hub."""
    seq_rows = (
        Sequencia.query.join(Machine)
        .filter(Sequencia.status.in_(['EM_SEQUENCIA', 'PRODUZINDO']))
        .order_by(Sequencia.sequence_order.asc())
        .all()
    )

    def seq_key(machine, op, article):
        return f"{(machine or '').strip()}|{(op or '').strip()}|{(article or '').strip().upper()}"

    seq_set = {}
    by_machine = {}
    for r in seq_rows:
        machine = str(r.machine.number)
        key = seq_key(machine, r.op, r.artigo)
        seq_set[key] = r.status
        by_machine.setdefault(machine, []).append(r)

    existing = SavedDrawing.query.all()
    existing_keys = {seq_key(r.machine, r.op, r.article) for r in existing}

    novos = 0
    for r in seq_rows:
        machine = str(r.machine.number)
        key = seq_key(machine, r.op, r.artigo)
        if key not in existing_keys:
            db.session.add(SavedDrawing(
                machine=machine, op=r.op, article=r.artigo, client=r.cliente, is_saved=False,
            ))
            existing_keys.add(key)
            novos += 1

    stale = [r for r in existing if seq_key(r.machine, r.op, r.article) not in seq_set]
    for r in stale:
        db.session.delete(r)

    if novos or stale:
        db.session.commit()

    status_map = seq_set
    seq_info_map = {}
    for machine, items in by_machine.items():
        items.sort(key=lambda r: r.sequence_order or 0)
        for i, r in enumerate(items):
            prev = items[i - 1] if i > 0 else None
            seq_info_map[seq_key(machine, r.op, r.artigo)] = {
                'position': i + 1,
                'entry': prev.termino_malharia.isoformat() if (prev and prev.termino_malharia) else None,
            }

    rows = SavedDrawing.query.order_by(SavedDrawing.created_at.desc()).all()
    return rows, status_map, seq_info_map, seq_key


@main_bp.route('/aba/saved-drawing/<int:drawing_id>/salvar', methods=['POST'])
@login_required
def saved_drawing_marcar(drawing_id):
    if not _can_mark_drawing_saved(current_user):
        abort(403)
    item = SavedDrawing.query.get_or_404(drawing_id)

    hora_brasilia = (datetime.utcnow() - timedelta(hours=3)).hour
    email = (current_user.email or '').strip().lower()
    if hora_brasilia >= 14 and email not in DRAWING_SAVE_EMAILS:
        flash('horario_limite_desenho_excedido', 'error')
        return redirect(url_for('main.view_tab', slug='saved-drawing'))

    agora = datetime.utcnow()
    item.is_saved = True
    item.saved_by_name = current_user.name
    item.saved_by_email = current_user.email
    item.saved_at = agora
    db.session.add(SavedDrawingSaveLog(
        machine=item.machine, op=item.op, article=item.article, client=item.client,
        observation=item.observation, saved_by_name=current_user.name, saved_by_email=current_user.email,
        saved_at=agora, action='saved',
    ))
    if not SavedDrawingHistory.query.filter_by(machine=item.machine, article=item.article).first():
        db.session.add(SavedDrawingHistory(machine=item.machine, article=item.article, source='app'))
    db.session.commit()
    flash('desenho_marcado_salvo_sucesso', 'success')
    return redirect(url_for('main.view_tab', slug='saved-drawing'))


@main_bp.route('/aba/saved-drawing/<int:drawing_id>/desmarcar', methods=['POST'])
@login_required
def saved_drawing_desmarcar(drawing_id):
    if not _can_mark_drawing_saved(current_user):
        abort(403)
    item = SavedDrawing.query.get_or_404(drawing_id)
    item.is_saved = False
    item.saved_by_name = None
    item.saved_by_email = None
    item.saved_at = None
    db.session.add(SavedDrawingSaveLog(
        machine=item.machine, op=item.op, article=item.article, client=item.client,
        saved_by_name=current_user.name, saved_by_email=current_user.email,
        saved_at=datetime.utcnow(), action='unmarked',
    ))
    db.session.commit()
    flash('desenho_desmarcado_sucesso', 'success')
    return redirect(url_for('main.view_tab', slug='saved-drawing'))


@main_bp.route('/aba/saved-drawing/<int:drawing_id>/observacao', methods=['POST'])
@login_required
def saved_drawing_observacao(drawing_id):
    if not _can_mark_drawing_saved(current_user):
        abort(403)
    item = SavedDrawing.query.get_or_404(drawing_id)
    item.observation = (request.form.get('observation') or '').strip() or None
    db.session.commit()
    flash('observacao_atualizada_sucesso', 'success')
    return redirect(url_for('main.view_tab', slug='saved-drawing'))


@main_bp.route('/aba/saved-drawing/<int:drawing_id>/excluir', methods=['POST'])
@login_required
def saved_drawing_excluir(drawing_id):
    if not _can_delete_drawing(current_user):
        abort(403)
    item = SavedDrawing.query.get_or_404(drawing_id)
    db.session.delete(item)
    db.session.commit()
    flash('desenho_excluido_sucesso', 'success')
    return redirect(url_for('main.view_tab', slug='saved-drawing'))


def _require_base_dados_admin():
    if not current_user.is_admin:
        abort(403)


@main_bp.route('/aba/base-de-dados/ortobom/adicionar', methods=['POST'])
@login_required
def base_dados_ortobom_adicionar():
    _require_base_dados_admin()
    cod = (request.form.get('cod_produto') or '').strip()
    cliente = (request.form.get('cliente') or 'ORTOBOM').strip().upper()
    if cod:
        db.session.add(ArticleClienteEspecial(cod_produto=cod, cliente=cliente))
        db.session.commit()
        flash('registro_adicionado_sucesso', 'success')
    return redirect(url_for('main.view_tab', slug='base-de-dados'))


@main_bp.route('/aba/base-de-dados/ortobom/<int:item_id>/excluir', methods=['POST'])
@login_required
def base_dados_ortobom_excluir(item_id):
    _require_base_dados_admin()
    item = ArticleClienteEspecial.query.get_or_404(item_id)
    db.session.delete(item)
    db.session.commit()
    flash('registro_excluido_sucesso', 'success')
    return redirect(url_for('main.view_tab', slug='base-de-dados'))


@main_bp.route('/aba/base-de-dados/estrutura/adicionar', methods=['POST'])
@login_required
def base_dados_estrutura_adicionar():
    _require_base_dados_admin()
    produto = (request.form.get('ds_produto') or '').strip()
    metros = (request.form.get('ds_metros') or '').strip()
    if produto and metros:
        db.session.add(BaseEstrutura(ds_produto=produto, ds_metros=metros))
        db.session.commit()
        flash('registro_adicionado_sucesso', 'success')
    return redirect(url_for('main.view_tab', slug='base-de-dados'))


@main_bp.route('/aba/base-de-dados/estrutura/<int:item_id>/excluir', methods=['POST'])
@login_required
def base_dados_estrutura_excluir(item_id):
    _require_base_dados_admin()
    item = BaseEstrutura.query.get_or_404(item_id)
    db.session.delete(item)
    db.session.commit()
    flash('registro_excluido_sucesso', 'success')
    return redirect(url_for('main.view_tab', slug='base-de-dados'))


@main_bp.route('/aba/base-de-dados/gramatura/adicionar', methods=['POST'])
@login_required
def base_dados_gramatura_adicionar():
    _require_base_dados_admin()
    try:
        gmin = float(request.form.get('gramatura_min'))
        gmax = float(request.form.get('gramatura_max'))
        metros = float(request.form.get('metros'))
    except (TypeError, ValueError):
        flash('valores_invalidos', 'error')
        return redirect(url_for('main.view_tab', slug='base-de-dados'))
    db.session.add(GramaturaMetrosRule(gramatura_min=gmin, gramatura_max=gmax, metros=metros))
    db.session.commit()
    flash('registro_adicionado_sucesso', 'success')
    return redirect(url_for('main.view_tab', slug='base-de-dados'))


@main_bp.route('/aba/base-de-dados/gramatura/<int:item_id>/excluir', methods=['POST'])
@login_required
def base_dados_gramatura_excluir(item_id):
    _require_base_dados_admin()
    item = GramaturaMetrosRule.query.get_or_404(item_id)
    db.session.delete(item)
    db.session.commit()
    flash('registro_excluido_sucesso', 'success')
    return redirect(url_for('main.view_tab', slug='base-de-dados'))


@main_bp.route('/aba/base-de-dados/historico-desenho/adicionar', methods=['POST'])
@login_required
def base_dados_historico_desenho_adicionar():
    _require_base_dados_admin()
    machine = (request.form.get('machine') or '').strip()
    article = (request.form.get('article') or '').strip()
    if machine and article:
        db.session.add(SavedDrawingHistory(machine=machine, article=article, source='manual'))
        db.session.commit()
        flash('registro_adicionado_sucesso', 'success')
    return redirect(url_for('main.view_tab', slug='base-de-dados'))


@main_bp.route('/aba/base-de-dados/historico-desenho/<int:item_id>/excluir', methods=['POST'])
@login_required
def base_dados_historico_desenho_excluir(item_id):
    _require_base_dados_admin()
    item = SavedDrawingHistory.query.get_or_404(item_id)
    db.session.delete(item)
    db.session.commit()
    flash('registro_excluido_sucesso', 'success')
    return redirect(url_for('main.view_tab', slug='base-de-dados'))


@main_bp.route('/painel')
@login_required
def dashboard():
    categories = Category.query.order_by(Category.order).all()
    for cat in categories:
        cat.visible_tabs = [tab for tab in cat.tabs if tab.visible_to(current_user)]
    return render_template('main/dashboard.html', categories=categories)


@main_bp.route('/aba/<slug>')
@login_required
def view_tab(slug):
    tab = Tab.query.filter_by(slug=slug).first_or_404()
    if not tab.visible_to(current_user):
        abort(403)

    if slug == 'programacao':
        machines = Machine.query.order_by(Machine.order.is_(None), Machine.order, Machine.number).all()
        total_maquinas_cadastradas = len(machines)
        maquinas_ativas = sum(1 for m in machines if m.esta_ativa)
        total_sequencias = Sequencia.query.count()

        busca = request.args.get('q', '').strip()
        filtro_cliente = request.args.get('cliente', '').strip()
        filtro_mes = request.args.get('mes', '').strip()

        todas_sequencias = Sequencia.query.all()
        clientes_disponiveis = sorted({s.cliente for s in todas_sequencias if s.cliente})
        meses_disponiveis = sorted({mes_label(s) for s in todas_sequencias if mes_label(s)})

        # busca por Pedido: o codigo de pedido so existe no Relatorio Pedidos,
        # entao cruza pelo OP pra tambem valer na busca das Sequencias
        ops_via_pedido = set()
        if busca:
            like = f'%{busca}%'
            ops_via_pedido = {
                p.nr_op for p in PedidoRelatorio.query.filter(
                    db.or_(PedidoRelatorio.cod_pedido.ilike(like), PedidoRelatorio.nr_op.ilike(like))
                ).all() if p.nr_op
            }

        if busca or filtro_cliente or filtro_mes:
            numeros_com_match = set()
            for m in machines:
                for s in m.sequencias:
                    if filtro_cliente and s.cliente != filtro_cliente:
                        continue
                    if filtro_mes and mes_label(s) != filtro_mes:
                        continue
                    if busca and not (
                        busca in f'{m.number:02d}'
                        or sequencia_bate_busca(s, busca)
                        or (s.op and s.op in ops_via_pedido)
                    ):
                        continue
                    numeros_com_match.add(m.number)
                    break
                else:
                    # maquina sem sequencias: so entra se a busca bater no numero dela
                    if busca and not filtro_cliente and not filtro_mes and busca in f'{m.number:02d}':
                        numeros_com_match.add(m.number)
            machines = [m for m in machines if m.number in numeros_com_match]

        if request.args.get('partial') == '1':
            return render_template(
                'main/_programacao_maquinas.html',
                machines=machines, busca=busca, filtro_cliente=filtro_cliente, filtro_mes=filtro_mes,
            )

        return render_template(
            'main/programacao.html',
            tab=tab,
            machines=machines,
            total_maquinas_cadastradas=total_maquinas_cadastradas,
            maquinas_ativas=maquinas_ativas,
            total_sequencias=total_sequencias,
            busca=busca,
            filtro_cliente=filtro_cliente,
            filtro_mes=filtro_mes,
            clientes_disponiveis=clientes_disponiveis,
            meses_disponiveis=meses_disponiveis,
            todas_maquinas=Machine.query.order_by(Machine.number).all(),
        )

    if slug == 'relatorio-pedidos':
        PAGE_SIZE = 50
        busca = request.args.get('q', '').strip()
        pagina = request.args.get('pagina', 1, type=int)

        query = PedidoRelatorio.query
        if busca:
            like = f'%{busca}%'
            query = query.filter(
                db.or_(
                    PedidoRelatorio.cod_pedido.ilike(like),
                    PedidoRelatorio.nome_cliente.ilike(like),
                    PedidoRelatorio.ds_produto.ilike(like),
                    PedidoRelatorio.nr_op.ilike(like),
                )
            )

        total = query.count()
        total_paginas = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        pagina = max(1, min(pagina, total_paginas))
        pedidos = (
            query.order_by(PedidoRelatorio.cod_pedido)
            .offset((pagina - 1) * PAGE_SIZE)
            .limit(PAGE_SIZE)
            .all()
        )
        return render_template(
            'main/relatorio_pedidos.html',
            tab=tab,
            pedidos=pedidos,
            busca=busca,
            pagina=pagina,
            total_paginas=total_paginas,
            total=total,
            total_geral=PedidoRelatorio.query.count(),
        )

    if slug == 'visao-geral':
        machines = Machine.query.order_by(Machine.number).all()
        total_maquinas = len(machines)
        maquinas_ativas = sum(1 for m in machines if m.esta_ativa)
        maquinas_manutencao = sum(1 for m in machines if m.status == Machine.STATUS_MANUTENCAO)
        maquinas_sem_programacao = sum(1 for m in machines if m.status == Machine.STATUS_SEM_PROGRAMACAO)
        maquinas_inoperante = sum(1 for m in machines if m.status == Machine.STATUS_INOPERANTE)

        total_sequencias = Sequencia.query.count()
        seq_produzindo = Sequencia.query.filter_by(status='PRODUZINDO').count()
        seq_em_sequencia = Sequencia.query.filter_by(status='EM_SEQUENCIA').count()
        seq_pcp = Sequencia.query.filter_by(status='PCP').count()

        fila_aguardando = FilaPreProgramacao.query.filter_by(status='WAITING').count()
        fila_movida = FilaPreProgramacao.query.filter_by(status='MOVED').count()

        total_pedidos = PedidoRelatorio.query.count()
        pedidos_pendentes = PedidoRelatorio.query.filter(PedidoRelatorio.qt_saldo > 0).count()

        ultimas_mudancas = (
            HistoricoProducao.query.order_by(HistoricoProducao.changed_at.desc()).limit(8).all()
        )

        todas_sequencias = Sequencia.query.all()
        seqs_produzindo = [s for s in todas_sequencias if s.status == 'PRODUZINDO']
        total_pcs_produzindo = sum(s.total_pcs or 0 for s in seqs_produzindo)
        # Metragem Total (KPI): soma de Qt.Solicitada (metros) das OPs em PRODUZINDO + PCP,
        # igual a mTotalReal do PCP Hub (PCP + Produzindo).
        metragem_total_kpi = sum(s.qt_solicitada or 0 for s in todas_sequencias if s.status in ('PRODUZINDO', 'PCP'))

        # Segmentacao real do PCP Hub: ORTOBOM/GAZIN pelo nome do cliente,
        # NACIONAL/EXPORTACAO pelo Cod.Segmento (COLCHAO MI / COLCHAO ME).
        def is_orto(s):
            return 'ORTOBOM' in (s.cliente or '').upper()

        def is_gazin(s):
            return 'GAZIN' in (s.cliente or '').upper()

        def is_me(s):
            return (s.cod_segmento or '').strip().upper() == 'COLCHAO ME'

        def is_mi(s):
            return (s.cod_segmento or '').strip().upper() == 'COLCHAO MI'

        def is_nacional(s):
            return is_mi(s) and not is_orto(s) and not is_gazin(s)

        SEGMENTOS = [
            ('ORTOBOM', is_orto),
            ('GAZIN', is_gazin),
            ('NACIONAL', is_nacional),
            ('EXPORTAÇÃO', is_me),
        ]

        ranking_clientes = []
        for nome, filtro in SEGMENTOS:
            todas_do_segmento = [s for s in todas_sequencias if filtro(s)]
            produzindo_do_segmento = [s for s in todas_do_segmento if s.status == 'PRODUZINDO']

            por_maquina = {}
            for s in produzindo_do_segmento:
                info = por_maquina.setdefault(s.machine.number, {
                    'maquina': s.machine.number, 'ops': [], 'pecas_produzidas': 0, 'total_pcs': 0,
                    'metragem_programada': 0,
                })
                info['ops'].append({
                    'op': s.op, 'artigo': s.artigo, 'cliente': s.cliente,
                    'metragem_programada': s.qt_solicitada or 0,
                })
                info['pecas_produzidas'] += s.pecas_produzidas or 0
                info['total_pcs'] += s.total_pcs or 0
                info['metragem_programada'] += s.qt_solicitada or 0
            for info in por_maquina.values():
                # Metros produzidos estimados: proporcao de pecas ja produzidas sobre o
                # total de pecas da OP, aplicada sobre a metragem programada (nao temos
                # a pesagem real do LOCAL 23 localmente, so a contagem de pecas).
                if info['total_pcs']:
                    info['metragem_produzida'] = round(
                        info['metragem_programada'] * info['pecas_produzidas'] / info['total_pcs']
                    )
                else:
                    info['metragem_produzida'] = 0
            itens_por_maquina = sorted(por_maquina.values(), key=lambda x: x['maquina'])

            ranking_clientes.append({
                'cliente': nome,
                'maquinas': len({s.machine_id for s in produzindo_do_segmento}),
                'metragem_produzindo': sum(s.qt_solicitada or 0 for s in produzindo_do_segmento),
                'metragem_programada': sum(s.qt_solicitada or 0 for s in todas_do_segmento),
                'itens': itens_por_maquina,
            })
        ranking_clientes.sort(key=lambda x: x['metragem_programada'], reverse=True)
        maior_metragem_cliente = max((c['metragem_programada'] for c in ranking_clientes), default=0) or 1

        por_estrutura = {}
        for s in todas_sequencias:
            if s.status == 'PRODUZINDO' and s.estrutura:
                por_estrutura.setdefault(s.estrutura, []).append(s)
        ranking_estruturas = []
        for estrutura, seqs in por_estrutura.items():
            por_maquina_e = {}
            for s in seqs:
                info = por_maquina_e.setdefault(s.machine.number, {
                    'maquina': s.machine.number, 'ops': [], 'pecas_produzidas': 0, 'total_pcs': 0,
                    'metragem_programada': 0,
                })
                info['ops'].append({'op': s.op, 'artigo': s.artigo, 'cliente': s.cliente})
                info['pecas_produzidas'] += s.pecas_produzidas or 0
                info['total_pcs'] += s.total_pcs or 0
                info['metragem_programada'] += s.qt_solicitada or 0
            for info in por_maquina_e.values():
                info['metragem_produzida'] = (
                    round(info['metragem_programada'] * info['pecas_produzidas'] / info['total_pcs'])
                    if info['total_pcs'] else 0
                )
            ranking_estruturas.append({
                'estrutura': estrutura,
                'maquinas': len(por_maquina_e),
                'itens': sorted(por_maquina_e.values(), key=lambda x: x['maquina']),
            })
        ranking_estruturas.sort(key=lambda x: x['maquinas'], reverse=True)
        ranking_estruturas = ranking_estruturas[:6]
        maior_maquinas_estrutura = max((e['maquinas'] for e in ranking_estruturas), default=0) or 1

        cliente_destaque = ranking_clientes[0] if ranking_clientes and ranking_clientes[0]['maquinas'] else None
        cliente_destaque_index = 0
        maquinas_cliente_destaque = [item['maquina'] for item in cliente_destaque['itens']] if cliente_destaque else []

        hist_finalizados = HistoricoProducao.query.filter_by(new_status='FINALIZADO').count()
        hist_excluidos = HistoricoProducao.query.filter_by(new_status='EXCLUIDO').count()
        hist_interrompidos = HistoricoProducao.query.filter_by(new_status='INTERROMPIDO').count()

        # Maquinas paradas (detalhe do card "Máquinas Paradas")
        paradas_detalhe = [
            {'maquina': m.number, 'status': m.status}
            for m in machines if not m.esta_ativa
        ]

        # Maquinas em gargalo: a malha (Termino Malharia) fica pronta DEPOIS da
        # data prometida ao cliente (Dt.Cliente) -- ou seja, o pano vai atrasar
        # em relacao ao prazo do pedido. So entram OPs que tem os dois campos
        # preenchidos (OPs Ortobom com Mes de Producao em vez de Dt.Cliente nao
        # entram aqui, pois nao ha uma data do cliente pra comparar).
        gargalo_detalhe = []
        for s in todas_sequencias:
            if s.status not in ('PRODUZINDO', 'EM_SEQUENCIA', 'PCP'):
                continue
            if not s.termino_malharia or not s.dt_cliente:
                continue
            dias_atraso = (s.termino_malharia - s.dt_cliente).days
            if dias_atraso <= 0:
                continue
            gargalo_detalhe.append({
                'maquina': s.machine.number, 'op': s.op, 'artigo': s.artigo, 'cliente': s.cliente,
                'dt_cliente': s.dt_cliente, 'termino': s.termino_malharia, 'dias_atraso': dias_atraso,
            })
        gargalo_detalhe.sort(key=lambda x: x['dias_atraso'], reverse=True)

        return render_template(
            'main/visao_geral.html',
            tab=tab,
            total_maquinas=total_maquinas,
            maquinas_ativas=maquinas_ativas,
            maquinas_manutencao=maquinas_manutencao,
            maquinas_sem_programacao=maquinas_sem_programacao,
            maquinas_inoperante=maquinas_inoperante,
            total_sequencias=total_sequencias,
            seq_produzindo=seq_produzindo,
            seq_em_sequencia=seq_em_sequencia,
            seq_pcp=seq_pcp,
            fila_aguardando=fila_aguardando,
            fila_movida=fila_movida,
            total_pedidos=total_pedidos,
            pedidos_pendentes=pedidos_pendentes,
            ultimas_mudancas=ultimas_mudancas,
            total_pcs_produzindo=total_pcs_produzindo,
            metragem_total_kpi=metragem_total_kpi,
            ranking_clientes=ranking_clientes,
            maior_metragem_cliente=maior_metragem_cliente,
            ranking_estruturas=ranking_estruturas,
            maior_maquinas_estrutura=maior_maquinas_estrutura,
            cliente_destaque=cliente_destaque,
            cliente_destaque_index=cliente_destaque_index,
            maquinas_cliente_destaque=maquinas_cliente_destaque,
            hist_finalizados=hist_finalizados,
            hist_excluidos=hist_excluidos,
            hist_interrompidos=hist_interrompidos,
            paradas_detalhe=paradas_detalhe,
            gargalo_detalhe=gargalo_detalhe,
        )

    if slug == 'pre-programacao':
        aba = request.args.get('aba', 'pedidos')

        machines = Machine.query.order_by(Machine.number).all()
        ATIVAS_STATUS = ('PRODUZINDO', 'EM_SEQUENCIA', 'PCP')
        estrutura_por_maquina = []
        maquinas_por_estrutura = {}  # estrutura -> [{'maquina': n, 'ops': count}, ...]
        for m in machines:
            seq_ativa = None
            for s in m.sequencias:
                if s.status == 'PRODUZINDO' and s.estrutura:
                    seq_ativa = s
                    break
            if not seq_ativa:
                for s in m.sequencias:
                    if s.status in ('EM_SEQUENCIA', 'PCP') and s.estrutura:
                        seq_ativa = s
                        break
            estrutura_atual = seq_ativa.estrutura if seq_ativa else None
            estrutura_por_maquina.append({'maquina': m.number, 'estrutura': estrutura_atual})

            if estrutura_atual and m.esta_ativa:
                ops_na_estrutura = sum(
                    1 for s in m.sequencias if s.status in ATIVAS_STATUS and s.estrutura == estrutura_atual
                )
                maquinas_por_estrutura.setdefault(estrutura_atual, []).append(
                    {'maquina': m.number, 'ops': ops_na_estrutura}
                )
        for lista in maquinas_por_estrutura.values():
            lista.sort(key=lambda x: x['ops'], reverse=True)

        analisar_itens = [
            {
                'item': item,
                'estrutura': (estrutura := resolve_estrutura_from_article(item.artigo)),
                'dt_prevfaturamento': resolve_dt_cliente_from_pedidos(item.op, item.artigo),
                'sugeridas': maquinas_por_estrutura.get(estrutura, [])[:6],
            }
            for item in FilaPreProgramacao.query.filter_by(status='WAITING')
            .order_by(FilaPreProgramacao.created_at.desc()).all()
        ]

        pedidos_op_na_fila = {i.op for i in FilaPreProgramacao.query.filter_by(status='WAITING').all() if i.op}
        pedidos_relatorio_itens = [
            p for p in PedidoRelatorio.query.filter(PedidoRelatorio.qt_saldo > 0).order_by(
                PedidoRelatorio.dt_prevfaturamento.is_(None), PedidoRelatorio.dt_prevfaturamento
            ).all()
            if normalize_client_name(p.nome_cliente) != 'ORTOBOM'
            and 'ECOFLEX' not in (p.nome_cliente or '').upper()
            and p.nr_op not in pedidos_op_na_fila
        ]

        return render_template(
            'main/pre_programacao.html',
            tab=tab,
            aba=aba,
            estrutura_por_maquina=estrutura_por_maquina,
            analisar_itens=analisar_itens,
            total_analisar=len(analisar_itens),
            pedidos_relatorio_itens=pedidos_relatorio_itens,
        )


    if slug == 'historico':
        return render_template('main/historico_artigo.html', tab=tab)

    if slug == 'controle-acessos':
        if not current_user.is_admin:
            abort(403)
        return redirect(url_for('admin.users'))

    if slug == 'dashboard-comercial':
        return render_template('main/dashboard_comercial.html', tab=tab, **DASHBOARD_COMERCIAL_MODELO)

    if slug == 'dashboard-estoque':
        return render_template('main/dashboard_estoque.html', tab=tab, **DASHBOARD_ESTOQUE_MODELO)

    if slug == 'controle-estoque':
        return render_template('main/controle_estoque.html', tab=tab, **_montar_controle_estoque())

    if slug == 'dashboard-tempo-real':
        return render_template('main/dashboard_tempo_real.html', tab=tab)

    if slug == 'gestao-malharia':
        return render_template('main/gestao_malharia.html', tab=tab)

    if slug == 'controle-producao':
        return render_template('main/controle_producao.html', tab=tab)

    if slug == 'saved-drawing':
        rows, status_map, seq_info_map, seq_key = _sync_saved_drawings()

        hist_keys = {
            _drawing_hist_key(h.machine, h.article)
            for h in SavedDrawingHistory.query.with_entities(SavedDrawingHistory.machine, SavedDrawingHistory.article).all()
        }

        itens = []
        for r in rows:
            key = seq_key(r.machine, r.op, r.article)
            status = status_map.get(key, '')
            in_history = _drawing_hist_key(r.machine, r.article) in hist_keys
            pendente = (not r.is_saved) and status in ('EM_SEQUENCIA', 'PRODUZINDO') and not in_history
            itens.append({
                'obj': r,
                'status': status,
                'in_history': in_history,
                'pendente': pendente,
                'seq_info': seq_info_map.get(key),
            })

        machine_options = sorted({i['obj'].machine for i in itens if i['obj'].machine}, key=lambda m: (len(m), m))
        article_options = sorted({i['obj'].article for i in itens if i['obj'].article})

        counts = {
            'pendentes': sum(1 for i in itens if i['pendente']),
            'salvos': sum(1 for i in itens if i['obj'].is_saved),
            'todos': len(itens),
        }

        historico = SavedDrawingSaveLog.query.order_by(SavedDrawingSaveLog.saved_at.desc()).all()

        return render_template(
            'main/saved_drawing.html', tab=tab, itens=itens, counts=counts,
            machine_options=machine_options, article_options=article_options,
            historico=historico, can_mark=_can_mark_drawing_saved(current_user),
            can_delete=_can_delete_drawing(current_user),
        )

    if slug == 'base-de-dados':
        PAGE_SIZE = 40
        estrutura_q = request.args.get('estrutura_q', '').strip()
        estrutura_pagina = request.args.get('estrutura_pagina', 1, type=int)
        estrutura_query = BaseEstrutura.query
        if estrutura_q:
            estrutura_query = estrutura_query.filter(BaseEstrutura.ds_produto.ilike(f'%{estrutura_q}%'))
        estrutura_total = estrutura_query.count()
        estrutura_paginas = max(1, (estrutura_total + PAGE_SIZE - 1) // PAGE_SIZE)
        estrutura_pagina = max(1, min(estrutura_pagina, estrutura_paginas))
        estrutura_itens = (
            estrutura_query.order_by(BaseEstrutura.ds_produto.asc())
            .offset((estrutura_pagina - 1) * PAGE_SIZE).limit(PAGE_SIZE).all()
        )

        historico_q = request.args.get('historico_q', '').strip()
        historico_pagina = request.args.get('historico_pagina', 1, type=int)
        historico_query = SavedDrawingHistory.query
        if historico_q:
            historico_query = historico_query.filter(SavedDrawingHistory.article.ilike(f'%{historico_q}%'))
        historico_total = historico_query.count()
        historico_paginas = max(1, (historico_total + PAGE_SIZE - 1) // PAGE_SIZE)
        historico_pagina = max(1, min(historico_pagina, historico_paginas))
        historico_itens = (
            historico_query.order_by(SavedDrawingHistory.created_at.desc())
            .offset((historico_pagina - 1) * PAGE_SIZE).limit(PAGE_SIZE).all()
        )

        return render_template(
            'main/base_de_dados.html', tab=tab,
            ortobom_itens=ArticleClienteEspecial.query.order_by(ArticleClienteEspecial.cliente, ArticleClienteEspecial.cod_produto).all(),
            estrutura_itens=estrutura_itens, estrutura_total=estrutura_total, estrutura_q=estrutura_q,
            estrutura_pagina=estrutura_pagina, estrutura_paginas=estrutura_paginas,
            gramatura_itens=GramaturaMetrosRule.query.order_by(GramaturaMetrosRule.gramatura_min.asc()).all(),
            historico_itens=historico_itens, historico_total=historico_total, historico_q=historico_q,
            historico_pagina=historico_pagina, historico_paginas=historico_paginas,
            is_admin=current_user.is_admin,
        )

    if slug == 'pedidos-comercial-mi':
        return render_template(
            'main/pedidos_comercial.html', tab=tab, regiao='MI',
            colunas=PEDIDOS_MI_COLUNAS, pedidos=PEDIDOS_MI_MODELO,
            indicadores_clientes=INDICADORES_CLIENTES_MI, pedidos_por_pais=None,
        )

    if slug == 'pedidos-comercial-me':
        from app.comercial_me import paineis
        from app.comercial_me.dados import catalogo
        from app.comercial_me.routes import ambiente_atual, idioma_atual
        regiao = ambiente_atual()
        return render_template(
            'main/comercial_me.html', tab=tab, regiao=regiao,
            catalogo=catalogo(), paineis=paineis.disponiveis(idioma_atual()),
        )

    return render_template('main/tab.html', tab=tab)


def _historico_finalizadas_query(busca='', mes=''):
    query = HistoricoProducao.query.filter(HistoricoProducao.new_status == 'FINALIZADO')
    if mes:
        query = query.filter(db.func.strftime('%m/%Y', HistoricoProducao.changed_at) == mes)
    if busca:
        like = f'%{busca}%'
        query = query.filter(db.or_(HistoricoProducao.op.ilike(like), HistoricoProducao.artigo.ilike(like)))
    return query.order_by(HistoricoProducao.changed_at.desc())


@main_bp.route('/aba/programacao/historico')
@login_required
def programacao_historico():
    """Dialog 'OPs Finalizadas — Histórico': OPs com status FINALIZADO no
    historico, filtravel por mes/busca — mesma fonte (production_history) e
    logica do botao Histórico do PCP Hub."""
    busca = request.args.get('q', '').strip()
    mes = request.args.get('mes', '').strip()

    meses_disponiveis = sorted({
        h.changed_at.strftime('%m/%Y') for h in
        HistoricoProducao.query.filter(HistoricoProducao.new_status == 'FINALIZADO').with_entities(HistoricoProducao.changed_at).all()
        if h.changed_at
    }, reverse=True)

    itens = _historico_finalizadas_query(busca, mes).limit(300).all()
    for h in itens:
        h.ds_metros = resolve_metros_from_article(h.artigo)

    hoje = datetime.utcnow()
    finalizados_mes_count = HistoricoProducao.query.filter(
        HistoricoProducao.new_status == 'FINALIZADO',
        db.func.strftime('%Y-%m', HistoricoProducao.changed_at) == hoje.strftime('%Y-%m'),
    ).count()
    cancelados_count = HistoricoProducao.query.filter(HistoricoProducao.new_status == 'CANCELADO').count()

    return render_template(
        'main/_programacao_historico.html', itens=itens, busca=busca, mes=mes,
        meses_disponiveis=meses_disponiveis, finalizados_mes_count=finalizados_mes_count,
        cancelados_count=cancelados_count,
    )


@main_bp.route('/aba/programacao/historico/download')
@login_required
def programacao_historico_download():
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    from io import BytesIO

    itens = _historico_finalizadas_query().all()

    wb = Workbook()
    ws = wb.active
    ws.title = 'Finalizadas'
    headers = ['Máquina', 'Artigo', 'DS.Metros', 'OP', 'Cliente', 'Qt Peças', 'Pçs/24h', 'Data Produção', 'Finalizada em', 'Comentário']
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True, color='FFFFFF')
        cell.fill = PatternFill('solid', fgColor='1E40AF')
        cell.alignment = Alignment(horizontal='center')
    for h in itens:
        ws.append([
            f'MÁQ {h.machine}' if h.machine else '', h.artigo, resolve_metros_from_article(h.artigo), h.op,
            h.cliente, h.quantity_pieces, h.pieces_per_day,
            h.date.strftime('%d/%m/%Y') if h.date else '',
            h.changed_at.strftime('%d/%m/%Y %H:%M') if h.changed_at else '',
            h.reason or '',
        ])
    for col in ws.columns:
        length = max((len(str(c.value)) for c in col if c.value is not None), default=10)
        ws.column_dimensions[col[0].column_letter].width = min(max(length + 2, 12), 50)

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return Response(
        buf.read(), mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        headers={'Content-Disposition': 'attachment; filename=ops_finalizadas.xlsx'},
    )


def _sorted_sequencias_for_download():
    return sorted(Sequencia.query.join(Machine).all(), key=lambda s: (s.machine.number, s.sequence_order or 0))


@main_bp.route('/aba/programacao/download')
@login_required
def programacao_download():
    """Excel 'layout profissional' com abas Produção / Resumo Completo /
    Finalizados / Interrompidos / Cancelados — mesma logica do botao
    Download do PCP Hub (3 modos: total / cliente / programadas)."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter
    from io import BytesIO

    modo = request.args.get('mode', 'total')
    cliente = (request.args.get('cliente') or '').strip()

    STATUS_LABEL = {
        'PCP': 'PCP', 'EM_SEQUENCIA': 'Em Sequência', 'PRODUZINDO': 'Produzindo',
        'FINALIZADO': 'Finalizado', 'INTERROMPIDO': 'Interrompido', 'CANCELADO': 'Cancelado',
    }

    class Row:
        """Formato comum pra linhas vindas da Sequencia (ativas) ou do
        Historico (finalizadas/interrompidas/canceladas), igual ao
        'MachineRow' do PCP Hub."""
        def __init__(self, machine, op, artigo, cliente, status, total_pcs, pecas_produzidas,
                     qt_solicitada, qt_avulsa, is_avulsa, sequence_order, dt_cliente,
                     termino_malharia, pieces_per_day, reason, created_at):
            self.machine = machine
            self.op = op
            self.artigo = artigo
            self.cliente = cliente
            self.status = status
            self.total_pcs = total_pcs or 0
            self.pecas_produzidas = pecas_produzidas or 0
            self.qt_solicitada = qt_solicitada or 0
            self.qt_avulsa = qt_avulsa or 0
            self.is_avulsa = is_avulsa
            self.sequence_order = sequence_order
            self.dt_cliente = dt_cliente
            self.termino_malharia = termino_malharia
            self.pieces_per_day = pieces_per_day
            self.reason = reason
            self.created_at = created_at

    ativos = [
        Row(s.machine.number, s.op, s.artigo, s.cliente, s.status, s.total_pcs, s.pecas_produzidas,
            s.qt_solicitada, s.qt_avulsa, bool(s.qt_avulsa), s.sequence_order, s.dt_cliente,
            s.termino_malharia, None, None, s.synced_at)
        for s in Sequencia.query.join(Machine).all() if s.machine
    ]

    fin_hist, inter_hist, canc_hist = [], [], []
    if modo in ('total', 'cliente'):
        fin_hist = HistoricoProducao.query.filter_by(new_status='FINALIZADO').all()
        inter_hist = HistoricoProducao.query.filter_by(new_status='INTERROMPIDO').all()
        canc_hist = HistoricoProducao.query.filter_by(new_status='CANCELADO').all()

        def dedup_by_op(rows):
            seen, out = set(), []
            for r in rows:
                key = r.op or f'{r.machine}-{r.artigo}-{r.changed_at}'
                if key in seen or not r.machine:
                    continue
                seen.add(key)
                out.append(r)
            return out

        def hist_to_row(h, status):
            return Row(h.machine, h.op, h.artigo, h.cliente, status, 0, h.quantity_pieces,
                       h.requested_quantity, None, False, 9999, h.client_date, h.estimated_end_date,
                       h.pieces_per_day, h.reason, h.changed_at)

        todos = ativos + [hist_to_row(h, 'FINALIZADO') for h in dedup_by_op(fin_hist)] \
            + [hist_to_row(h, 'INTERROMPIDO') for h in dedup_by_op(inter_hist)] \
            + [hist_to_row(h, 'CANCELADO') for h in dedup_by_op(canc_hist)]
    elif modo == 'programadas':
        todos = [r for r in ativos if r.status in ('PCP', 'EM_SEQUENCIA', 'PRODUZINDO')]
    else:
        todos = ativos

    if modo == 'cliente' and cliente:
        alvo = cliente.strip().upper()
        todos = [r for r in todos if (r.cliente or '').strip().upper() == alvo]

    todos.sort(key=lambda r: (r.machine or 0, r.sequence_order or 0))

    if not todos:
        flash('nada_para_exportar', 'error')
        return redirect(url_for('main.view_tab', slug='programacao'))

    wb = Workbook()
    ws = wb.active
    ws.title = 'Produção'
    ws.freeze_panes = 'A2'
    headers = [
        'Máquina', 'Seq.', 'Status', 'OP', 'Artigo', 'Cliente', 'Estrutura', 'DS.Metros',
        'Total Peças', 'Peças Produzidas', 'PÇ.PRODUZIR', 'Qt Solicitada', 'Pçs/24h',
        'Término Malharia', 'Prazo Cliente', 'Em Atraso', 'Qt Avulsa', 'Motivo', 'Atualizado em',
    ]
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True, color='FFFFFF')
        cell.fill = PatternFill('solid', fgColor='1E40AF')
        cell.alignment = Alignment(horizontal='center', wrap_text=True)

    for i, r in enumerate(todos):
        atrasado = bool(r.termino_malharia and r.dt_cliente and r.termino_malharia > r.dt_cliente)
        row = [
            f'MÁQ {r.machine}', r.sequence_order if (r.sequence_order or 0) < 9999 else None,
            STATUS_LABEL.get(r.status, r.status), r.op, r.artigo, r.cliente,
            resolve_estrutura_from_article(r.artigo), resolve_metros_from_article(r.artigo),
            r.total_pcs, r.pecas_produzidas, max(0, r.total_pcs - r.pecas_produzidas), r.qt_solicitada,
            r.pieces_per_day, r.termino_malharia.strftime('%d/%m/%Y') if r.termino_malharia else '',
            r.dt_cliente.strftime('%d/%m/%Y') if r.dt_cliente else '',
            'SIM' if atrasado else 'NÃO', r.qt_avulsa, r.reason or '',
            r.created_at.strftime('%d/%m/%Y %H:%M') if r.created_at else '',
        ]
        ws.append(row)
        fill = PatternFill('solid', fgColor='F8FAFC' if i % 2 == 0 else 'FFFFFF')
        for cell in ws[ws.max_row]:
            cell.fill = fill
        atraso_cell = ws.cell(row=ws.max_row, column=16)
        atraso_cell.fill = PatternFill('solid', fgColor='DC2626' if atrasado else '16A34A')
        atraso_cell.font = Font(bold=True, color='FFFFFF')

    for col_cells in ws.columns:
        length = max((len(str(c.value)) for c in col_cells if c.value is not None), default=10)
        ws.column_dimensions[col_cells[0].column_letter].width = min(max(length + 2, 12), 50)
    ws.auto_filter.ref = f'A1:{get_column_letter(len(headers))}{ws.max_row}'

    def add_sheet(name, color, cols, rows):
        s = wb.create_sheet(name)
        s.append(cols)
        for cell in s[1]:
            cell.font = Font(bold=True, color='FFFFFF')
            cell.fill = PatternFill('solid', fgColor=color)
        for row in rows:
            s.append(row)
        for col_cells in s.columns:
            length = max((len(str(c.value)) for c in col_cells if c.value is not None), default=10)
            s.column_dimensions[col_cells[0].column_letter].width = min(max(length + 2, 10), 40)

    add_sheet('Resumo Completo', '0EA5E9',
               ['Artigo', 'Cliente', 'OP', 'Estrutura', 'DS.Metros', 'Qt Avulsa', 'Peças Produzidas', 'Total Pçs', 'PÇ.PRODUZIR', 'Qt Solicitada', 'Motivo'],
               [[r.artigo, r.cliente, r.op, resolve_estrutura_from_article(r.artigo), resolve_metros_from_article(r.artigo),
                 r.qt_avulsa, r.pecas_produzidas, r.total_pcs, max(0, r.total_pcs - r.pecas_produzidas), r.qt_solicitada, r.reason or '']
                for r in todos])
    add_sheet('Finalizados', '16A34A',
               ['Data Finalização', 'Máquina', 'OP', 'Artigo', 'Cliente', 'Qt Peças', 'Qt Solicitada', 'Pçs/24h', 'Motivo'],
               [[h.changed_at.strftime('%d/%m/%Y %H:%M') if h.changed_at else '', h.machine, h.op, h.artigo, h.cliente,
                 h.quantity_pieces, h.requested_quantity, h.pieces_per_day, h.reason or ''] for h in fin_hist])
    add_sheet('Interrompidos', 'F59E0B',
               ['Data', 'Máquina', 'OP', 'Artigo', 'Cliente', 'Qt Peças', 'Qt Solicitada', 'Motivo'],
               [[h.changed_at.strftime('%d/%m/%Y %H:%M') if h.changed_at else '', h.machine, h.op, h.artigo, h.cliente,
                 h.quantity_pieces, h.requested_quantity, h.reason or ''] for h in inter_hist])
    add_sheet('Cancelados', 'DC2626',
               ['Data', 'Máquina', 'OP', 'Artigo', 'Cliente', 'Qt Peças', 'Qt Solicitada', 'Motivo'],
               [[h.changed_at.strftime('%d/%m/%Y %H:%M') if h.changed_at else '', h.machine, h.op, h.artigo, h.cliente,
                 h.quantity_pieces, h.requested_quantity, h.reason or ''] for h in canc_hist])

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    if modo == 'programadas':
        nome = f'producao_programadas_{datetime.utcnow().strftime("%Y-%m-%d")}.xlsx'
    elif modo == 'cliente' and cliente:
        slug_cliente = re.sub(r'[^a-z0-9_-]', '_', cliente.lower())[:40]
        nome = f'producao_{slug_cliente}_{datetime.utcnow().strftime("%Y-%m-%d")}.xlsx'
    else:
        nome = f'producao_maquinas_{datetime.utcnow().strftime("%Y-%m-%d")}.xlsx'
    return Response(
        buf.read(), mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        headers={'Content-Disposition': f'attachment; filename={nome}'},
    )


@main_bp.route('/aba/programacao/dias-sem-producao')
@login_required
def programacao_dias_sem_producao():
    dias = NonWorkingDay.query.order_by(NonWorkingDay.date.desc()).all()
    return render_template(
        'main/_programacao_dias_sem_producao.html', dias=dias,
        maquinas=Machine.query.order_by(Machine.number).all(),
    )


@main_bp.route('/aba/programacao/dias-sem-producao/adicionar', methods=['POST'])
@login_required
def programacao_dias_sem_producao_adicionar():
    if not current_user.is_admin:
        abort(403)
    data_str = request.form.get('date', '').strip()
    machine_number = request.form.get('machine_number', type=int)
    comment = (request.form.get('comment') or '').strip() or None
    try:
        data = datetime.strptime(data_str, '%Y-%m-%d').date()
    except ValueError:
        flash('valores_invalidos', 'error')
        return redirect(url_for('main.view_tab', slug='programacao'))
    machine = Machine.query.filter_by(number=machine_number).first() if machine_number else None
    db.session.add(NonWorkingDay(date=data, machine_id=machine.id if machine else None, comment=comment, created_by_id=current_user.id))
    db.session.commit()
    flash('registro_adicionado_sucesso', 'success')
    return redirect(url_for('main.view_tab', slug='programacao'))


@main_bp.route('/aba/programacao/dias-sem-producao/<int:item_id>/excluir', methods=['POST'])
@login_required
def programacao_dias_sem_producao_excluir(item_id):
    if not current_user.is_admin:
        abort(403)
    item = NonWorkingDay.query.get_or_404(item_id)
    db.session.delete(item)
    db.session.commit()
    flash('registro_excluido_sucesso', 'success')
    return redirect(url_for('main.view_tab', slug='programacao'))


@main_bp.route('/aba/programacao/dias-sem-producao/recalcular', methods=['POST'])
@login_required
def programacao_dias_sem_producao_recalcular():
    """Recalcula o Termino Malharia das OPs ainda nao finalizadas empurrando
    a data pra frente conforme os dias sem producao cadastrados (aproximacao
    razoavel do recalculo do PCP Hub, que nao pudemos extrair por completo)."""
    if not current_user.is_admin:
        abort(403)

    atualizadas = 0
    for machine in Machine.query.all():
        dias_maquina = sorted({
            d.date for d in NonWorkingDay.query.filter(
                db.or_(NonWorkingDay.machine_id == machine.id, NonWorkingDay.machine_id.is_(None))
            ).all()
        })
        if not dias_maquina:
            continue
        seqs = Sequencia.query.filter_by(machine_id=machine.id).filter(
            Sequencia.status.in_(['PCP', 'EM_SEQUENCIA']), Sequencia.termino_malharia.isnot(None)
        ).order_by(Sequencia.sequence_order.asc()).all()
        for s in seqs:
            pulados = [d for d in dias_maquina if s.termino_malharia and d >= s.termino_malharia]
            if pulados:
                s.termino_malharia = s.termino_malharia + timedelta(days=len(pulados))
                atualizadas += 1
    db.session.commit()
    flash('programacao_recalculada_sucesso', 'success')
    return redirect(url_for('main.view_tab', slug='programacao'))


@main_bp.route('/aba/pre-programacao/mover', methods=['POST'])
@login_required
def mover_pre_programacao():
    item_id = request.form.get('item_id', type=int)
    numero = request.form.get('machine_number', type=int)
    item = FilaPreProgramacao.query.get_or_404(item_id)
    machine = Machine.query.filter_by(number=numero).first() if numero else None
    if not machine:
        flash('novo_registro_maquina_invalida', 'error')
        return redirect(url_for('main.view_tab', slug='pre-programacao', aba='analisar'))

    estrutura = resolve_estrutura_from_article(item.artigo)
    cliente = normalize_client_name(item.cliente)
    dt_cliente = resolve_dt_cliente_from_pedidos(item.op, item.artigo)
    is_ortobom = cliente == 'ORTOBOM'

    proxima_ordem = db.session.query(db.func.coalesce(db.func.max(Sequencia.sequence_order), 0)).filter_by(
        machine_id=machine.id
    ).scalar() + 1

    db.session.add(Sequencia(
        machine_id=machine.id,
        sequence_order=proxima_ordem,
        artigo=item.artigo,
        cliente=item.cliente,
        op=item.op,
        status='PCP',
        total_pcs=item.total_pcs or 0,
        pecas_produzidas=0,
        qt_solicitada=item.requested_quantity or 0,
        qt_avulsa=0,
        cod_segmento=item.cod_segmento or resolve_cod_segmento_from_article(item.artigo),
        dt_cliente=None if is_ortobom else dt_cliente,
        production_month=item.production_month if is_ortobom else None,
        estrutura=estrutura,
        region='BR',
    ))

    item.status = 'MOVED'
    item.moved_to_machine = str(numero)
    item.moved_at = datetime.utcnow()
    db.session.commit()

    flash('pre_selecao_movida_sucesso', 'success')
    return redirect(url_for('main.view_tab', slug='pre-programacao', aba='analisar'))


@main_bp.route('/aba/pre-programacao/excluir/<int:item_id>', methods=['POST'])
@login_required
def excluir_pre_programacao(item_id):
    item = FilaPreProgramacao.query.get_or_404(item_id)
    db.session.delete(item)
    db.session.commit()
    flash('pre_selecao_excluida_sucesso', 'success')
    return redirect(url_for('main.view_tab', slug='pre-programacao', aba='analisar'))


@main_bp.route('/api/report32')
@login_required
def api_report32():
    """Dados reais de pesagem da malharia (Relatorio 32 / LOCAL 23), no mesmo
    formato consultado pelo Dashboard Tempo Real do PCP Hub (tabela
    report_32_movement), filtrados por intervalo de data (ISO 8601)."""
    from_str = request.args.get('from', '')
    to_str = request.args.get('to', '')

    def parse_iso(v):
        if not v:
            return None
        try:
            return datetime.fromisoformat(v.replace('Z', '+00:00')).replace(tzinfo=None)
        except ValueError:
            return None

    de = parse_iso(from_str)
    ate = parse_iso(to_str)

    query = Report32Movement.query
    if de:
        query = query.filter(Report32Movement.dt_real >= de)
    if ate:
        query = query.filter(Report32Movement.dt_real <= ate)

    rows = query.order_by(Report32Movement.dt_real.asc()).all()
    return jsonify([
        {
            'cod_maquina': r.cod_maquina,
            'cod_artigo': r.cod_artigo,
            'metro_padrao': r.metro_padrao,
            'qt_pesos': r.qt_pesos,
            'dt_real': r.dt_real.isoformat() + 'Z' if r.dt_real else None,
            'op': r.op,
        }
        for r in rows
    ])


def _norm_art(v):
    v = (v or '').strip()
    if not v:
        return ''
    return v.split()[0].split('-')[0].upper()


@main_bp.route('/api/historico-artigo/search')
@login_required
def api_historico_artigo_search():
    """Busca de artigos/OPs em todas as fontes locais (equivalente ao que o
    Historico por Artigo do PCP Hub faz em production_history, machine_production,
    priority_queue, report_orders_month e report_32_movement)."""
    termo = request.args.get('q', '').strip()
    if len(termo) < 2:
        return jsonify({'articles': [], 'ops': []})
    like = f'%{termo}%'

    artigos = {}
    def add_art(raw):
        chave = _norm_art(raw)
        if chave and chave not in artigos:
            artigos[chave] = (raw or '').strip()

    for (a,) in db.session.query(HistoricoProducao.artigo).filter(HistoricoProducao.artigo.ilike(like)).limit(2000):
        add_art(a)
    for (a,) in db.session.query(Sequencia.artigo).filter(Sequencia.artigo.ilike(like)).limit(2000):
        add_art(a)
    for (a,) in db.session.query(FilaPreProgramacao.artigo).filter(FilaPreProgramacao.artigo.ilike(like)).limit(2000):
        add_art(a)
    for (a,) in db.session.query(PedidoRelatorio.cod_produto).filter(PedidoRelatorio.cod_produto.ilike(like)).limit(2000):
        add_art(a)
    for (a,) in db.session.query(Report32Movement.cod_artigo).filter(Report32Movement.cod_artigo.ilike(like)).limit(2000):
        add_art(a)

    ops = {}
    def add_op(op, art):
        op = (op or '').strip()
        chave = _norm_art(art)
        if op and chave and op not in ops:
            ops[op] = chave

    for op, a in db.session.query(HistoricoProducao.op, HistoricoProducao.artigo).filter(HistoricoProducao.op.ilike(like)).limit(2000):
        add_op(op, a)
    for op, a in db.session.query(Sequencia.op, Sequencia.artigo).filter(Sequencia.op.ilike(like)).limit(2000):
        add_op(op, a)
    for op, a in db.session.query(Report32Movement.op, Report32Movement.cod_artigo).filter(Report32Movement.op.ilike(like)).limit(2000):
        add_op(op, a)

    return jsonify({
        'articles': sorted(
            [{'key': k, 'label': v} for k, v in artigos.items()],
            key=lambda x: x['key'],
        )[:60],
        'ops': sorted(
            [{'op': k, 'article': v} for k, v in ops.items()],
            key=lambda x: x['op'],
        )[:60],
    })


@main_bp.route('/api/historico-artigo/article-data')
@login_required
def api_historico_artigo_data():
    """Todas as linhas cruas necessarias pra montar o Historico por Artigo de
    um codigo: OPs ativas + historico + fila (pre-programacao) + pesagens do
    Relatorio 32 + arquivo diario. O cruzamento fica no front, igual ao PCP Hub."""
    art = _norm_art(request.args.get('art', ''))
    if not art:
        return jsonify({'historico': [], 'ativo': [], 'fila': [], 'movimentos': [], 'daily_cycles': []})

    like_prefix = f'{art}%'

    def matches(valor):
        return _norm_art(valor) == art

    historico = [
        {
            'id': r.id, 'op': r.op, 'machine': r.machine, 'artigo': r.artigo, 'cliente': r.cliente,
            'status': r.new_status, 'total_pcs': r.total_pcs, 'requested_quantity': r.requested_quantity,
            'changed_at': r.changed_at.isoformat() if r.changed_at else None,
        }
        for r in HistoricoProducao.query.filter(HistoricoProducao.artigo.ilike(like_prefix)).all()
        if matches(r.artigo)
    ]
    ativo = [
        {
            'id': r.id, 'op': r.op, 'machine': str(r.machine.number) if r.machine else None,
            'artigo': r.artigo, 'cliente': r.cliente, 'status': r.status, 'total_pcs': r.total_pcs,
            'requested_quantity': r.qt_solicitada, 'is_avulsa': bool(r.qt_avulsa), 'qt_avulsa': r.qt_avulsa,
            'created_at': r.synced_at.isoformat() if r.synced_at else None,
        }
        for r in Sequencia.query.join(Machine).filter(Sequencia.artigo.ilike(like_prefix)).all()
        if matches(r.artigo)
    ]
    ops_relevantes = {r['op'] for r in historico if r['op']} | {r['op'] for r in ativo if r['op']}
    fila = [
        {
            'op': r.op, 'moved_to_machine': r.moved_to_machine, 'is_avulsa': r.is_avulsa,
            'created_at': r.created_at.isoformat() if r.created_at else None,
            'moved_at': r.moved_at.isoformat() if r.moved_at else None,
        }
        for r in FilaPreProgramacao.query.filter(FilaPreProgramacao.op.in_(ops_relevantes)).all()
    ] if ops_relevantes else []

    movimentos = [
        {
            'op': r.op, 'nr_lote': r.nr_lote, 'nr_item': r.nr_item, 'local_code': r.local_code,
            'cod_artigo': r.cod_artigo, 'qt_movimento': r.qt_movimento, 'dt_real': r.dt_real.isoformat() if r.dt_real else None,
            'cod_maquina': r.cod_maquina,
        }
        for r in Report32Movement.query.filter(Report32Movement.op.in_(ops_relevantes)).all()
    ] if ops_relevantes else []

    daily_cycles = [
        {'machine': r.machine, 'op': r.op, 'cycle_date': r.cycle_date.isoformat() if r.cycle_date else None,
         'pieces': r.pieces, 'meters': r.meters}
        for r in ArticleDailyCycle.query.filter(ArticleDailyCycle.article == art).order_by(ArticleDailyCycle.cycle_date.asc()).all()
    ]

    return jsonify({'historico': historico, 'ativo': ativo, 'fila': fila, 'movimentos': movimentos, 'daily_cycles': daily_cycles})


@main_bp.route('/api/historico-artigo/machines')
@login_required
def api_historico_artigo_machines():
    numeros = {
        str(int(m)) for (m,) in db.session.query(ArticleDailyCycle.machine).distinct()
        if m and m.strip().isdigit()
    }
    return jsonify(sorted(numeros, key=int))


@main_bp.route('/api/historico-artigo/machine-data')
@login_required
def api_historico_artigo_machine_data():
    maquina = request.args.get('machine', '').strip()
    rows = [
        {'cycle_date': r.cycle_date.isoformat() if r.cycle_date else None, 'article': r.article,
         'op': r.op, 'pieces': r.pieces, 'meters': r.meters}
        for r in ArticleDailyCycle.query.all()
        if r.machine and str(int(r.machine)) == maquina
    ]
    rows.sort(key=lambda r: r['cycle_date'] or '', reverse=True)
    return jsonify(rows)


@main_bp.route('/api/historico-artigo/local30')
@login_required
def api_historico_artigo_local30():
    """Pesagens do LOCAL 30 (acabamento). Ainda sem agente publicando dados
    reais — a tabela existe mas fica vazia ate o agente do LOCAL 30 rodar,
    igual ao PCP Hub hoje."""
    return jsonify([])


def _build_client_maps():
    manual = {m.cod_produto: m.nome_cliente for m in ArticleClientMapping.query.all() if m.cod_produto}
    especial = {e.cod_produto: e.cliente for e in ArticleClienteEspecial.query.all() if e.cod_produto}
    report = {}
    for p in PedidoRelatorio.query.filter(PedidoRelatorio.cod_produto.isnot(None)).all():
        if p.cod_produto and p.cod_produto not in report and p.nome_cliente:
            report[p.cod_produto] = p.nome_cliente
    return manual, especial, report


def _resolve_client_fast(cod_artigo, manual, especial, report):
    raw = (cod_artigo or '').strip()
    if not raw:
        return None
    partes = raw.split()
    cod = partes[0].split('-')[0] if partes else ''
    for chave in (cod, raw):
        if chave and chave in manual:
            return normalize_client_name(manual[chave])
    for chave in (cod, raw):
        if chave and chave in especial:
            return especial[chave]
    for chave in (cod, raw):
        if chave and chave in report:
            return normalize_client_name(report[chave])
    return None


@main_bp.route('/api/malharia/movimentos')
@login_required
def api_malharia_movimentos():
    """Todas as pesagens reais do Relatorio 32 (mesma fonte do Dashboard Tempo
    Real e do Historico por Artigo), ja com o cliente resolvido por artigo —
    fonte unica de dados da Gestao da Malharia, igual ao PCP Hub."""
    manual, especial, report = _build_client_maps()
    cache = {}

    def cliente_de(art):
        if art not in cache:
            cache[art] = _resolve_client_fast(art, manual, especial, report)
        return cache[art]

    rows = Report32Movement.query.order_by(Report32Movement.dt_real.asc()).all()
    return jsonify([
        {
            'id': r.id, 'cod_maquina': r.cod_maquina, 'op': r.op, 'nr_item': r.nr_item,
            'cod_artigo': r.cod_artigo, 'qt_movimento': r.qt_movimento, 'qt_pesos': r.qt_pesos,
            'dt_real': r.dt_real.isoformat() if r.dt_real else None, 'metro_padrao': r.metro_padrao,
            'local_code': r.local_code, 'cliente': cliente_de(r.cod_artigo),
        }
        for r in rows
    ])


@main_bp.route('/api/malharia/sequencia')
@login_required
def api_malharia_sequencia():
    """Fila ativa de cada maquina (machine_production do PCP Hub): a OP que
    esta PRODUZINDO agora (posicao 1) seguida das que estao EM_SEQUENCIA, na
    ordem real de producao — usado no Mapeamento de Sequencia."""
    rows = (
        Sequencia.query.join(Machine)
        .filter(Sequencia.status.in_(['PRODUZINDO', 'EM_SEQUENCIA']))
        .order_by(Sequencia.sequence_order.asc())
        .all()
    )
    return jsonify([
        {
            'machine': str(r.machine.number), 'article': r.artigo, 'client': r.cliente,
            'op': r.op, 'status': r.status, 'sequence_order': r.sequence_order,
        }
        for r in rows
    ])


@main_bp.route('/api/malharia/status')
@login_required
def api_malharia_status():
    """Data/hora da ultima sincronizacao do Relatorio 32 e total de registros,
    pra mostrar a barra de status no topo da Gestao da Malharia."""
    total = Report32Movement.query.count()
    ultimo = db.session.query(db.func.max(Report32Movement.synced_at)).scalar()
    return jsonify({
        'last_sync': ultimo.isoformat() + 'Z' if ultimo else None,
        'count': total,
    })


def _get_or_create_quality_settings():
    settings = QualitySettings.query.first()
    if not settings:
        settings = QualitySettings()
        db.session.add(settings)
        db.session.commit()
    return settings


@main_bp.route('/api/controle-producao/settings', methods=['GET', 'POST'])
@login_required
def api_controle_producao_settings():
    """Metas/capacidades editaveis do Controle de Producao (linha unica,
    igual a quality_settings do PCP Hub)."""
    settings = _get_or_create_quality_settings()
    if request.method == 'POST':
        if not current_user.is_admin:
            abort(403)
        data = request.get_json(silent=True) or {}
        for campo in ('meta_dia', 'meta_turno', 'media_padrao', 'cap_maquinas', 'cap_pecas_turno', 'cap_metros_turno'):
            if campo in data and data[campo] not in (None, ''):
                setattr(settings, campo, data[campo])
        settings.updated_at = datetime.utcnow()
        settings.updated_by_email = current_user.email
        db.session.commit()
    return jsonify({
        'meta_dia': settings.meta_dia, 'meta_turno': settings.meta_turno,
        'media_padrao': settings.media_padrao, 'cap_maquinas': settings.cap_maquinas,
        'cap_pecas_turno': settings.cap_pecas_turno, 'cap_metros_turno': settings.cap_metros_turno,
        'updated_at': settings.updated_at.isoformat() + 'Z' if settings.updated_at else None,
        'updated_by_email': settings.updated_by_email,
    })


@main_bp.route('/api/controle-producao/reprovas')
@login_required
def api_controle_producao_reprovas():
    """Reprovas do cru (Relatorio 24). Ainda sem agente publicando dados reais
    — a fonte existe mas fica vazia ate o agente do Relatorio 24 rodar, igual
    ao PCP Hub hoje (tabela report_24_reproves tambem esta zerada la)."""
    return jsonify([])


@main_bp.route('/aba/programacao/lookup-artigo')
@login_required
def lookup_artigo():
    artigo = request.args.get('artigo', '').strip()
    op = request.args.get('op', '').strip()
    cliente = resolve_client_from_article(artigo)
    estrutura = resolve_estrutura_from_article(artigo)
    cod_segmento = resolve_cod_segmento_from_article(artigo)
    dt_cliente = resolve_dt_cliente_from_pedidos(op, artigo) if op else None
    is_ortobom = cliente == 'ORTOBOM'
    return jsonify({
        'cliente': cliente,
        'estrutura': estrutura,
        'cod_segmento': cod_segmento,
        'dt_cliente': dt_cliente.isoformat() if dt_cliente else None,
        'is_ortobom': is_ortobom,
    })


@main_bp.route('/aba/programacao/novo-registro', methods=['POST'])
@login_required
def novo_registro_programacao():
    numero = request.form.get('machine_number', type=int)
    machine = Machine.query.filter_by(number=numero).first() if numero else None
    if not machine:
        flash('novo_registro_maquina_invalida', 'error')
        return redirect(url_for('main.view_tab', slug='programacao'))

    artigo = request.form.get('artigo', '').strip()
    op = request.form.get('op', '').strip()

    cliente = request.form.get('cliente', '').strip() or resolve_client_from_article(artigo)
    estrutura = (request.form.get('estrutura', '').strip() or resolve_estrutura_from_article(artigo) or '').upper()
    cod_segmento = request.form.get('cod_segmento', '').strip() or resolve_cod_segmento_from_article(artigo)

    estruturas_ativas = {c.estrutura for c in machine.capacities if c.ativa}
    if estrutura and estruturas_ativas and estrutura not in estruturas_ativas:
        flash('novo_registro_estrutura_nao_habilitada', 'error')
        return redirect(url_for('main.view_tab', slug='programacao'))

    def parse_data(campo):
        valor = request.form.get(campo, '').strip()
        if not valor:
            return None
        try:
            return datetime.strptime(valor, '%Y-%m-%d').date()
        except ValueError:
            return None

    def parse_int(campo):
        valor = request.form.get(campo, '').strip()
        try:
            return int(valor) if valor else 0
        except ValueError:
            return 0

    proxima_ordem = db.session.query(db.func.coalesce(db.func.max(Sequencia.sequence_order), 0)).filter_by(
        machine_id=machine.id
    ).scalar() + 1

    is_ortobom = normalize_client_name(cliente) == 'ORTOBOM'
    dt_cliente = parse_data('dt_cliente')
    production_month = request.form.get('production_month', '').strip() or None
    if not is_ortobom:
        production_month = None
        if not dt_cliente:
            dt_cliente = resolve_dt_cliente_from_pedidos(op, artigo)

    seq = Sequencia(
        machine_id=machine.id,
        sequence_order=proxima_ordem,
        artigo=artigo,
        cliente=cliente,
        op=op,
        status='PCP',  # toda OP nova entra como PCP, igual ao PCP Hub
        total_pcs=parse_int('total_pcs'),
        pecas_produzidas=0,
        qt_solicitada=parse_int('qt_solicitada'),
        qt_avulsa=parse_int('qt_avulsa'),
        cod_segmento=cod_segmento,
        dt_cliente=dt_cliente,
        production_month=production_month,
        termino_malharia=parse_data('termino_malharia'),
        estrutura=estrutura or None,
        region='BR',
    )
    db.session.add(seq)
    db.session.commit()
    flash('novo_registro_sucesso', 'success')
    return redirect(url_for('main.maquina_detalhe', numero=machine.number))


@main_bp.route('/aba/programacao/maquina/<int:numero>')
@login_required
def maquina_detalhe(numero):
    tab = Tab.query.filter_by(slug='programacao').first_or_404()
    if not tab.visible_to(current_user):
        abort(403)
    machine = Machine.query.filter_by(number=numero).first_or_404()
    sequencias = (
        Sequencia.query.filter_by(machine_id=machine.id)
        .order_by(Sequencia.sequence_order)
        .all()
    )
    rodando = any(s.status == 'PRODUZINDO' for s in sequencias)
    total_pcs_maquina = sum(s.total_pcs or 0 for s in sequencias)
    produzindo_count = sum(1 for s in sequencias if s.status == 'PRODUZINDO')
    return render_template(
        'main/maquina_detalhe.html',
        tab=tab,
        machine=machine,
        sequencias=sequencias,
        rodando=rodando,
        total_pcs_maquina=total_pcs_maquina,
        produzindo_count=produzindo_count,
    )
