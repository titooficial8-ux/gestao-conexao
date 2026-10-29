"""Botao INFO da maquina e alerta 'Peca apresentando defeito' da Programacao.

Fontes (todas lidas, nada e gravado fora do Gestao):
- Etapas de liberacao do SKU (E1 mecanico, E2 revisao cru, E3 acabamento, E4 liberacao): lista SharePoint 2026_liberacao_sku_BR_GT.
- Peca com defeito / ocorrencias de qualidade: lista SharePoint ncform (a mesma do Reprova Cru).
- Acompanhamento: sequencia da maquina (TOTVS), pesagens do Relatorio 32 e historico de producao do Gestao.
No .env: as mesmas SHAREPOINT_TENANT_ID/CLIENT_ID/CLIENT_SECRET dos indicadores; opcionais LIBERACAO_SKU_LISTA, LIDER_MALHARIA_EMAILS.
"""
import os
import re
import threading
import time
from datetime import datetime, timedelta

from flask import Blueprint, abort, jsonify, request
from flask_login import current_user, login_required

from app import db
from app.models import Machine, NcEstado, PesagemMalharia, Sequencia, Tab

info_bp = Blueprint('info_maquina', __name__, url_prefix='/info')

LISTA_LIBERACAO = os.getenv('LIBERACAO_SKU_LISTA', 'dc941722-1fd0-453a-a9ce-3592cf6e8443')
ALERTA_HORAS = int(os.getenv('ALERTA_DEFEITO_HORAS', '12'))
_cache, _trava = {}, threading.Lock()
ETAPAS = (('E1', 'Mecânico (liberação na máquina)'), ('E2', 'Revisão do cru'), ('E3', 'Acabamento'), ('E4', 'Liberação final'))


def _cacheado(chave, ttl, fn):
    """Mantem o ultimo valor bom: se o SharePoint falhar, devolve o antigo (ou lista vazia) em vez de quebrar a tela."""
    agora = time.time()
    with _trava:
        v = _cache.get(chave)
        if v and agora - v[0] < ttl:
            return v[1]
    try:
        novo = fn()
    except Exception as e:  # noqa: BLE001
        with _trava:
            _cache[chave] = (agora - ttl + 15, v[1] if v else [], str(e)[:200])
        return v[1] if v else []
    with _trava:
        _cache[chave] = (agora, novo, None)
    return novo


def _erro(chave):
    v = _cache.get(chave)
    return v[2] if v and len(v) > 2 else None


def _maq2(v):
    v = re.sub(r'\D', '', str(v or ''))
    return v.zfill(2) if v else ''


def _utc_local(iso):
    try:
        d = datetime.fromisoformat(str(iso).replace('Z', '+00:00'))
        return (d.replace(tzinfo=None) - timedelta(hours=3)) if d.tzinfo else d
    except ValueError:
        return None


def _liberacoes() -> list:
    from app.indicadores import sharepoint as sp

    def ler():
        saida, url, params = [], f'{sp.GRAPH}/sites/{sp._site()}/lists/{LISTA_LIBERACAO}/items', {'$expand': 'fields', '$top': 999}
        while url:
            j = sp._get(url, params)
            for it in j.get('value') or []:
                saida.append(dict(it.get('fields') or {}, _criado=it.get('createdDateTime'), _alterado=it.get('lastModifiedDateTime')))
            url, params = j.get('@odata.nextLink'), None
        return saida
    return _cacheado('liberacoes', 60, ler)


def _ncs() -> list:
    from app.indicadores import sharepoint as sp
    from app.indicadores.servico import DEFEITOS
    desde = (datetime.utcnow() - timedelta(days=60)).strftime('%Y-%m-%dT00:00:00Z')

    def ler():
        saida = []
        for x in sp.itens_recentes(desde):
            cods = sorted('R' + k[-2:] for k, v in x.items() if re.fullmatch(r'_x0052_\d\d', k) and v)
            cont = re.findall(r'R\d\d', str(x.get('contagemR') or ''))
            cods = cods or cont
            saida.append(dict(
                id=str(x.get('id') or x.get('_id')), maquina=_maq2(x.get('Maquina')), artigo=str(x.get('field_7') or '').strip(), op=str(x.get('Ordemdeproducao') or '').strip(),
                peca=str(x.get('field_9') or ''), emitente=x.get('field_2') or '', origem=x.get('field_4') or '', setor=x.get('field_13') or '', turno=x.get('TURNO') or '',
                metros=float(x.get('Metros2') or 0), quilos=float(x.get('Quilos2') or 0), cliente=x.get('field_8') or '', obs=x.get('field_49') or '',
                defeitos=[f'{c} {DEFEITOS.get(c, "")}'.strip() for c in cods], criado=_utc_local(x.get('_criado') or x.get('Created'))))
        return saida
    return _cacheado('ncs', 20, ler)


def _hub_cientes() -> set:
    """NCs que o pessoal ja deu 'ciente' no PCP Hub (enquanto o Hub ainda e usado): contam como resolvidas aqui tambem. Falha = conjunto vazio."""
    def ler():
        from app import pcphub
        if not pcphub.configurado():
            return set()
        import requests
        token = pcphub._login()
        r = requests.get(f'{pcphub.URL}/rest/v1/nc_alertas', params=dict(select='nc_id', ciente_em='not.is.null', order='criado_em.desc', limit='1500'),
                         headers=dict(apikey=pcphub.CHAVE, Authorization=f'Bearer {token}'), timeout=30)
        r.raise_for_status()
        return {str(x['nc_id']) for x in r.json()}
    return _cacheado('hub_cientes', 30, ler) or set()


def resolvidas() -> set:
    return {e.nc_id for e in NcEstado.query.filter_by(estado='LIBERADO').all()} | set(_hub_cientes())


def pode_liberar(u=None) -> bool:
    """Lider da malharia (ou administrador): usuario do setor Malharia com cargo de lider, ou e-mail em LIDER_MALHARIA_EMAILS."""
    u = u or current_user
    if not u.is_authenticated:
        return False
    if u.is_admin or (u.email or '').lower() in {e.strip().lower() for e in os.getenv('LIDER_MALHARIA_EMAILS', '').split(',') if e.strip()}:
        return True
    setor = (u.setor.name if u.setor else '').lower()
    cargo = (u.role.name if u.role else '').lower()
    return 'malharia' in setor and ('lider' in cargo or 'líder' in cargo)


def _tab_ok():
    tab = Tab.query.filter_by(slug='programacao').first_or_404()
    if not tab.visible_to(current_user):
        abort(403)


# ------------------------------------------------------------------ alerta: peca apresentando defeito
@info_bp.route('/alertas')
@login_required
def alertas():
    _tab_ok()
    limite = datetime.utcnow() - timedelta(hours=ALERTA_HORAS + 3)
    estados = {e.nc_id: e for e in NcEstado.query.all()}
    resolv = resolvidas()
    itens = []
    for n in _ncs():
        if not n['defeitos'] or (n['setor'] and 'produ' not in n['setor'].lower()) or not n['criado'] or n['criado'] + timedelta(hours=3) < limite:
            continue
        e = estados.get(n['id'])
        if n['id'] in resolv:
            continue
        itens.append(dict(n, origem=_origem(n['origem']), criado=n['criado'].strftime('%d/%m %H:%M'), estado=e.estado if e else 'AGUARDANDO', por=e.por_nome if e else None, em=(e.em - timedelta(hours=3)).strftime('%d/%m, %H:%M') if e and e.em else None,
                          ordem=n['criado'].isoformat()))
    itens.sort(key=lambda x: x['ordem'], reverse=True)
    return jsonify(itens=itens, pode=pode_liberar(), erro=_erro('ncs'))


@info_bp.route('/alertas/<nc_id>/estado', methods=['POST'])
@login_required
def alerta_estado(nc_id):
    _tab_ok()
    if not pode_liberar():
        return jsonify(erro='Somente o líder da malharia pode marcar ANALISANDO ou LIBERADO.'), 403
    est = str((request.get_json(silent=True) or {}).get('estado') or '').upper()
    if est not in ('ANALISANDO', 'LIBERADO'):
        return jsonify(erro='Estado inválido.'), 400
    e = NcEstado.query.filter_by(nc_id=str(nc_id)).first() or NcEstado(nc_id=str(nc_id))
    e.estado, e.por_nome, e.em = est, current_user.name, datetime.utcnow()
    db.session.add(e)
    db.session.commit()
    return jsonify(ok=True)


# ------------------------------------------------------------------ INFO da maquina / OP
def _turno_idx(dt):
    m = dt.hour * 60 + dt.minute
    return 0 if 380 <= m < 860 else (1 if 860 <= m < 1340 else 2)


def _acompanhamento(seq, maq):
    hoje = datetime.now()
    ini = hoje.replace(hour=6, minute=20, second=0, microsecond=0)
    if hoje < ini:
        ini -= timedelta(days=1)
    pes, ontem = [0, 0, 0], [0, 0, 0]
    ini_ont = ini - timedelta(days=1)
    for r in PesagemMalharia.query.filter_by(op=str(seq.op)).all():          # pecas pesadas (local 23) desta OP, com a hora real da baixa de fio
        h = r.hora_real or r.visto_em
        if not h or h < ini_ont or h >= ini + timedelta(days=1):
            continue
        (pes if h >= ini else ontem)[_turno_idx(h)] += 1
    meta_dia = seq.pecas_dia or 0
    return dict(op=seq.op, artigo=seq.artigo, cliente=seq.cliente, status=seq.status, situacao=seq.situacao, produzidas=seq.pecas_produzidas or 0, total=seq.total_pcs or 0,
                meta_dia=round(meta_dia, 1) if meta_dia else None, meta_turno=round(meta_dia / 3, 1) if meta_dia else None, pesadas_turno=pes, pesadas_ontem=ontem, inicio_ontem=ini_ont.strftime('%d/%m %H:%M'), inicio_dia=ini.strftime('%d/%m %H:%M'),
                termino=seq.prev_termino_em.strftime('%d/%m %H:%M') if seq.prev_termino_em else (seq.termino_malharia.strftime('%d/%m/%Y') if seq.termino_malharia else None),
                rpm=seq.prev_rpm, estrutura=seq.estrutura)


def _etapas(artigo, op, maquina):
    """Linha mais recente da liberacao do SKU para artigo + maquina (prefere a da mesma OP)."""
    cod = (artigo or '').split(' ')[0]
    cand = [x for x in _liberacoes() if str(x.get('field_2') or '').strip() == cod and _maq2(x.get('field_3')) == maquina]
    if not cand:
        return None, []
    cand.sort(key=lambda x: (str(x.get('field_4')) == str(op), x.get('_alterado') or ''), reverse=True)
    x = cand[0]
    g = lambda k: x.get(k) if x.get(k) not in (None, '') else None
    etapas = [
        dict(cod='E1', titulo=ETAPAS[0][1], feito=True, itens=[('Status gm', g('field_20')), ('Status largura', g('field_21')), ('Status volta', g('field_22')), ('Usuário', g('field_25')), ('Gm cru', g('field_12')), ('Largura crua', g('field_13')),
                                                         ('Tamanho da volta', g('field_14')), ('Observação', g('field_24'))]),
        dict(cod='E2', titulo=ETAPAS[1][1], feito=bool(g('field_37') or g('field_26')), itens=[('Revisão', g('field_37')), ('Responsável', g('field_26')), ('Status gm', g('field_35')), ('Status largura', g('field_36')), ('Gm cru', g('field_29')),
                                                                                           ('Largura crua', g('field_28')), ('Voltas sugeridas', g('field_34')), ('Observação', g('field_38'))]),
        dict(cod='E3', titulo=ETAPAS[2][1], feito=bool(g('field_53') or g('field_40')), itens=[('Acabamento', g('field_53')), ('Responsável', g('field_40')), ('Status gm', g('field_49')), ('Status largura', g('field_50')), ('Visual', g('field_51')),
                                                                                           ('Motivo reprovação', g('field_52')), ('Observação', g('field_54'))]),
        dict(cod='E4', titulo=ETAPAS[3][1], feito=bool(g('field_57')), itens=[('Liberação', g('field_57')), ('Usuário', g('field_59')), ('Observação', g('field_58'))]),
    ]
    atual = str(g('field_7') or 'E1')
    for e in etapas:
        e['itens'] = [dict(k=k, v=v) for k, v in e['itens'] if v is not None]
        e['atual'] = e['cod'] == atual
    resumo = dict(status=g('field_5'), etapa=atual, op=g('field_4'), atualizado=(_utc_local(x.get('_alterado')) or datetime.now()).strftime('%d/%m %H:%M'),
                  regulagem={k: g(c) for k, c in (('Ponto disco', 'field_61'), ('Ponto cilindro', 'field_62'), ('Puxador', 'field_63'), ('Frontura', 'field_64'), ('Altura disco', 'field_65'), ('Fita trama', 'field_66'), ('Tensão fios', 'field_67'),
                                                  ('Largura alargador', 'field_68'), ('Voltas carrinho enchimento', 'field_69'), ('RPM', 'field_70')) if g(c) is not None})
    return resumo, etapas


def _origem(o):
    """Auditoria Interna = a peca reprovou no cru."""
    return 'Auditoria Interna (Reprova Cru)' if 'auditoria interna' in str(o or '').lower() else (o or '')


def _ocorrencias(artigo, maquina, num):
    """Ultimas ocorrencias SO deste artigo nesta maquina e SO as apontadas na revisao do cru (NC da lista ncform)."""
    from app.models import PedidoRelatorio
    cod = (artigo or '').split(' ')[0]
    resolv = resolvidas()
    recente = datetime.now() - timedelta(hours=72)
    lista = [n for n in _ncs() if n['artigo'] == cod and n['maquina'] == maquina and n['criado']]
    com_pedido = {str(p.nr_op) for p in PedidoRelatorio.query.filter(PedidoRelatorio.nr_op.in_([n['op'] for n in lista if n['op']])).all()} if lista else set()
    lista.sort(key=lambda n: n['criado'], reverse=True)
    return [dict(quando=n['criado'].strftime('%d/%m %H:%M'), tipo='NC', resolvida=n['id'] in resolv, aberta=(n['id'] not in resolv and n['criado'] >= recente), artigo=n['artigo'], op=n['op'] or '–',
                 cliente=n['cliente'] or '–', avulsa=bool(n['op']) and n['op'] not in com_pedido, peca=n['peca'], defeitos=', '.join(n['defeitos']), metros=n['metros'], origem=_origem(n['origem']),
                 emitente=n['emitente'], obs=n['obs']) for n in lista[:40]]


@info_bp.route('/maquina/<int:numero>/alertas')
@login_required
def info_alertas_maquina(numero):
    """Sequencias da maquina com ocorrencia de defeito/reprova (NC da revisao do cru ainda nao resolvida): pisca quando e da mesma OP; fica vermelho fixo quando e do mesmo artigo em outra OP da maquina (72 h). Liberacao nao faz piscar."""
    _tab_ok()
    maq = Machine.query.filter_by(number=numero).first_or_404()
    m2 = f'{numero:02d}'
    recente = datetime.now() - timedelta(hours=72)
    resolv = resolvidas()
    ncs = [n for n in _ncs() if n['maquina'] == m2 and n['criado'] and n['id'] not in resolv]      # NC ja resolvida (liberada aqui ou 'ciente' no Hub) nao conta mais
    ids, marca = [], []
    for sq in maq.sequencias:
        if sq.status in ('FINALIZADO', 'CANCELADO'):
            continue
        cod = (sq.artigo or '').split(' ')[0]
        if any(n['artigo'] == cod and n['op'] == str(sq.op) for n in ncs):            # NC desta mesma OP: i piscando
            ids.append(sq.id)
        elif any(n['artigo'] == cod and n['criado'] >= recente for n in ncs):          # mesmo artigo reprovado em outra OP nesta maquina: i vermelho fixo (so identifica)
            marca.append(sq.id)
    return jsonify(ids=ids, marca=marca)


@info_bp.route('/maquina/<int:numero>')
@login_required
def info_maquina(numero):
    """?seq=<id> escolhe uma sequencia; sem ele, a que esta PRODUZINDO (ou a primeira da fila)."""
    _tab_ok()
    maq = Machine.query.filter_by(number=numero).first_or_404()
    seqs = [s for s in maq.sequencias if s.status not in ('FINALIZADO', 'CANCELADO')]
    sid = request.args.get('seq', type=int)
    seq = next((s for s in seqs if s.id == sid), None) or next((s for s in seqs if s.status == 'PRODUZINDO'), None) or (seqs[0] if seqs else None)
    if not seq:
        return jsonify(maquina=numero, vazio=True)
    m2 = f'{numero:02d}'
    resumo, etapas = _etapas(seq.artigo, seq.op, m2)
    return jsonify(maquina=numero, acompanhamento=_acompanhamento(seq, maq), liberacao=resumo, etapas=etapas, ocorrencias=_ocorrencias(seq.artigo, m2, numero), erro_sharepoint=_erro('liberacoes') or _erro('ncs'))


# ------------------------------------------------------------------ aumento de quantidade da OP (PCPFP084)
def _ajuste_json(a):
    return dict(id=a.id, op=a.op, maquina=a.maquina, artigo=a.artigo, qt_antes=round(a.qt_antes or 0, 1), qt_depois=round(a.qt_depois or 0, 1), aumento=round((a.qt_depois or 0) - (a.qt_antes or 0), 1),
                pecas_antes=a.pecas_antes, pecas_depois=a.pecas_depois, quando=(a.detectado_em - timedelta(hours=3)).strftime('%d/%m %H:%M') if a.detectado_em else '',
                justificativa=a.justificativa, por=a.justificado_por)


@info_bp.route('/ajustes/<int:numero>')
@login_required
def ajustes_maquina(numero):
    """Sequencias da maquina com aumento de quantidade ainda sem justificativa (o total de pecas pisca)."""
    _tab_ok()
    from app.models import AjusteQuantidade
    seqs = {s.id for s in Machine.query.filter_by(number=numero).first_or_404().sequencias}
    return jsonify(itens=[dict(id=a.id, seq=a.sequencia_id) for a in AjusteQuantidade.query.filter(AjusteQuantidade.justificado_em.is_(None)).all() if a.sequencia_id in seqs])


@info_bp.route('/ajuste/<int:ajuste_id>')
@login_required
def ajuste_detalhe(ajuste_id):
    _tab_ok()
    from app.models import AjusteQuantidade
    a = db.session.get(AjusteQuantidade, ajuste_id) or abort(404)
    emp, aviso = [], None
    try:
        from app.totvs import api, empenho
        cli = api.TotvsModaClient()
        cli.autenticar()
        log = []
        mats = empenho.buscar_materiais_op(cli, [int(a.op)], log).get(str(a.op)) or {}
        emp = [dict(codigo=c, nome=v[0], qtd=v[1]) for c, v in sorted(mats.items(), key=lambda kv: -kv[1][1])]
        if not emp:
            aviso = 'O TOTVS não devolveu materiais empenhados para esta OP' + (f' ({log[0].get("erro") or log[0].get("http")})' if log else '') + '.'
    except Exception as e:  # noqa: BLE001
        aviso = f'Não foi possível consultar o empenho no TOTVS agora ({str(e)[:120]}).'
    return jsonify(ajuste=_ajuste_json(a), empenho=emp, total_kg=round(sum(x['qtd'] for x in emp), 2), aviso=aviso)


@info_bp.route('/ajuste/<int:ajuste_id>/confirmar', methods=['POST'])
@login_required
def ajuste_confirmar(ajuste_id):
    _tab_ok()
    from app.main.routes import _pode_dias_sem_producao
    from app.models import AjusteQuantidade
    if not _pode_dias_sem_producao():
        return jsonify(erro='Somente o PCP pode justificar o aumento de quantidade.'), 403
    a = db.session.get(AjusteQuantidade, ajuste_id) or abort(404)
    motivo = re.sub(r'\s+', ' ', str((request.get_json(silent=True) or {}).get('motivo') or '')).strip()
    if len(motivo) < 5:
        return jsonify(erro='Justifique o motivo (mínimo 5 caracteres).'), 400
    a.justificativa, a.justificado_por, a.justificado_em = motivo[:500], current_user.name, datetime.utcnow()
    db.session.commit()
    return jsonify(ok=True)


# ------------------------------------------------------------------ ocorrencias do Acompanhamento de Producao (revisor em parada, paradas de maquina...)
@info_bp.route('/paradas')
@login_required
def paradas():
    _tab_ok()
    from app import acompanhamento
    if not acompanhamento.configurado():
        return jsonify(itens=[], erro='Token da API do Acompanhamento não configurado (ACOMP_TOKEN ou SETUPS_TOKEN no .env).')
    return jsonify(**acompanhamento.abertas())


@info_bp.route('/acompanhamento-diagnostico')
@login_required
def acompanhamento_diagnostico():
    """So administrador: lista as rotas da API do Acompanhamento e um exemplo das ocorrencias em andamento."""
    if not current_user.is_admin:
        abort(403)
    from app import acompanhamento
    out = dict(url=acompanhamento._url(), token_configurado=acompanhamento.configurado())
    for nome, caminho, params in (('rotas', '', None), ('ocorrencias_andamento', '/ocorrencias', {'status': 'andamento'})):
        try:
            j = acompanhamento.chamar(caminho, params)
            out[nome] = j if nome == 'rotas' else acompanhamento._lista(j)[:8]
        except Exception as e:  # noqa: BLE001
            out[nome] = f'erro: {e}'
    return jsonify(out)
