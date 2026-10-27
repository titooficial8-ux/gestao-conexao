"""Comercial MI: clientes que nao compram e artigos que nao rodam ha N meses, motivo informado pelo comercial e indicadores para a diretoria.

Clientes: pedidos de venda do mercado interno (TOTVS de 2026 em diante + planilha 2023-2025, os mesmos da Entrada de Pedidos).
Artigos: OPs lidas do TOTVS (app/totvs/rodagem.py). Motivos ficam em motivos_inativos."""
import csv
import io
import json
import threading
from datetime import date, datetime, timedelta

from flask import Blueprint, Response, abort, current_app, jsonify, request
from flask_login import current_user, login_required

from app import db
from app.models import ArtigoRodagem, MotivoInativo, Sequencia, Tab

cmi_bp = Blueprint('cmi', __name__, url_prefix='/comercial-mi')

MOTIVOS_CLIENTE = ['Preço', 'Prazo de Entrega', 'Qualidade', 'Desenvolvimento']
MOTIVOS_ARTIGO = ['Tirou de linha', 'Preço', 'Problema de qualidade', 'Desenvolvimento']
SEM_MOTIVO = 'Sem motivo informado'
ATIVAS = ('PRODUZINDO', 'EM_SEQUENCIA', 'PCP')
LIMITE_ANOS = 3                                     # so olha clientes/artigos que compraram/rodaram nos ultimos 3 anos


def _autorizar():
    tab = Tab.query.filter_by(slug='pedidos-comercial-mi').first_or_404()
    if not tab.visible_to(current_user):
        abort(403)


def _corte(meses: int):
    hoje = date.today()
    return hoje, hoje - timedelta(days=round(meses * 30.4)), hoje - timedelta(days=365 * LIMITE_ANOS)


def _registros_mi():
    """[(data ISO, valor, metros, cliente)] de pedidos do mercado interno."""
    from app.main.routes import normalize_client_name
    from app.totvs import entrada, planilha_pedidos
    dados = entrada._ler()
    if dados.get('versao') != entrada.VERSAO:
        dados = {}
    fantasia = dados.get('clientes', {})
    regs = []
    for d, valor, metros, local, cod, _ins, razao in (dados.get('pedidos') or {}).values():
        if local == 'MI' and int(d[:4]) in entrada.ANOS_TOTVS:
            regs.append((d, valor, metros, normalize_client_name(fantasia.get(str(cod)) or razao) or 'SEM CLIENTE'))
    try:
        plan = planilha_pedidos.ler()
    except Exception:  # noqa: BLE001
        plan = {}
    for d, valor, metros, local, cliente in plan.get('pedidos', []):
        if local == 'MI' and int(d[:4]) not in entrada.ANOS_TOTVS:
            regs.append((d, valor, metros, normalize_client_name(cliente) or 'SEM CLIENTE'))
    return regs


def _motivos(tipo: str) -> dict:
    return {m.chave: m for m in MotivoInativo.query.filter_by(tipo=tipo).all()}


def _motivo_dict(m):
    return dict(motivo=m.motivo if m else '', obs=(m.obs if m else '') or '', desenho=(m.desenho if m else '') or '',
                usuario=(m.usuario if m else '') or '', atualizado=m.atualizado_em.strftime('%d/%m/%Y') if m and m.atualizado_em else '')


def clientes_inativos(meses: int = 3) -> list:
    hoje, corte, limite = _corte(meses)
    por = {}
    for d, valor, metros, nome in _registros_mi():
        if nome == 'SEM CLIENTE':
            continue
        dd = date.fromisoformat(d)
        if dd <= hoje:
            por.setdefault(nome, []).append((dd, valor, metros))
    mot = _motivos('cliente')
    saida = []
    for nome, pedidos in por.items():
        ultima = max(p[0] for p in pedidos)
        if not (limite <= ultima < corte):
            continue
        janela = [p for p in pedidos if ultima - timedelta(days=365) < p[0] <= ultima]     # os 12 meses antes de parar de comprar
        valor_12m = sum(p[1] for p in janela)
        saida.append(dict(chave=nome.upper(), nome=nome, ultima=ultima.isoformat(), dias=(hoje - ultima).days, meses=round((hoje - ultima).days / 30.4, 1),
                          pedidos_12m=len(janela), valor_12m=round(valor_12m, 2), metros_12m=round(sum(p[2] for p in janela), 1),
                          valor_total=round(sum(p[1] for p in pedidos), 2), primeira=min(p[0] for p in pedidos).isoformat(),
                          **_motivo_dict(mot.get(nome.upper()))))
    saida.sort(key=lambda x: -x['valor_12m'])
    return saida


def artigos_parados(meses: int = 3) -> list:
    hoje, corte, limite = _corte(meses)
    ativos = {str(s.artigo or '').split()[0] for s in Sequencia.query.all() if (s.status or '') in ATIVAS and s.artigo}
    mot = _motivos('artigo')
    saida = []
    for a in ArtigoRodagem.query.all():
        try:
            ops = json.loads(a.ops_json or '[]')
        except ValueError:
            continue
        datas = [(date.fromisoformat(o[0]), o) for o in ops if o and o[0]]
        if not datas or a.cod_artigo in ativos:
            continue
        ultima = max(d for d, _ in datas)
        if not (limite <= ultima < corte):
            continue
        janela = [o for d, o in datas if ultima - timedelta(days=365) < d <= ultima]
        por_cliente = {}
        for o in janela:
            por_cliente[o[2]] = por_cliente.get(o[2], 0) + (o[1] or 0)
        ult_op = max(datas, key=lambda x: x[0])[1]
        saida.append(dict(chave=a.cod_artigo, cod=a.cod_artigo, nome=a.descricao or a.cod_artigo, ultima=ultima.isoformat(), dias=(hoje - ultima).days,
                          meses=round((hoje - ultima).days / 30.4, 1), ops_12m=len(janela), metros_12m=round(sum(o[1] or 0 for o in janela), 1),
                          ops_total=len(datas), ultima_op=ult_op[3], clientes=', '.join(c for c, _ in sorted(por_cliente.items(), key=lambda kv: -kv[1])[:2] if c),
                          **_motivo_dict(mot.get(a.cod_artigo))))
    saida.sort(key=lambda x: -x['metros_12m'])
    return saida


def _bases(meses: int) -> dict:
    """Denominadores dos indicadores: quantos clientes/artigos existem na base e quanto foi faturado/rodado nos ultimos 12 meses."""
    hoje, corte, limite = _corte(meses)
    um_ano = hoje - timedelta(days=365)
    ult, receita_12m = {}, 0.0
    for d, valor, _m, nome in _registros_mi():
        if nome == 'SEM CLIENTE':
            continue
        dd = date.fromisoformat(d)
        if dd > hoje:
            continue
        ult[nome] = max(ult.get(nome, dd), dd)
        if dd > um_ano:
            receita_12m += valor
    clientes_base = sum(1 for u in ult.values() if u >= limite)
    clientes_ativos = sum(1 for u in ult.values() if u >= corte)
    ativos_art = {str(s.artigo or '').split()[0] for s in Sequencia.query.all() if (s.status or '') in ATIVAS and s.artigo}
    art_base = art_ativos = 0
    metros_12m = 0.0
    for a in ArtigoRodagem.query.all():
        try:
            ops = json.loads(a.ops_json or '[]')
        except ValueError:
            continue
        datas = [date.fromisoformat(o[0]) for o in ops if o and o[0]]
        if not datas:
            continue
        ultima = max(datas)
        metros_12m += sum((o[1] or 0) for o in ops if o and o[0] and date.fromisoformat(o[0]) > um_ano)
        if ultima >= limite:
            art_base += 1
            if ultima >= corte or a.cod_artigo in ativos_art:
                art_ativos += 1
    return dict(receita_12m=round(receita_12m, 2), clientes_base=clientes_base, clientes_ativos=clientes_ativos,
                artigos_base=art_base, artigos_ativos=art_ativos, metros_rodados_12m=round(metros_12m, 1))


def _faixas(lista: list, meses: int, campo_valor: str) -> list:
    """Quanto tempo faz que pararam: de `meses` ate 6, 6-12, 12-24 e 24+ (recente = mais facil de recuperar)."""
    cortes = [c for c in (6, 12, 24) if c > meses]
    limites = [meses] + cortes + [10 ** 6]
    saida = []
    for i in range(len(limites) - 1):
        ini, fim = limites[i], limites[i + 1]
        itens = [x for x in lista if ini <= x['meses'] < fim]
        rotulo = f'{ini} a {fim} meses' if fim < 10 ** 6 else f'mais de {ini} meses'
        saida.append(dict(faixa=rotulo, qtd=len(itens), valor=round(sum(x[campo_valor] for x in itens), 2)))
    return saida


def indicadores(clientes: list, artigos: list, meses: int = 3, bases: dict = None) -> dict:
    bases = bases or {}

    def agrupar(lista, motivos, campo_valor):
        g = {m: dict(motivo=m, qtd=0, valor=0.0) for m in motivos + [SEM_MOTIVO]}
        for x in lista:
            k = x['motivo'] if x['motivo'] in g else SEM_MOTIVO
            g[k]['qtd'] += 1
            g[k]['valor'] += x[campo_valor]
        return [dict(v, valor=round(v['valor'], 2)) for v in g.values()]

    def sem_motivo(lista, campo_valor):
        return [dict(nome=x['nome'], valor=x[campo_valor], meses=x['meses']) for x in lista if not x['motivo']][:5]

    por_mes = {}
    for x in clientes:
        k = x['ultima'][:7]
        por_mes[k] = por_mes.get(k, 0) + 1
    inf_c = sum(1 for x in clientes if x['motivo'])
    inf_a = sum(1 for x in artigos if x['motivo'])
    risco = round(sum(x['valor_12m'] for x in clientes), 2)
    top3 = sum(x['valor_12m'] for x in clientes[:3])
    top10 = sum(x['valor_12m'] for x in clientes[:10])
    m_par = round(sum(x['metros_12m'] for x in artigos), 1)
    return dict(
        clientes=dict(total=len(clientes), base=bases.get('clientes_base', 0), ativos=bases.get('clientes_ativos', 0),
                      pct_base=round(len(clientes) / bases['clientes_base'] * 100) if bases.get('clientes_base') else 0,
                      valor_em_risco=risco, receita_12m=bases.get('receita_12m', 0),
                      pct_receita=round(risco / bases['receita_12m'] * 100) if bases.get('receita_12m') else 0,
                      pct_top3=round(top3 / risco * 100) if risco else 0, pct_top10=round(top10 / risco * 100) if risco else 0,
                      informados=inf_c, pct_informado=round(inf_c / len(clientes) * 100) if clientes else 0,
                      por_motivo=agrupar(clientes, MOTIVOS_CLIENTE, 'valor_12m'), faixas=_faixas(clientes, meses, 'valor_12m'),
                      top=[dict(nome=x['nome'], valor=x['valor_12m'], meses=x['meses'], motivo=x['motivo']) for x in clientes[:5]],
                      sem_motivo=sem_motivo(clientes, 'valor_12m'),
                      saida_por_mes=[dict(mes=k, qtd=v) for k, v in sorted(por_mes.items())]),
        artigos=dict(total=len(artigos), base=bases.get('artigos_base', 0), ativos=bases.get('artigos_ativos', 0),
                     pct_base=round(len(artigos) / bases['artigos_base'] * 100) if bases.get('artigos_base') else 0,
                     metros_12m=m_par, metros_rodados_12m=bases.get('metros_rodados_12m', 0),
                     pct_metros=round(m_par / (m_par + bases['metros_rodados_12m']) * 100) if bases.get('metros_rodados_12m') and m_par else 0,
                     informados=inf_a, pct_informado=round(inf_a / len(artigos) * 100) if artigos else 0,
                     por_motivo=agrupar(artigos, MOTIVOS_ARTIGO, 'metros_12m'), faixas=_faixas(artigos, meses, 'metros_12m'),
                     top=[dict(nome=x['nome'], valor=x['metros_12m'], meses=x['meses'], motivo=x['motivo']) for x in artigos[:5]],
                     sem_motivo=sem_motivo(artigos, 'metros_12m')))


def _meses():
    try:
        return max(1, min(int(request.args.get('meses', 3)), 24))
    except ValueError:
        return 3


@cmi_bp.route('/dados')
@login_required
def dados():
    _autorizar()
    meses = _meses()
    cl, ar = clientes_inativos(meses), artigos_parados(meses)
    from app.totvs import rodagem
    return jsonify(ok=True, meses=meses, clientes=cl, artigos=ar, indicadores=indicadores(cl, ar, meses, _bases(meses)),
                   motivos=dict(cliente=MOTIVOS_CLIENTE, artigo=MOTIVOS_ARTIGO),
                   fonte_artigos=dict(ultima=rodagem.ESTADO.get('ultima'), erro=rodagem.ESTADO.get('erro'), rodando=bool(rodagem.ESTADO.get('rodando')),
                                      total=ArtigoRodagem.query.count()))


@cmi_bp.route('/motivo', methods=['POST'])
@login_required
def salvar_motivo():
    _autorizar()
    j = request.get_json(silent=True) or {}
    tipo, chave = j.get('tipo'), str(j.get('chave') or '').strip()[:160]
    validos = MOTIVOS_CLIENTE if tipo == 'cliente' else MOTIVOS_ARTIGO if tipo == 'artigo' else None
    motivo = str(j.get('motivo') or '').strip()
    if validos is None or not chave or (motivo and motivo not in validos):
        return jsonify(ok=False, erro='Dados inválidos.'), 400
    m = MotivoInativo.query.filter_by(tipo=tipo, chave=chave).first()
    obs, desenho = str(j.get('obs') or '').strip()[:500], str(j.get('desenho') or '').strip()[:200]
    if not motivo and not obs and not desenho:                  # tudo vazio: remove o registro
        if m:
            db.session.delete(m)
            db.session.commit()
        return jsonify(ok=True)
    m = m or MotivoInativo(tipo=tipo, chave=chave)
    m.nome, m.motivo, m.obs, m.desenho = str(j.get('nome') or chave)[:200], motivo, obs, desenho
    m.usuario, m.atualizado_em = current_user.name, datetime.utcnow()
    db.session.add(m)
    db.session.commit()
    return jsonify(ok=True, usuario=m.usuario, atualizado=m.atualizado_em.strftime('%d/%m/%Y'))


@cmi_bp.route('/artigos/atualizar', methods=['POST'])
@login_required
def atualizar_artigos():
    _autorizar()
    from app.totvs import rodagem
    if not rodagem.configurado():
        return jsonify(ok=False, erro='TOTVS não configurado no .env.'), 400
    threading.Thread(target=rodagem.atualizar, args=(current_app._get_current_object(),), daemon=True, name='rodagem-agora').start()
    return jsonify(ok=True)


@cmi_bp.route('/exportar')
@login_required
def exportar():
    _autorizar()
    meses, tipo = _meses(), request.args.get('tipo', 'clientes')
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=';')
    if tipo == 'artigos':
        w.writerow(['Artigo', 'Descrição', 'Última OP', 'Meses parado', 'OPs (12 m antes)', 'Metros (12 m antes)', 'Clientes', 'Motivo', 'Desenho da amostra', 'Observação'])
        for x in artigos_parados(meses):
            w.writerow([x['cod'], x['nome'], x['ultima'], x['meses'], x['ops_12m'], x['metros_12m'], x['clientes'], x['motivo'], x['desenho'], x['obs']])
    else:
        w.writerow(['Cliente', 'Última compra', 'Meses sem comprar', 'Pedidos (12 m antes)', 'Valor (12 m antes) R$', 'Metros (12 m antes)', 'Motivo', 'Observação'])
        for x in clientes_inativos(meses):
            w.writerow([x['nome'], x['ultima'], x['meses'], x['pedidos_12m'], x['valor_12m'], x['metros_12m'], x['motivo'], x['obs']])
    return Response('﻿' + buf.getvalue(), mimetype='text/csv; charset=utf-8',
                    headers={'Content-Disposition': f'attachment; filename=comercial_mi_{tipo}_{date.today():%Y-%m-%d}.csv'})
