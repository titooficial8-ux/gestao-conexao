import os
import time
import json
import hashlib
import gzip
import re
import unicodedata

from flask import Blueprint, render_template, abort, redirect, url_for, request, flash, jsonify, Response, session
from flask_login import login_required, current_user
from datetime import date, datetime, timedelta, timezone

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


def _pedido_aberto():
    """Filtro: pedidos que nao estao ATENDIDO nem CANCELADO (o relatorio mostra os dois, mas eles nao entram nas contas da programacao)."""
    return db.or_(PedidoRelatorio.tp_situacao.is_(None), PedidoRelatorio.tp_situacao.notin_(['ATENDIDO', 'CANCELADO']))


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
        _pedido_aberto(),
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


from app.totvs.artigos import dados_do_artigo  # noqa: E402


_ESTRUTURA_CURTA = {'CM': 'COMUM', 'PL': 'PLANO', 'NV': 'NOVA', 'XP': 'XPREMIUM', 'XN': 'XNEW', 'CF': 'COMFORT'}


def estrutura_curta(texto):
    """'ESTRUTURA COMUM' / 'CM' -> 'COMUM' (nome curto que aparece no cartao da maquina)."""
    t = _strip_accents((texto or '').upper()).strip()
    if not t:
        return None
    t = _ESTRUTURA_CURTA.get(t, t.replace('ESTRUTURA', '').strip())
    return _ESTRUTURA_CURTA.get(t, t) or None


def indicadores_maquinas(machines):
    """{id da maquina: dict(tipo, codigo, titulo)} do cliente que esta rodando (OP PRODUZINDO):
    ORTOBOM -> logo; COLCHAO ME (exportacao) -> bandeira do pais do cliente; demais (nacional) -> bandeira do Brasil."""
    from app.models import ClientePais
    from app.paises import PAISES, pais_do_cliente
    tabela = {c.nome_normalizado: c.pais for c in ClientePais.query.all()}
    saida = {}
    for m in machines:
        prod = next((s for s in m.sequencias if (s.status or '') == 'PRODUZINDO'), None)
        if not prod:
            continue
        cliente = prod.cliente or ''
        nome_cli = _strip_accents(cliente.upper())
        if 'ORTOBOM' in nome_cli:
            saida[m.id] = dict(tipo='ortobom', titulo=f'Ortobom · {cliente}')
        elif 'GAZIN' in nome_cli:
            saida[m.id] = dict(tipo='gazin', titulo=f'Gazin · {cliente}')
        elif 'ECOFLEX' in nome_cli:
            saida[m.id] = dict(tipo='ecoflex', titulo=f'Ecoflex · {cliente}')
        elif re.search(r'\bFLEX\b', nome_cli):
            saida[m.id] = dict(tipo='flex', titulo=f'Flex do Brasil · {cliente}')
        elif (prod.cod_segmento or '').strip().upper() == 'COLCHAO ME':
            cod = pais_do_cliente(cliente, tabela)
            if cod:
                saida[m.id] = dict(tipo='flag', codigo=cod, titulo=f'Exportação · {PAISES[cod][0]} · {cliente}')
            else:
                saida[m.id] = dict(tipo='exp', titulo=f'Exportação · país não cadastrado · {cliente}')
        else:
            saida[m.id] = dict(tipo='flag', codigo='br', titulo=f'Nacional · {cliente}')
    return saida


def estruturas_rodando(machines):
    """{id da maquina: nome curto da estrutura que esta rodando (OP PRODUZINDO)}; sem OP produzindo, nao aparece."""
    saida = {}
    for m in machines:
        prod = next((s for s in m.sequencias if (s.status or '') == 'PRODUZINDO'), None)
        if prod:
            est = estrutura_curta(prod.estrutura or resolve_estrutura_from_article(prod.artigo))
            if est:
                saida[m.id] = est
    return saida


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
    # 2) estrutura lida do TOTVS (classificacao do artigo) e 3) estrutura do Relatorio Pedidos
    a = dados_do_artigo(artigo)
    if a and a.estrutura:
        return a.estrutura
    p = _find_pedido_por_artigo(artigo)
    if p and p.estrutura:
        return p.estrutura
    return None


def resolve_metros_from_article(artigo):
    """DS.Metros (metragem padrao) do artigo: 1) METRO IDEAL LOTE lido do TOTVS (acompanha qualquer mudanca de la),
    2) Base Estrutura (copia do PCP Hub) como reserva para artigo que o TOTVS ainda nao informou."""
    artigo = (artigo or '').strip()
    if not artigo:
        return None
    a = dados_do_artigo(artigo)
    if a and a.metro_ideal:
        return str(int(a.metro_ideal)) if float(a.metro_ideal).is_integer() else str(a.metro_ideal)
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

@main_bp.route('/raport', methods=['GET', 'POST'])
@login_required
def raport_cadastro():
    """So administrador: raport por estrutura, excecao por maquina (ex.: 4 e 8 em CM = 4,0), RPM instalado e tamanho da peca por artigo."""
    if not current_user.is_admin:
        abort(403)
    from app import previsao
    from app.models import ArtigoTamanho, MaquinaRpm, RaportEstrutura, RaportMaquinaEstrutura
    previsao.semear_raport()

    def num(v):
        try:
            return float(str(v).replace(',', '.'))
        except ValueError:
            return None
    if request.method == 'POST':
        f = request.form
        for chave, valor in f.items():
            tipo, _, ident = chave.partition(':')
            v = num(valor)
            if tipo == 'e' and v:
                RaportEstrutura.query.filter_by(estrutura=ident).update({'raport': v})
            elif tipo == 'r' and ident.isdigit():
                MaquinaRpm.query.filter_by(maquina=int(ident)).update({'rpm_instalado': v}) if v else None
            elif tipo == 'x':
                RaportMaquinaEstrutura.query.filter_by(id=int(ident)).delete() if f.get('del:' + ident) else (v and RaportMaquinaEstrutura.query.filter_by(id=int(ident)).update({'raport': v}))
            elif tipo == 't' and v:
                ArtigoTamanho.query.filter_by(artigo=ident).update({'metros_peca': v})
        m, e, r = num(f.get('novo_maq')), (f.get('novo_estr') or '').strip().upper(), num(f.get('novo_raport'))
        if m and e and r:
            RaportMaquinaEstrutura.query.filter_by(maquina=int(m), estrutura=e).delete()
            db.session.add(RaportMaquinaEstrutura(maquina=int(m), estrutura=e, raport=r))
        if num(f.get('novo_rpm_maq')) and num(f.get('novo_rpm')):
            mq = int(num(f['novo_rpm_maq']))
            MaquinaRpm.query.filter_by(maquina=mq).delete()
            db.session.add(MaquinaRpm(maquina=mq, rpm_instalado=num(f['novo_rpm'])))
        if (f.get('novo_art') or '').strip() and num(f.get('novo_tam')):
            ArtigoTamanho.query.filter_by(artigo=f['novo_art'].strip()).delete()
            db.session.add(ArtigoTamanho(artigo=f['novo_art'].strip(), metros_peca=num(f['novo_tam']), estrutura=(f.get('novo_art_estr') or '').strip().upper() or None))
        db.session.commit()
        flash('Raport salvo. A previsao usa os novos valores no proximo calculo (a cada poucos minutos).', 'success')
        return redirect(url_for('main.raport_cadastro'))
    return render_template('main/raport.html', estruturas=RaportEstrutura.query.order_by(RaportEstrutura.estrutura).all(),
                           excecoes=RaportMaquinaEstrutura.query.order_by(RaportMaquinaEstrutura.maquina).all(),
                           rpms=MaquinaRpm.query.order_by(MaquinaRpm.maquina).all(), artigos=ArtigoTamanho.query.order_by(ArtigoTamanho.artigo).all())


@main_bp.route('/aba/dt-cliente-diagnostico')
@login_required
def dt_cliente_diagnostico():
    """So administrador: por que uma OP ficou sem Dt.Cliente? Mostra as linhas do Relatorio Pedidos ligadas a ela e as datas de previsao."""
    if not current_user.is_admin:
        abort(403)
    op = (request.args.get('op') or '').strip()
    linhas = PedidoRelatorio.query.filter_by(nr_op=op).all()
    seq = Sequencia.query.filter_by(op=op).first()
    return jsonify(op=op, artigo_na_programacao=seq.artigo if seq else None, dt_cliente_na_programacao=seq.dt_cliente.isoformat() if seq and seq.dt_cliente else None,
                   resolvido=(resolve_dt_cliente_from_pedidos(op, seq.artigo if seq else None).isoformat() if seq and resolve_dt_cliente_from_pedidos(op, seq.artigo) else None),
                   linhas=[dict(pedido=p.cod_pedido, cliente=p.nome_cliente, situacao=p.tp_situacao, dt_pedido=str(p.dt_pedido or ''), dt_prev_faturamento=str(p.dt_prevfaturamento or ''), chegada30=str(p.dt_chegada30 or ''),
                                cod_produto=p.cod_produto, produto=p.ds_produto, qt_solicitada=p.qt_solicitada, qt_pendente=p.qt_pendente) for p in linhas],
                   outras_op_do_mesmo_cliente_artigo=[dict(op=p.nr_op, pedido=p.cod_pedido, dt_prev=str(p.dt_prevfaturamento or '')) for p in PedidoRelatorio.query.filter(PedidoRelatorio.nome_cliente == (seq.cliente if seq else None)).limit(20).all()] if seq else [])


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
        total_sequencias = Sequencia.query.filter(Sequencia.status.notin_(['FINALIZADO', 'CANCELADO'])).count()

        busca = request.args.get('q', '').strip()
        filtro_cliente = request.args.get('cliente', '').strip()
        filtro_mes = request.args.get('mes', '').strip()

        todas_sequencias = Sequencia.query.filter(Sequencia.status.notin_(['FINALIZADO', 'CANCELADO'])).all()
        hoje_ = date.today()
        maquinas_gargalo = {q.machine.number for q in todas_sequencias if q.machine and sequencia_atrasada(q, hoje_)}      # maquina com OP passando da data do cliente
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
                for s in (x for x in m.sequencias if x.status not in ('FINALIZADO', 'CANCELADO')):
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
                est_rodando=estruturas_rodando(machines), ind_maquinas=indicadores_maquinas(machines), maquinas_gargalo=maquinas_gargalo,
            )

        return render_template(
            'main/programacao.html',
            tab=tab,
            machines=machines,
            est_rodando=estruturas_rodando(machines), ind_maquinas=indicadores_maquinas(machines), maquinas_gargalo=maquinas_gargalo,
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
        for termo in busca.split():          # cada palavra precisa aparecer em alguma coluna (ex.: "flex 300036")
            like = f'%{termo}%'
            query = query.filter(
                db.or_(
                    PedidoRelatorio.cod_pedido.ilike(like),
                    PedidoRelatorio.nome_cliente.ilike(like),
                    PedidoRelatorio.unidade.ilike(like),
                    PedidoRelatorio.cod_produto.ilike(like),
                    PedidoRelatorio.ds_produto.ilike(like),
                    PedidoRelatorio.nr_op.ilike(like),
                    PedidoRelatorio.ops_extra.ilike(like),
                    PedidoRelatorio.cod_segmento.ilike(like),
                    PedidoRelatorio.estrutura.ilike(like),
                    PedidoRelatorio.tp_situacao.ilike(like),
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
        ops_sem_pedido = []
        if busca and not total:                    # o relatorio e feito por PEDIDO: OP sem pedido ligado (avulsa) nao tem linha; explica em vez de so dizer "nenhum pedido"
            for termo in busca.split():
                if not termo.isdigit():
                    continue
                sq = Sequencia.query.filter_by(op=termo).first()
                fila = None if sq else FilaPreProgramacao.query.filter_by(op=termo).first()
                ref = sq or fila
                if not ref:
                    continue
                cod = (ref.artigo or '').split(' ')[0]
                abertos = [x for x in PedidoRelatorio.query.filter(PedidoRelatorio.cod_produto == cod, PedidoRelatorio.qt_pendente > 0.5).all() if normalize_client_name(x.nome_cliente or '') == normalize_client_name(ref.cliente or '')]
                ops_sem_pedido.append(dict(op=termo, artigo=ref.artigo, cliente=ref.cliente, qt=int((getattr(ref, 'qt_solicitada', None) or getattr(ref, 'requested_quantity', 0) or 0)),
                                           onde=(f'máquina {sq.machine.number:02d}' if sq and sq.machine else 'Pré-Seleção'),
                                           pedidos_abertos=sorted({x.cod_pedido for x in abertos})))
        return render_template(
            'main/relatorio_pedidos.html',
            tab=tab,
            ops_sem_pedido=ops_sem_pedido,
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

        # so as OPs que estao na Programacao (finalizada = pesagem do cru concluida, cancelada = saiu da fila), igual as listas da aba
        ativas_q = Sequencia.query.filter(Sequencia.status.notin_(['FINALIZADO', 'CANCELADO']))
        total_sequencias = ativas_q.count()
        seq_produzindo = Sequencia.query.filter_by(status='PRODUZINDO').count()
        seq_em_sequencia = Sequencia.query.filter_by(status='EM_SEQUENCIA').count()
        seq_pcp = Sequencia.query.filter_by(status='PCP').count()

        fila_aguardando = FilaPreProgramacao.query.filter_by(status='WAITING').count()
        fila_movida = FilaPreProgramacao.query.filter_by(status='MOVED').count()

        total_pedidos = PedidoRelatorio.query.count()
        pedidos_pendentes = PedidoRelatorio.query.filter(PedidoRelatorio.qt_saldo > 0, _pedido_aberto()).count()

        ultimas_mudancas = (
            HistoricoProducao.query.order_by(HistoricoProducao.changed_at.desc()).limit(8).all()
        )

        todas_sequencias = ativas_q.all()
        from app.models import PesagemResumo
        metros_reais = {r.op: r.metros for r in PesagemResumo.query.all() if r.metros}     # metros ja pesados no local 23 (API do TOTVS)
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
                info['metragem_real'] = info.get('metragem_real', 0) + (metros_reais.get(str(s.op)) or 0)
            for info in por_maquina.values():
                # Metros produzidos: os metros reais ja pesados no local 23 (API do TOTVS); sem pesagem,
                # estima pela proporcao de pecas produzidas sobre o total de pecas da OP.
                if info.get('metragem_real'):
                    info['metragem_produzida'] = round(info['metragem_real'])
                elif info['total_pcs']:
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
            est_curta = estrutura_curta(s.estrutura or resolve_estrutura_from_article(s.artigo))     # 'ESTRUTURA NOVA' e 'NOVA' sao a mesma estrutura
            if s.status == 'PRODUZINDO' and est_curta:
                por_estrutura.setdefault(est_curta, []).append(s)
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
                info['metragem_real'] = info.get('metragem_real', 0) + (metros_reais.get(str(s.op)) or 0)
            for info in por_maquina_e.values():
                info['metragem_produzida'] = (
                    round(info['metragem_real']) if info.get('metragem_real') else
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
        aba = 'analisar'                       # a Pre-Programacao so tem a aba Analisar Sequencia (os Pedidos do Relatorio foram removidos)

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
            estrutura_atual = estrutura_curta(seq_ativa.estrutura) if seq_ativa else None
            estrutura_por_maquina.append({'maquina': m.number, 'estrutura': estrutura_atual})

            if estrutura_atual and m.esta_ativa:
                ops_na_estrutura = sum(
                    1 for s in m.sequencias if s.status in ATIVAS_STATUS and estrutura_curta(s.estrutura) == estrutura_atual
                )
                maquinas_por_estrutura.setdefault(estrutura_atual, []).append(
                    {'maquina': m.number, 'ops': ops_na_estrutura}
                )
        for lista in maquinas_por_estrutura.values():
            lista.sort(key=lambda x: x['ops'], reverse=True)

        pedidos_da_op = {}                         # pedido(s) ligados a cada OP (calculo do TOTVS); OP avulsa nao tem pedido e fica em branco
        for r in PedidoRelatorio.query.filter(PedidoRelatorio.nr_op.isnot(None), PedidoRelatorio.nr_op != '').all():
            lista = pedidos_da_op.setdefault(str(r.nr_op), [])
            if r.cod_pedido and r.cod_pedido not in lista:
                lista.append(r.cod_pedido)
        analisar_itens = [
            {
                'item': item,
                'estrutura': (estrutura := estrutura_curta(resolve_estrutura_from_article(item.artigo))),
                'dt_prevfaturamento': _fila_dt_prev(item),
                'avulsa': (avulsa := 'CONEXAO MALHAS' in (item.cliente or '').upper() or bool(item.is_avulsa)),
                'cliente': (cli := ((_cliente_pelo_artigo(item.artigo) if avulsa else None) or normalize_client_name(item.cliente or ''))),
                'ortobom': cli == 'ORTOBOM',
                'pedidos': pedidos_da_op.get(str(item.op), []),
                'sugeridas': maquinas_por_estrutura.get(estrutura, [])[:6],
            }
            for item in FilaPreProgramacao.query.filter_by(status='WAITING')
            .order_by(FilaPreProgramacao.created_at.desc()).all()
        ]

        return render_template(
            'main/pre_programacao.html',
            tab=tab,
            aba=aba,
            estrutura_por_maquina=estrutura_por_maquina,
            analisar_itens=analisar_itens,
            total_analisar=len(analisar_itens),
        )


    if slug == 'historico':
        return render_template('main/historico_artigo.html', tab=tab)

    if slug == 'controle-acessos':
        if not current_user.is_admin:
            abort(403)
        return redirect(url_for('admin.users'))

    if slug == 'dashboard-comercial':
        from app.relatorio_fat import agenda
        from app.relatorio_fat.routes import pode_gerar
        destinos_ped = [x.strip() for x in agenda.configuracao()['destinos'].replace(';', ',').split(',') if x.strip()]
        return render_template('main/bi_comercial.html', tab=tab, pode_relatorio=pode_gerar(current_user), destinos_pedidos=destinos_ped)

    if slug == 'dashboard-estoque':
        return render_template('main/dashboard_estoque.html', tab=tab, **DASHBOARD_ESTOQUE_MODELO)

    if slug in ('cronograma-dev', 'cotacoes', 'cronograma-amostras', 'cronograma-projetos', 'laudos-desenvolvimento', 'produtos-tecnologicos', 'material-institucional'):
        return render_template(f'desenvolvimento/{slug}.html', tab=tab)

    if slug == 'controle-estoque':
        return render_template('main/controle_estoque.html', tab=tab)

    if slug == 'indicadores':
        return render_template('main/indicadores.html', tab=tab)

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

    if slug == 'painel-financeiro':
        return render_template('main/financeiro.html', tab=tab)

    if slug == 'expedicao':
        return render_template('main/expedicao.html', tab=tab)

    if slug == 'controle-artigos':
        return render_template('main/controle_artigos.html', tab=tab)

    if slug == 'pedidos-comercial-mi':
        return render_template('main/comercial_mi.html', tab=tab)

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


def _historico_query(tipo='finalizadas', busca='', mes=''):
    status = 'CANCELADO' if tipo == 'canceladas' else 'FINALIZADO'
    query = HistoricoProducao.query.filter(HistoricoProducao.new_status == status)
    if mes:
        query = query.filter(db.func.strftime('%m/%Y', HistoricoProducao.changed_at) == mes)
    if busca:
        like = f'%{busca}%'
        query = query.filter(db.or_(HistoricoProducao.op.ilike(like), HistoricoProducao.artigo.ilike(like)))
    return query.order_by(HistoricoProducao.changed_at.desc())


def _historico_finalizadas_query(busca='', mes=''):
    return _historico_query('finalizadas', busca, mes)


def _eh_pcp():
    """So o PCP justifica cancelamentos: admin ou cargo que edita Planejamento (Planejamento/Vendas). Nao vale o setor: todos os usuarios estao em todos os setores."""
    if current_user.is_admin:
        return True
    cat = Category.query.filter_by(slug='planejamento').first()
    return bool(cat and current_user.can_edit_category(cat))


@main_bp.route('/aba/programacao/historico')
@login_required
def programacao_historico():
    """Dialog 'OPs Finalizadas / Canceladas — Histórico': OPs que a pesagem do cru terminou (FINALIZADO) ou que foram
    canceladas no TOTVS (CANCELADO), filtravel por mes/busca — mesma fonte (production_history) do botao Histórico do PCP Hub."""
    busca = request.args.get('q', '').strip()
    mes = request.args.get('mes', '').strip()
    tipo = 'canceladas' if request.args.get('tipo') == 'canceladas' else 'finalizadas'
    status = 'CANCELADO' if tipo == 'canceladas' else 'FINALIZADO'

    meses_disponiveis = sorted({
        (h.changed_at - timedelta(hours=3)).strftime('%m/%Y') for h in
        HistoricoProducao.query.filter(HistoricoProducao.new_status == status).with_entities(HistoricoProducao.changed_at).all()
        if h.changed_at
    }, reverse=True)

    itens = _historico_query(tipo, busca, mes).limit(300).all()
    for h in itens:
        h.ds_metros = resolve_metros_from_article(h.artigo)

    hoje = datetime.utcnow()
    finalizados_mes_count = HistoricoProducao.query.filter(
        HistoricoProducao.new_status == 'FINALIZADO',
        db.func.strftime('%Y-%m', HistoricoProducao.changed_at) == hoje.strftime('%Y-%m'),
    ).count()
    cancelados_count = HistoricoProducao.query.filter(HistoricoProducao.new_status == 'CANCELADO').count()

    return render_template(
        'main/_programacao_historico.html', itens=itens, busca=busca, mes=mes, tipo=tipo,
        meses_disponiveis=meses_disponiveis, finalizados_mes_count=finalizados_mes_count,
        cancelados_count=cancelados_count, pode_justificar=_eh_pcp(),
    )


@main_bp.route('/aba/programacao/historico/<int:hid>/justificar', methods=['POST'])
@login_required
def programacao_historico_justificar(hid):
    """Motivo do cancelamento de uma OP: so quem e do PCP."""
    if not _eh_pcp():
        abort(403)
    h = HistoricoProducao.query.filter_by(id=hid, new_status='CANCELADO').first_or_404()
    motivo = (request.form.get('motivo') or '').strip()
    if len(motivo) < 3:
        return jsonify(ok=False, erro='Escreva o motivo do cancelamento.'), 400
    h.motivo_cancelamento = motivo[:500]
    h.justificado_por = current_user.name
    h.justificado_em = datetime.utcnow()
    db.session.commit()
    return jsonify(ok=True, motivo=h.motivo_cancelamento, por=h.justificado_por, em=(h.justificado_em - timedelta(hours=3)).strftime('%d/%m/%Y %H:%M'))


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
            (h.changed_at - timedelta(hours=3)).strftime('%d/%m/%Y %H:%M') if h.changed_at else '',
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
               ['Artigo', 'Cliente', 'OP', 'Estrutura', 'Gramatura', 'Composição', 'DS.Metros', 'Qt Avulsa', 'Peças Produzidas', 'Total Pçs', 'PÇ.PRODUZIR', 'Qt Solicitada', 'Motivo'],
               [[r.artigo, r.cliente, r.op, resolve_estrutura_from_article(r.artigo),
                 getattr(dados_do_artigo(r.artigo), 'gramatura', None), getattr(dados_do_artigo(r.artigo), 'composicao', None), resolve_metros_from_article(r.artigo),
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


def _pode_dias_sem_producao() -> bool:
    """Quem marca os dias sem producao: administrador ou PCP (mesma regra do PCP Hub: PCP, admin e gestores)."""
    if current_user.is_admin:
        return True
    nomes = {x.name for x in (current_user.sectors or [])} | ({current_user.setor.name} if getattr(current_user, 'setor', None) else set())
    return 'PCP' in nomes


@main_bp.route('/aba/programacao/dias-sem-producao/dados')
@login_required
def programacao_dias_dados():
    """Dados do calendario Dias sem Producao: datas marcadas (global e por maquina) e feriados nacionais, SP e Jundiai (ano anterior, atual e seguinte)."""
    from app.dias_producao import feriados
    ano = date.today().year
    fer = {}
    for a in (ano - 1, ano, ano + 1):
        fer.update(feriados(a))
    marcados = [dict(id=d.id, data=d.date.isoformat(), maquina=d.machine.number if d.machine else None, motivo=d.comment or '') for d in NonWorkingDay.query.order_by(NonWorkingDay.date).all()]
    return jsonify(marcados=marcados, feriados=fer, pode_editar=_pode_dias_sem_producao())


@main_bp.route('/aba/programacao/dias-sem-producao/salvar', methods=['POST'])
@login_required
def programacao_dias_salvar():
    """Salva as datas marcadas para TODAS as maquinas (como no PCP Hub) e recalcula o Termino Malharia de todas as OPs. Domingos ja sao folga sempre."""
    if not _pode_dias_sem_producao():
        abort(403)
    d = request.get_json(silent=True) or {}
    alvo = set()
    for x in d.get('datas') or []:
        try:
            alvo.add(datetime.strptime(str(x)[:10], '%Y-%m-%d').date())
        except ValueError:
            continue
    atuais = {n.date: n for n in NonWorkingDay.query.filter(NonWorkingDay.machine_id.is_(None)).all()}
    for dia, n in atuais.items():
        if dia not in alvo:
            db.session.delete(n)
    for dia in alvo - set(atuais):
        db.session.add(NonWorkingDay(date=dia, machine_id=None, created_by_id=current_user.id))
    db.session.commit()
    from app import previsao
    try:
        n = previsao.calcular(esperar=True)
    except Exception:  # noqa: BLE001
        n = 0
    return jsonify(ok=True, marcados=len(alvo), recalculadas=n)


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
    if not _pode_dias_sem_producao():
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
    if not _pode_dias_sem_producao():
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
    if not _pode_dias_sem_producao():
        abort(403)

    from app import previsao
    try:
        previsao.calcular(esperar=True)                          # voltas/RPM e raport: domingos e datas marcadas empurram o termino
    except Exception:  # noqa: BLE001
        pass
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
            if s.termino_calculado:                  # a previsao por voltas/RPM ja considera os dias sem producao
                continue
            pulados = [d for d in dias_maquina if s.termino_malharia and d >= s.termino_malharia]
            if pulados:
                s.termino_malharia = s.termino_malharia + timedelta(days=len(pulados))
                atualizadas += 1
    db.session.commit()
    flash('programacao_recalculada_sucesso', 'success')
    return redirect(url_for('main.view_tab', slug='programacao'))


def _parse_dt_texto(txt):
    """'dd/mm[/aaaa]' vira data; qualquer outro texto (ate 10 caracteres) fica como descricao. Devolve (data, texto)."""
    m = re.fullmatch(r'(\d{1,2})/(\d{1,2})(?:/(\d{2}|\d{4}))?', txt)
    if not m:
        return None, txt
    ano = int(m.group(3)) if m.group(3) else date.today().year
    ano += 2000 if ano < 100 else 0
    return date(ano, int(m.group(2)), int(m.group(1))), None


def _cliente_pelo_artigo(artigo):
    """OP avulsa criada pela Conexao vem do TOTVS com o cliente 'CONEXAO MALHAS...': o cliente de verdade e quem pediu esse artigo (mesma ideia da Programacao).
    Casa pelo codigo do produto OU pelo inicio da descricao; prefere pedidos em aberto (maior saldo pendente), senao o pedido mais recente. ORTOBOM (qualquer unidade) vira 'ORTOBOM'."""
    cod = (artigo or '').split(' ')[0].strip()
    if not cod:
        return None
    pedidos = [p for p in PedidoRelatorio.query.filter(db.or_(PedidoRelatorio.cod_produto == cod, PedidoRelatorio.ds_produto.like(cod + ' %'), PedidoRelatorio.ds_produto == cod)).all()
               if p.nome_cliente and 'CONEXAO MALHAS' not in p.nome_cliente.upper() and (p.tp_situacao or '') != 'CANCELADO']
    if not pedidos:
        return None
    abertos = [p for p in pedidos if (p.tp_situacao or '') != 'ATENDIDO' and (p.qt_pendente or 0) > 0.5]
    if abertos:
        saldo = {}
        for p in abertos:
            nome = normalize_client_name(p.nome_cliente)
            saldo[nome] = saldo.get(nome, 0) + (p.qt_pendente or 0)
        return max(saldo, key=saldo.get)
    ultimo = max(pedidos, key=lambda p: (p.dt_pedido or date.min))
    return normalize_client_name(ultimo.nome_cliente)


def _fila_dt_prev(item):
    """DT.PREV.FAT do item da Pre-Programacao: o que o PCP digitou; senao a menor Prev. Faturamento do pedido ligado a OP; senao dos pedidos em aberto do mesmo cliente+artigo."""
    if item.dt_prevfaturamento:
        return item.dt_prevfaturamento
    d = resolve_dt_cliente_from_pedidos(item.op, item.artigo)
    if not d and item.artigo:
        cod = item.artigo.split()[0]
        cli = normalize_client_name(item.cliente or '')
        datas = [p.dt_prevfaturamento for p in PedidoRelatorio.query.filter(PedidoRelatorio.cod_produto == cod, PedidoRelatorio.qt_pendente > 0).all()
                 if p.dt_prevfaturamento and normalize_client_name(p.nome_cliente or '') == cli]
        d = min(datas) if datas else None
    return d.strftime('%d/%m/%Y') if d else None


@main_bp.route('/aba/pre-programacao/assinatura')
@login_required
def pre_programacao_assinatura():
    """Impressao digital da fila da Pre-Programacao: a tela confere a cada 20 s e recarrega sozinha quando entra/sai OP ou muda um dado."""
    itens = FilaPreProgramacao.query.filter_by(status='WAITING').order_by(FilaPreProgramacao.id).all()
    base = '|'.join(f'{i.id}:{i.artigo}:{i.cliente}:{i.total_pcs}:{i.dt_prevfaturamento}' for i in itens)
    pedidos = PedidoRelatorio.query.filter(PedidoRelatorio.nr_op.in_([i.op for i in itens if i.op])).count() if itens else 0
    return jsonify(sig=hashlib.md5(f'{base}#{pedidos}'.encode()).hexdigest(), n=len(itens))


@main_bp.route('/aba/pre-programacao/<int:item_id>/dt-prev', methods=['POST'])
@login_required
def pre_programacao_dt(item_id):
    item = FilaPreProgramacao.query.get_or_404(item_id)
    txt = re.sub(r'\s+', ' ', str((request.get_json(silent=True) or {}).get('texto') or '')).strip()
    if len(txt) > 10:
        return jsonify(erro='Use no máximo 10 caracteres.'), 400
    if txt:
        try:
            _parse_dt_texto(txt)
        except ValueError:
            return jsonify(erro='Data inválida.'), 400
    item.dt_prevfaturamento = txt or None
    db.session.commit()
    return jsonify(ok=True, exibe=txt or 'A DEFINIR')


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

    from app.auditoria import bloqueio
    aviso = bloqueio(item.artigo, machine.number)
    if aviso:
        flash(aviso, 'error')
        return redirect(url_for('main.view_tab', slug='pre-programacao', aba='analisar'))
    estrutura = resolve_estrutura_from_article(item.artigo)
    cliente = (_cliente_pelo_artigo(item.artigo) if 'CONEXAO MALHAS' in (item.cliente or '').upper() else None) or normalize_client_name(item.cliente)
    manual = (item.dt_prevfaturamento or '').strip()
    dt_man, txt_man = _parse_dt_texto(manual) if manual else (None, None)
    dt_cliente = dt_man or resolve_dt_cliente_from_pedidos(item.op, item.artigo)
    is_ortobom = cliente == 'ORTOBOM'

    proxima_ordem = db.session.query(db.func.coalesce(db.func.max(Sequencia.sequence_order), 0)).filter_by(
        machine_id=machine.id
    ).scalar() + 1

    db.session.add(Sequencia(
        machine_id=machine.id,
        sequence_order=proxima_ordem,
        artigo=item.artigo,
        cliente=cliente,
        op=item.op,
        status='PCP',
        total_pcs=item.total_pcs or 0,
        pecas_produzidas=0,
        qt_solicitada=item.requested_quantity or 0,
        qt_avulsa=0,
        cod_segmento=item.cod_segmento or resolve_cod_segmento_from_article(item.artigo),
        dt_cliente=dt_man if manual else (None if is_ortobom else dt_cliente),
        production_month=txt_man if manual else (item.production_month if is_ortobom else None),
        dt_cliente_manual=bool(manual),
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


_MOV_CACHE = {'hub': None, 'resp': None}


def _iso_utc(dt):
    return dt.isoformat() + 'Z' if dt else None            # dt_real e guardado em UTC sem fuso: sem o Z o navegador le como horario local (3 h de erro)


def _hub_movimentos_json():
    """Pesagens antigas (Relatorio 32 do PCP Hub): sao ~32 mil linhas que nao mudam, entao ficam em cache (JSON pronto)."""
    chave = (Report32Movement.query.count(), db.session.query(db.func.max(Report32Movement.id)).scalar())
    quente = _MOV_CACHE['hub']
    if quente and quente['chave'] == chave and time.time() - quente['t'] < 600:
        return quente
    manual, especial, report = _build_client_maps()
    cache = {}

    def cliente_de(art):
        if art not in cache:
            cache[art] = _resolve_client_fast(art, manual, especial, report)
        return cache[art]

    rows = Report32Movement.query.order_by(Report32Movement.dt_real.asc()).all()
    saida = [
        {
            'id': r.id, 'cod_maquina': r.cod_maquina, 'op': r.op, 'nr_item': r.nr_item,
            'cod_artigo': r.cod_artigo, 'qt_movimento': r.qt_movimento, 'qt_pesos': r.qt_pesos,
            'dt_real': _iso_utc(r.dt_real), 'metro_padrao': r.metro_padrao,
            'local_code': r.local_code, 'cliente': cliente_de(r.cod_artigo),
        }
        for r in rows
    ]
    quente = dict(chave=chave, t=time.time(), texto=json.dumps(saida, ensure_ascii=False, separators=(',', ':'))[1:-1],
                  vistos={(str(r['op']), str(r['nr_item'])) for r in saida}, cliente_de=cliente_de)
    _MOV_CACHE['hub'] = quente
    return quente


def _peso_peca(desc, metros, kg_baixa, gramatura=None, largura=None):
    """qt. pesos da peca como no Relatorio 32: metros x gramatura (g/m2) x largura (m) / 1000, lidas do nome do artigo (ex.: 240G 2,20L).
    A baixa de fio e so o fio consumido (parte do peso), entao ela entra apenas quando o nome do artigo nao traz gramatura e largura."""
    if gramatura and largura and metros:                           # campos do produto da OP no TOTVS (GRAMATURA M2 e LARGURA PADRAO)
        return round(metros * gramatura * largura / 1000, 2)
    g = re.search(r'(\d{2,3})\s*G\b', desc or '', re.I)
    l = re.search(r'(\d+[.,]\d+)\s*L\b', desc or '', re.I)
    if g and l and metros:
        return round(metros * int(g.group(1)) * float(l.group(1).replace(',', '.')) / 1000, 2)
    return round(kg_baixa or 0, 2)


def _api_baixas(hub):
    """Pecas do local 23 = baixas de fio do TOTVS (uma por peca pesada, com hora e kg exatos). Metros e artigo vem do movimento 23->24 ligado."""
    from app.models import BaixaFio
    saida = []
    for b in BaixaFio.query.order_by(BaixaFio.mudou).all():
        desc = b.artigo_desc or ''
        saida.append({
            'id': 2_000_000_000 + b.id, 'cod_maquina': str(b.maquina) if b.maquina is not None else None, 'op': b.op, 'nr_item': str(b.codigo),
            'cod_artigo': desc, 'qt_movimento': b.metros, 'qt_pesos': _peso_peca(desc, b.metros, b.kg, b.gramatura, b.largura),
            'dt_real': _iso_utc(b.mudou + timedelta(hours=3)),                       # mudou esta no horario do Brasil
            'metro_padrao': b.metro_padrao or b.metros or 0, 'local_code': '23',
            'cliente': hub['cliente_de'](desc) if desc else b.cliente_nome,
        })
    return json.dumps(saida, ensure_ascii=False, separators=(',', ':'))[1:-1]


def _api_movimentos(hub):
    """Pecas pesadas no local 23 (MALHARIA PESAGEM) direto da API do TOTVS, atualizadas a cada 10 s.
    A hora e a que o Gestao viu a pesagem (a API so tem a data); o peso e metros x gramatura x largura (confere com o Relatorio 32)."""
    from app.models import PesagemMalharia, PesagemResumo
    resumos = {r.op: r for r in PesagemResumo.query.all()}
    clientes_op = {}
    for sq in Sequencia.query.all():
        clientes_op.setdefault(sq.op, sq.cliente)
    saida = []
    pecas = [p for p in PesagemMalharia.query.order_by(PesagemMalharia.data, PesagemMalharia.item).all()
             if resumos.get(p.op) and p.metros is not None and (str(p.op), str(p.item)) not in hub['vistos']]
    unicas, chaves = [], set()
    for p in pecas:                                                 # nunca conta a mesma peca duas vezes (mesma OP, data, sequencia e metros)
        k = (p.op, p.data, p.sequencia, round(p.metros, 3))
        if k not in chaves:
            chaves.add(k)
            unicas.append(p)
    pecas = unicas
    # hora de cada peca: baixa de fio (trx) > momento em que o Gestao viu > estimada entre as vizinhas da mesma OP
    horas = {}
    for p in pecas:
        if p.hora_real and p.hora_origem == 'trx':
            horas[p.id] = p.hora_real + timedelta(hours=3)            # hora da baixa de fio (horario do Brasil, UTC-3)
        elif p.visto_em is not None:
            v = p.visto_em.astimezone(timezone.utc).replace(tzinfo=None)
            if abs((v - timedelta(hours=3)).date() - p.data) <= timedelta(days=1):    # visto na propria data (nao em carga historica)
                horas[p.id] = v
    por_op = {}
    for p in pecas:
        por_op.setdefault(p.op, []).append(p)
    for lista in por_op.values():
        lista.sort(key=lambda x: (x.data, x.item))
        for i, p in enumerate(lista):
            if p.id in horas:
                continue
            ant = next(((j, horas[lista[j].id]) for j in range(i - 1, -1, -1) if lista[j].id in horas), None)
            prox = next(((j, horas[lista[j].id]) for j in range(i + 1, len(lista)) if lista[j].id in horas), None)
            if ant and prox and prox[1] > ant[1]:
                horas[p.id] = ant[1] + (prox[1] - ant[1]) * (i - ant[0]) / (prox[0] - ant[0])
            elif ant:
                horas[p.id] = ant[1] + timedelta(hours=2.7) * (i - ant[0])
            elif prox:
                horas[p.id] = prox[1] - timedelta(hours=2.7) * (prox[0] - i)
            else:                                                       # sem nenhuma vizinha com hora: meio do dia da movimentacao
                horas[p.id] = datetime.combine(p.data, datetime.min.time()) + timedelta(hours=15)
    for p in pecas:
        r = resumos[p.op]
        dt_utc = horas[p.id]
        ini_dia = datetime.combine(p.data, datetime.min.time()) + timedelta(hours=9, minutes=20)     # dia de producao da data do movimento: 06:20 BRT ...
        dt_utc = min(max(dt_utc, ini_dia), ini_dia + timedelta(hours=23, minutes=59))                # ... ate 06:19 BRT do dia seguinte
        desc = r.artigo_desc or ''
        kg = round(p.peso_kg, 2) if p.peso_kg else 0             # kg da baixa de fio ligada a peca (API do TOTVS)
        saida.append({
            'id': 1_000_000_000 + p.id, 'cod_maquina': str(r.maquina) if r.maquina is not None else None, 'op': p.op, 'nr_item': str(p.item),
            'cod_artigo': desc, 'qt_movimento': p.metros, 'qt_pesos': _peso_peca(desc, p.metros, kg, r.gramatura, r.largura),      # metros x gramatura x largura (como o Relatorio 32); a baixa so entra se faltar gramatura/largura
            'dt_real': _iso_utc(dt_utc), 'metro_padrao': r.metro_padrao or p.metros,
            'local_code': '23', 'cliente': hub['cliente_de'](desc) or clientes_op.get(p.op),
        })
    return json.dumps(saida, ensure_ascii=False, separators=(',', ':'))[1:-1]


@main_bp.route('/api/malharia/movimentos')
@login_required
def api_malharia_movimentos():
    """Pesagens do local 23 (Relatorio 32 do PCP Hub ate o corte + API do TOTVS depois dele), ja com o cliente resolvido por artigo.
    Fonte unica da Gestao da Malharia, do Controle de Producao e do Dashboard Tempo Real. Como a tela relê a cada 15 s, a resposta
    vai com ETag (304 quando nada mudou) e compactada (gzip): sao ~7 MB de JSON."""
    from app import relatorio32_hub
    base = _hub_movimentos_json()
    if os.getenv('CONTROLE_FONTE', 'relatorio32') == 'relatorio32' and relatorio32_hub.em_dia():
        hub, api_txt = base, ''                                    # pecas = Relatorio 32 (ponte com o Hub, igual ao que o PCP Hub mostra); se a ponte parar, cai nas baixas de fio
        return _resposta_movimentos(hub, api_txt)
    hub = dict(base, texto='', vistos=set())
    from app.models import BaixaFio
    # Pecas = pesagens do local 23 lidas do TOTVS (uma por peca, igual ao Relatorio 32). As baixas de fio so cobriam parte das pecas (68 de ~104 no dia 29/10) e ficam como reserva/opcao: CONTROLE_FONTE=baixas
    from app.models import PesagemMalharia, PesagemResumo
    usa_baixas = os.getenv('CONTROLE_FONTE', 'baixas') != 'pesagens' and BaixaFio.query.first()
    chave_dados = (usa_baixas, db.session.query(db.func.count(PesagemMalharia.id), db.func.max(PesagemMalharia.synced_at), db.func.max(PesagemMalharia.hora_real)).one(),
                   db.session.query(db.func.count(PesagemResumo.id), db.func.max(PesagemResumo.atualizado_em)).one(), db.session.query(db.func.count(Sequencia.id), db.func.max(Sequencia.synced_at)).one())
    memo = _MOV_CACHE.get('api')
    if memo and memo['chave'] == chave_dados and time.time() - memo['t'] < 120:         # nada mudou no banco: reaproveita o calculo (antes refazia tudo a cada leitura de 15 s de cada tela)
        api_txt = memo['txt']
    else:
        api_txt = _api_baixas(hub) if usa_baixas else _api_movimentos(hub)
        _MOV_CACHE['api'] = dict(chave=chave_dados, txt=api_txt, t=time.time())
    return _resposta_movimentos(hub, api_txt)


def _resposta_movimentos(hub, api_txt):
    etag = hashlib.md5(f"{hub['chave']}|{api_txt}".encode()).hexdigest()
    if request.headers.get('If-None-Match', '').strip('"W/') == etag:
        r = Response(status=304)
    else:
        resp = _MOV_CACHE['resp']
        if not resp or resp['etag'] != etag:
            corpo = ('[' + hub['texto'] + (',' if hub['texto'] and api_txt else '') + api_txt + ']').encode('utf-8')
            resp = _MOV_CACHE['resp'] = dict(etag=etag, plano=corpo, gz=gzip.compress(corpo, 5))
        usar_gz = 'gzip' in request.headers.get('Accept-Encoding', '')
        r = Response(resp['gz'] if usar_gz else resp['plano'], mimetype='application/json')
        if usar_gz:
            r.headers['Content-Encoding'] = 'gzip'
        r.headers['Vary'] = 'Accept-Encoding'
    r.headers['ETag'] = f'"{etag}"'
    r.headers['Cache-Control'] = 'no-cache'
    return r


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
    from app.models import PesagemMalharia, PesagemResumo
    total = Report32Movement.query.count() + PesagemMalharia.query.filter(
        db.or_(PesagemMalharia.visto_em.isnot(None), PesagemMalharia.hora_origem == 'trx')).count()
    ultimo = max([x for x in (db.session.query(db.func.max(Report32Movement.synced_at)).scalar(),
                              db.session.query(db.func.max(PesagemResumo.atualizado_em)).scalar()) if x] or [None]) if True else None
    return jsonify({
        'last_sync': ultimo.isoformat() + 'Z' if ultimo else None,       # ultima busca no TOTVS (a cada 10 s)
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
    from app.auditoria import bloqueio
    aviso = bloqueio(artigo, machine.number)
    if aviso:                                           # Controle de Artigos: artigo que nao roda nesta maquina nao pode ser programado nela
        flash(aviso, 'error')
        return redirect(url_for('main.maquina_detalhe', numero=machine.number))

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


@main_bp.route('/aba/programacao/maquina/<int:numero>/historico-producao/dia')
@login_required
def maquina_historico_producao_dia(numero):
    """Peca a peca de um dia de producao (?dia=AAAA-MM-DD) da maquina: para conferir de onde vem cada contagem."""
    from app.models import BaixaFio
    tab = Tab.query.filter_by(slug='programacao').first_or_404()
    if not tab.visible_to(current_user):
        abort(403)
    try:
        dia = date.fromisoformat(request.args.get('dia', ''))
    except ValueError:
        return jsonify(ok=False, erro='Dia inválido.'), 400
    t0 = datetime.combine(dia, datetime.min.time()) + timedelta(hours=6, minutes=20)
    pecas = BaixaFio.query.filter(BaixaFio.maquina == numero, BaixaFio.mudou >= t0, BaixaFio.mudou < t0 + timedelta(days=1)).order_by(BaixaFio.mudou).all()
    return jsonify(ok=True, dia=dia.isoformat(), pecas=[dict(
        hora=b.mudou.strftime('%d/%m %H:%M:%S'), data_transacao=b.data.strftime('%d/%m') if b.data else None, codigo=b.codigo, op=b.op,
        artigo=b.artigo_desc, metros=round(float(b.metro_padrao or b.metros or 0), 2), kg=round(float(b.kg or 0), 2), casada=bool(b.casada)) for b in pecas])


@main_bp.route('/aba/programacao/maquina/<int:numero>/historico-producao')
@login_required
def maquina_historico_producao(numero):
    """Pecas, metros e kg que a maquina fez em cada dia de producao (06:20 -> 06:20), com a media por 24 h.
    Fonte: baixas de fio do TOTVS ligadas a maquina (a mesma do Controle de Producao). ?dias=30 ou ?de=AAAA-MM-DD&ate=AAAA-MM-DD."""
    from app.models import BaixaFio
    tab = Tab.query.filter_by(slug='programacao').first_or_404()
    if not tab.visible_to(current_user):
        abort(403)
    agora = datetime.now()                                    # o servidor roda no horario do Brasil
    dia_atual = (agora - timedelta(hours=6, minutes=20)).date()   # dia de producao em andamento
    try:
        if request.args.get('de') and request.args.get('ate'):
            de, ate = date.fromisoformat(request.args['de']), date.fromisoformat(request.args['ate'])
        else:
            dias = max(1, min(int(request.args.get('dias', 30)), 366))
            ate, de = dia_atual, dia_atual - timedelta(days=dias - 1)
    except ValueError:
        return jsonify(ok=False, erro='Período inválido.'), 400
    ate = min(ate, dia_atual)
    if de > ate or (ate - de).days > 366:
        return jsonify(ok=False, erro='Período inválido (máximo 1 ano).'), 400
    t0 = datetime.combine(de, datetime.min.time()) + timedelta(hours=6, minutes=20)
    t1 = datetime.combine(ate + timedelta(days=1), datetime.min.time()) + timedelta(hours=6, minutes=20)
    por_dia = {}
    for b in BaixaFio.query.filter(BaixaFio.maquina == numero, BaixaFio.mudou >= t0, BaixaFio.mudou < t1).order_by(BaixaFio.mudou).all():
        d = por_dia.setdefault((b.mudou - timedelta(hours=6, minutes=20)).date(), [0, 0.0, 0.0, {}])
        m_peca = float(b.metro_padrao or b.metros or 0)
        d[0] += 1
        d[1] += m_peca
        d[2] += float(b.kg or 0)
        a = d[3].setdefault((b.artigo_desc or '').strip() or (f'OP {b.op}' if b.op else 'Artigo não identificado'), dict(op=b.op, pecas=0, metros=0.0))
        a['pecas'] += 1
        a['metros'] += m_peca
    dias_lista, d = [], de
    while d <= ate:
        p, m, k, arts = por_dia.get(d, [0, 0.0, 0.0, {}])
        dias_lista.append(dict(dia=d.isoformat(), semana=['seg', 'ter', 'qua', 'qui', 'sex', 'sáb', 'dom'][d.weekday()], pecas=p,
                               metros=round(m, 1), kg=round(k, 1), parcial=(d == dia_atual),
                               artigos=[dict(artigo=n, op=v['op'], pecas=v['pecas'], metros=round(v['metros'], 1))
                                        for n, v in sorted(arts.items(), key=lambda kv: -kv[1]['pecas'])]))
        d += timedelta(days=1)
    fechados = [x for x in dias_lista if not x['parcial']]            # o dia em andamento nao entra nas medias
    produzindo = [x for x in fechados if x['pecas'] > 0]
    def media(lista, campo):
        return round(sum(x[campo] for x in lista) / len(lista), 1) if lista else 0
    resumo = dict(
        total_pecas=sum(x['pecas'] for x in dias_lista), total_metros=round(sum(x['metros'] for x in dias_lista), 1),
        total_kg=round(sum(x['kg'] for x in dias_lista), 1),
        dias_com_producao=len(produzindo), dias_periodo=len(fechados),
        media_pecas_24h=media(produzindo, 'pecas'), media_metros_24h=media(produzindo, 'metros'),
        media_pecas_periodo=media(fechados, 'pecas'),
        melhor=max(produzindo, key=lambda x: x['pecas'], default=None))
    return jsonify(ok=True, maquina=numero, de=de.isoformat(), ate=ate.isoformat(), dias=dias_lista[::-1], resumo=resumo)


ATRASO_MARGEM_DIAS = int(os.getenv('ATRASO_MARGEM_DIAS', '0'))        # folga entre o termino da malharia e a data do cliente (0 = atrasa quando o termino passa da dt.cliente)


def sequencia_atrasada(q, hoje=None) -> bool:
    """OP atrasada: termino previsto da malharia depois da data do cliente (menos a folga), ou data do cliente ja vencida com a OP ainda aberta."""
    if (q.status or '') in ('FINALIZADO', 'CANCELADO') or not q.dt_cliente:
        return False
    hoje = hoje or date.today()
    if q.termino_malharia and q.termino_malharia > q.dt_cliente - timedelta(days=ATRASO_MARGEM_DIAS):
        return True
    return q.dt_cliente < hoje


_ASSIN_CAMPOS = ('id', 'maquina_id', 'ordem', 'op', 'status', 'situacao', 'pecas', 'total_pcs', 'qt_solicitada', 'qt_avulsa', 'estrutura', 'termino', 'dt_cliente', 'artigo', 'cliente', 'aud_ok')
_ASSIN_ULT, _ASSIN_LOG = {}, []


def _assinatura_programacao(maquina=None) -> str:
    """Impressao digital do que aparece na Programacao (situacao, quantidades, ordem, termino...): a tela compara e se recarrega sozinha quando muda.
    Guarda no log (so em memoria) o que mudou entre uma leitura e outra: /aba/programacao/assinatura-log (admin) mostra quem fica oscilando."""
    import hashlib
    q = Sequencia.query.join(Machine).filter(Sequencia.status.notin_(['FINALIZADO', 'CANCELADO']))
    if maquina:
        q = q.filter(Machine.number == maquina)
    partes = [f'{x.id}|{x.machine_id}|{x.sequence_order}|{x.op}|{x.status}|{x.situacao}|{x.pecas_produzidas}|{x.total_pcs}|{x.qt_solicitada}|{x.qt_avulsa}|{x.estrutura}|{x.termino_malharia}|{x.dt_cliente}|{x.artigo}|{x.cliente}|{x.aud_ok}'
              for x in q.order_by(Sequencia.id).all()]
    antes = _ASSIN_ULT.get(maquina)
    _ASSIN_ULT[maquina] = partes
    if antes is not None and antes != partes:
        velho = {p.split('|')[0]: p.split('|') for p in antes}
        for p in partes:
            c = p.split('|')
            v = velho.get(c[0])
            if v != c:
                _ASSIN_LOG.append(dict(quando=datetime.now().strftime('%H:%M:%S'), maquina=maquina, seq=c[0], op=c[3], mudou=[f'{n}: {v[i] if v else "(nova)"} -> {c[i]}' for i, n in enumerate(_ASSIN_CAMPOS) if not v or v[i] != c[i]]))
        del _ASSIN_LOG[:-80]
    return hashlib.md5('\n'.join(partes).encode()).hexdigest()


@main_bp.route('/aba/programacao/assinatura-log')
@login_required
def programacao_assinatura_log():
    if not current_user.is_admin:
        abort(403)
    return jsonify(ultimas=list(reversed(_ASSIN_LOG)))


@main_bp.route('/aba/programacao/assinatura')
@login_required
def programacao_assinatura():
    tab = Tab.query.filter_by(slug='programacao').first_or_404()
    if not tab.visible_to(current_user):
        abort(403)
    return jsonify(sig=_assinatura_programacao(request.args.get('maquina', type=int)))


@main_bp.route('/aba/programacao/sequencia/<int:seq_id>/dt-cliente', methods=['POST'])
@login_required
def sequencia_dt_cliente(seq_id):
    """PCP digita a DT.CLIENTE (ORTOBOM e OP avulsa): ate 10 caracteres; uma data (dd/mm/aaaa ou dd/mm) vira data, qualquer outro texto fica como descricao. Vazio volta ao automatico."""
    tab = Tab.query.filter_by(slug='programacao').first_or_404()
    if not tab.visible_to(current_user) or not _pode_dias_sem_producao():
        abort(403)
    q = db.session.get(Sequencia, seq_id) or abort(404)
    avulsa = bool(q.qt_avulsa) or not PedidoRelatorio.query.filter(PedidoRelatorio.nr_op == str(q.op)).first()
    if normalize_client_name(q.cliente or '') != 'ORTOBOM' and not avulsa:
        return jsonify(erro='A DT.CLIENTE só pode ser editada em OP da ORTOBOM ou OP avulsa.'), 400
    txt = re.sub(r'\s+', ' ', str((request.get_json(silent=True) or {}).get('texto') or '')).strip()
    if len(txt) > 10:
        return jsonify(erro='Use no máximo 10 caracteres.'), 400
    if not txt:
        q.dt_cliente_manual, q.dt_cliente, q.production_month = False, None, None
    else:
        data = None
        m = re.fullmatch(r'(\d{1,2})/(\d{1,2})(?:/(\d{2}|\d{4}))?', txt)
        if m:
            ano = int(m.group(3)) if m.group(3) else date.today().year
            ano += 2000 if ano < 100 else 0
            try:
                data = date(ano, int(m.group(2)), int(m.group(1)))
            except ValueError:
                return jsonify(erro='Data inválida.'), 400
        q.dt_cliente, q.production_month, q.dt_cliente_manual = data, (None if data else txt), True
    db.session.commit()
    exibe = q.dt_cliente.strftime('%d/%m/%Y') if q.dt_cliente else (q.production_month.title() if q.production_month else 'A DEFINIR')
    return jsonify(ok=True, exibe=exibe, edita=(q.dt_cliente.strftime('%d/%m/%Y') if q.dt_cliente else (q.production_month or '')))


@main_bp.route('/aba/programacao/maquina/<int:numero>/reordenar', methods=['POST'])
@login_required
def maquina_reordenar(numero):
    """Arrastar OPs dentro da maquina: grava a nova ordem da fila e recalcula o termino da malharia (voltas/RPM ou raport) na hora."""
    tab = Tab.query.filter_by(slug='programacao').first_or_404()
    if not tab.visible_to(current_user):
        abort(403)
    from app.models import AuditoriaLog
    machine = Machine.query.filter_by(number=numero).first_or_404()
    d = request.get_json(silent=True) or {}
    ids = [int(x) for x in (d.get('ids') or []) if str(x).isdigit()]
    ativas = {q.id: q for q in Sequencia.query.filter_by(machine_id=machine.id).filter(Sequencia.status.notin_(['FINALIZADO', 'CANCELADO'])).all()}
    if sorted(ids) != sorted(ativas):
        return jsonify(erro='A fila mudou enquanto você arrastava; a página vai recarregar.'), 409
    antes = {q.id: q.sequence_order for q in ativas.values()}
    produzindo = [i for i in ids if ativas[i].status == 'PRODUZINDO']
    ids = produzindo + [i for i in ids if i not in produzindo]           # a OP produzindo fica sempre em 1o
    for pos, i in enumerate(ids, 1):
        ativas[i].sequence_order = pos
    movido = ativas.get(int(d.get('movido') or 0))
    if movido:
        db.session.add(AuditoriaLog(usuario=getattr(current_user, 'name', None) or 'Usuário', acao='MOVIDA', maquina=numero, artigo=movido.artigo, op=movido.op, dt_cliente=movido.dt_cliente,
                                    origem=f'{(antes.get(movido.id) or 0)}º', destino=f'{ids.index(movido.id) + 1}º'))
    db.session.commit()
    from app import previsao
    try:
        n = previsao.calcular(maquina=numero, esperar=True)
    except Exception:  # noqa: BLE001
        n = 0
    return jsonify(ok=True, recalculadas=n)


@main_bp.route('/aba/programacao/sharepoint-lista')
@login_required
def sharepoint_lista_diagnostico():
    """So administrador: mostra colunas e os 5 itens mais recentes de uma lista do SharePoint (ex.: ?lista=2026_liberacao_sku_BR_GT ou ?lista=controleMecanico); sem ?lista= lista todas."""
    if not current_user.is_admin:
        abort(403)
    from app.indicadores import sharepoint as sp
    from app import previsao
    site = previsao.FICHA_SITE_ID
    nome = (request.args.get('lista') or '').strip()
    try:
        listas = sp._get(f'{sp.GRAPH}/sites/{site}/lists', {'$top': 200})['value']
        if not nome:
            return jsonify(listas=[dict(id=x['id'], nome=x.get('name'), titulo=x.get('displayName')) for x in listas])
        alvo = next((x for x in listas if nome.lower() in (x['id'].lower(), (x.get('name') or '').lower(), (x.get('displayName') or '').lower())), None)
        if not alvo:
            return jsonify(erro='lista não encontrada', listas=[x.get('name') for x in listas]), 404
        cols = sp._get(f"{sp.GRAPH}/sites/{site}/lists/{alvo['id']}/columns", {'$top': 200})['value']
        itens = sp._get(f"{sp.GRAPH}/sites/{site}/lists/{alvo['id']}/items", {'$expand': 'fields', '$top': 5, '$orderby': 'lastModifiedDateTime desc'})['value']
        return jsonify(lista=alvo.get('displayName'), id=alvo['id'], colunas=[dict(nome=c.get('name'), titulo=c.get('displayName')) for c in cols if not c.get('readOnly') or c.get('name') in ('Title',)],
                       exemplos=[x.get('fields') for x in itens])
    except Exception as e:  # noqa: BLE001
        return jsonify(erro=f'{type(e).__name__}: {str(e)[:300]}'), 500


@main_bp.route('/aba/programacao/ficha-diagnostico')
@login_required
def ficha_diagnostico():
    """So administrador: confere a ficha (basedeficha do SharePoint) contra as OPs da fila: de onde vem o RPM e as voltas de cada uma."""
    if not current_user.is_admin:
        abort(403)
    from markupsafe import escape
    from app import previsao
    if request.args.get('atualizar'):
        previsao._ult_ficha[0] = 0.0
        previsao.verificar_ficha()
    idx = previsao._indices()
    linhas = []
    for q in Sequencia.query.join(Machine).filter(Sequencia.status.in_(['PRODUZINDO', 'EM_SEQUENCIA', 'PCP'])).order_by(Machine.number, Sequencia.sequence_order).all():
        art = previsao._codigo(q.artigo)
        f = idx[3].get(art.upper()) or idx[3].get(art.upper().split('-')[0])
        antiga = idx[2].get(art)
        linhas.append(f'<tr class="{"" if f else "bad"}"><td>{q.machine.number}</td><td>{escape(q.op or "")}</td><td>{escape(art)}</td><td>{escape(q.cliente or "")}</td>'
                      f'<td>{f.rpm if f else "—"}</td><td>{f.voltas if f else "—"}</td><td>{f.data.strftime("%d/%m/%Y") if f and f.data else ""}</td><td>{escape(f.status) if f else ""}</td>'
                      f'<td>{antiga.rpm if antiga else "—"}</td><td>{antiga.voltas if antiga else "—"}</td><td>{escape(q.prev_fonte or "")}</td><td>{q.prev_rpm or ""}</td><td>{q.prev_voltas or ""}</td></tr>')
    e = previsao.ESTADO_FICHA
    return ('<meta charset="utf-8"><style>body{font:13px Arial;margin:18px}td,th{border:1px solid #ccc;padding:3px 7px;text-align:left}table{border-collapse:collapse}tr.bad td{background:#fff1f0}</style>'
            f'<h2>Ficha técnica (basedeficha) × OPs da fila</h2><p>Artigos na ficha: <b>{len(set(f.artigo for f in idx[3].values()))}</b> ({e["registros"]} registros válidos lidos) · última leitura: {e["ultima"] or "ainda não"} · erro: {escape(e["erro"] or "nenhum")} · '
            '<a href="?atualizar=1">ler de novo agora</a></p><p>Linhas vermelhas = artigo sem ficha com RPM e voltas (a previsão usa o histórico da producaoDIARIA ou o Raport).</p>'
            '<table><tr><th>Máq.</th><th>OP</th><th>Artigo</th><th>Cliente</th><th>RPM (ficha)</th><th>Voltas/peça (ficha)</th><th>Data da ficha</th><th>Status</th><th>RPM (producaoDIARIA)</th><th>Voltas (producaoDIARIA)</th><th>Fonte usada</th><th>RPM usado</th><th>Voltas usadas</th></tr>'
            + ''.join(linhas) + '</table>')


@main_bp.route('/aba/programacao/auditoria-quantidades')
@login_required
def auditoria_quantidades():
    """Confere, maquina a maquina (da 2a sequencia em diante), a QT.SOLICITADA da Programacao contra os pedidos ligados a cada OP e aponta o que parece errado."""
    tab = Tab.query.filter_by(slug='programacao').first_or_404()
    if not tab.visible_to(current_user):
        abort(403)
    from markupsafe import escape
    filtro_maq = request.args.get('maquina', type=int)
    so_prob = request.args.get('problemas') == '1'
    seqs = (Sequencia.query.join(Machine).filter(Sequencia.status.notin_(['FINALIZADO', 'CANCELADO'])).order_by(Machine.number, Sequencia.sequence_order, Sequencia.id).all())
    peds = {}
    for r in PedidoRelatorio.query.filter(PedidoRelatorio.nr_op.isnot(None), PedidoRelatorio.nr_op != '').all():
        peds.setdefault(str(r.nr_op), {})[(r.cod_pedido, r.cod_produto)] = r
    por_maq = {}
    for q in seqs:
        por_maq.setdefault(q.machine.number, []).append(q)
    linhas, total_prob = [], 0
    for numero, lista in por_maq.items():
        if filtro_maq and numero != filtro_maq:
            continue
        ordens = [q.sequence_order for q in lista]
        for pos, q in enumerate(lista):
            if pos == 0:
                continue                                       # a 1a sequencia (produzindo) nao entra na conferencia
            rows = list(peds.get(str(q.op), {}).values())
            sol, pend = sum(r.qt_solicitada or 0 for r in rows), sum(r.qt_pendente or 0 for r in rows)
            base, qa, pcs = q.qt_solicitada or 0, q.qt_avulsa or 0, q.total_pcs or 0
            avisos = []
            if rows:
                if pend and base < pend * 0.95:
                    avisos.append(f'OP ({base:.0f}) menor que o pendente do pedido ({pend:.0f})')
                elif sol and base > sol * 1.15:
                    avisos.append(f'OP ({base:.0f}) maior que o pedido ({sol:.0f}) em {(base / sol - 1) * 100:.0f}%')
            else:
                if qa and qa == pcs and base > qa * 3:
                    avisos.append(f'QT.AVULSA ({qa}) parece estar em peças; a OP tem {base} m')
                elif qa and base and abs(qa - base) / base > 0.05:
                    avisos.append(f'QT.AVULSA ({qa}) diferente da QT.SOLICITADA ({base})')
                elif not qa:
                    avisos.append('OP sem pedido ligado e sem QT.AVULSA')
            if ordens.count(q.sequence_order) > 1:
                avisos.append(f'sequência {q.sequence_order} repetida na máquina')
            total_prob += 1 if avisos else 0
            if so_prob and not avisos:
                continue
            linhas.append(f'<tr class="{"bad" if avisos else ""}"><td>{numero}</td><td>{q.sequence_order}º</td><td>{escape(q.op or "")}</td><td>{escape((q.artigo or "")[:12])}</td><td>{escape(q.cliente or "")}</td><td>{escape(q.situacao or q.status or "")}</td>'
                          f'<td class="n">{base}</td><td class="n">{qa or ""}</td><td class="n">{pcs}</td><td>{escape(", ".join(sorted({r.cod_pedido for r in rows})) or "—")}</td><td class="n">{sol:.0f}</td><td class="n">{pend:.0f}</td><td>{escape("; ".join(avisos)) or "ok"}</td></tr>')
    return ('<meta charset="utf-8"><style>body{font:13px Arial;margin:18px}td,th{border:1px solid #ccc;padding:3px 7px;text-align:left}table{border-collapse:collapse}.n{text-align:right}tr.bad td{background:#fff1f0}</style>'
            f'<h2>Conferência da QT.SOLICITADA (da 2ª sequência em diante)</h2><p>{len(linhas)} linha(s) · {total_prob} com aviso · <a href="?problemas=1">só os avisos</a> · <a href="?">todas</a> · filtro por máquina: <code>?maquina=2</code></p>'
            '<table><tr><th>Máq.</th><th>Seq.</th><th>OP</th><th>Artigo</th><th>Cliente</th><th>Situação</th><th>QT.SOLICITADA (máquina)</th><th>QT.AVULSA</th><th>Peças</th><th>Pedido(s) ligado(s)</th><th>Pedido: solicitado</th><th>Pedido: pendente</th><th>Conferência</th></tr>'
            + ''.join(linhas) + '</table>'
            '<p>QT.SOLICITADA da máquina é a quantidade da OP no TOTVS (em metros, arredondada em peças inteiras), por isso costuma ficar alguns % acima do pedido. O aviso só aparece quando a OP é menor que o pendente do pedido, maior que o pedido em mais de 15%, ou quando a QT.AVULSA está incoerente.</p>')


@main_bp.route('/aba/programacao/maquina/<int:numero>')
@login_required
def maquina_detalhe(numero):
    tab = Tab.query.filter_by(slug='programacao').first_or_404()
    if not tab.visible_to(current_user):
        abort(403)
    from app.auditoria import MECANICOS, impedidos_por_sequencia, sessao_aberta
    machine = Machine.query.filter_by(number=numero).first_or_404()
    sequencias = (
        Sequencia.query.filter_by(machine_id=machine.id)
        .filter(Sequencia.status.notin_(['FINALIZADO', 'CANCELADO']))   # pesagem do cru concluida ou cancelada sai da fila (automatico pelo TOTVS)
        .order_by(Sequencia.sequence_order)
        .all()
    )
    rodando = any(s.status == 'PRODUZINDO' for s in sequencias)
    total_pcs_maquina = sum(s.total_pcs or 0 for s in sequencias)
    produzindo_count = sum(1 for s in sequencias if s.status == 'PRODUZINDO')
    artigos = {s.id: dados_do_artigo(s.artigo) for s in sequencias}   # gramatura/composicao lidas do TOTVS
    pedidos_op = {}                                                   # pedido(s) de cada OP (Relatorio Pedidos); sem pedido = OP avulsa (ex.: criada direto no PCPFM156)
    ops_da_maquina = [str(x.op) for x in sequencias if x.op]
    if ops_da_maquina:
        for r in PedidoRelatorio.query.filter(PedidoRelatorio.nr_op.in_(ops_da_maquina)).all():
            lista = pedidos_op.setdefault(str(r.nr_op), [])
            if r.cod_pedido and r.cod_pedido not in [x['pedido'] for x in lista]:
                lista.append(dict(pedido=r.cod_pedido, cliente=r.nome_cliente or '', solicitada=int(r.qt_solicitada or 0), pendente=int(r.qt_pendente or 0)))
    avulsa_cli = set()                                                # OP de cliente criada direto no PCPFM156: sem pedido ligado e sem nenhum pedido aberto do mesmo cliente+artigo que pudesse te-la gerado
    abertos = None
    for q in sequencias:
        if q.qt_avulsa or pedidos_op.get(str(q.op)) or not q.op:
            continue
        if abertos is None:
            abertos = [(normalize_client_name(r.nome_cliente or ''), (r.ds_produto or '').split(' ')[0]) for r in PedidoRelatorio.query.filter(_pedido_aberto(), PedidoRelatorio.qt_pendente > 0.5).all()]
        cod = (q.artigo or '').split(' ')[0]
        if (normalize_client_name(q.cliente or ''), cod) not in abertos:
            avulsa_cli.add(q.id)
    ds_metros = {s.id: resolve_metros_from_article(s.artigo) for s in sequencias}   # metragem padrao do artigo (METRO IDEAL LOTE do TOTVS)
    # entrega da maquina em 24 h pela conta voltas/RPM (ineficiencia incluida): da OP que esta produzindo; se ela ainda nao tem
    # voltas/RPM na base, usa a primeira OP da fila que tem (e o cartao avisa de qual OP veio)
    produzindo = next((s for s in sequencias if s.status == 'PRODUZINDO'), None)
    ref24 = produzindo if (produzindo and produzindo.pecas_dia) else next((s for s in sequencias if s.pecas_dia), None)
    pecas_24h = ref24.pecas_dia if ref24 else None
    metro_ref = None
    if ref24:
        try:
            metro_ref = float(str(resolve_metros_from_article(ref24.artigo) or '').replace(',', '.')) or None
        except ValueError:
            metro_ref = None
        if not metro_ref and ref24.total_pcs and ref24.qt_solicitada:
            metro_ref = ref24.qt_solicitada / ref24.total_pcs
    metros_24h = round(pecas_24h * metro_ref) if pecas_24h and metro_ref else None
    origem_24h = None
    if ref24:
        origem_24h = dict(op=ref24.op, artigo=ref24.artigo, voltas=int(ref24.prev_voltas or 0), rpm=ref24.prev_rpm, fonte=ref24.prev_fonte, raport=ref24.prev_raport,
                          da_producao=bool(produzindo and ref24.id == produzindo.id), sem_base_producao=bool(produzindo and not produzindo.pecas_dia),
                          op_produzindo=produzindo.op if produzindo else None)
    return render_template(
        'main/maquina_detalhe.html',
        pode_editar_dt=_pode_dias_sem_producao(), dt_editavel={q.id for q in sequencias if q.qt_avulsa or q.id in avulsa_cli or 'ORTOBOM' in normalize_client_name(q.cliente or '').upper()}, atrasadas={q.id for q in sequencias if sequencia_atrasada(q)}, aud_sessao=sessao_aberta(machine.number), impedidos=impedidos_por_sequencia(sequencias, machine.number), mecanicos=MECANICOS,
        artigos=artigos, ds_metros=ds_metros, pedidos_op=pedidos_op, avulsa_cli=avulsa_cli, pecas_24h=pecas_24h, metros_24h=metros_24h, origem_24h=origem_24h,
        tab=tab,
        machine=machine,
        sequencias=sequencias,
        rodando=rodando,
        total_pcs_maquina=total_pcs_maquina,
        produzindo_count=produzindo_count,
    )
