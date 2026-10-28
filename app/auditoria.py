"""Auditoria semanal das maquinas (reuniao com o mecanico) e Controle de Artigos (artigos que nao rodam em determinadas maquinas, do PCP Hub).

Auditoria: o PCP clica em "Iniciar Auditoria" na maquina, escolhe o mecanico e as engrenagens das OPs ficam clicaveis
(vermelho = ainda nao verificado, verde = verificado na maquina pelo mecanico e PCP). Tudo vira log; OPs que mudam de maquina
durante a auditoria entram no log com origem e destino (detectado na leitura do PCP Hub).
Controle de Artigos: espelho somente leitura de artigo_maquina_impedimentos do PCP Hub (la o sistema trava a programacao do artigo na maquina)."""
import re
import uuid
from datetime import datetime, timedelta, timezone

from flask import Blueprint, abort, jsonify, render_template, request
from flask_login import current_user, login_required

from app import db
from app.models import ArtigoImpedimento, AuditoriaLog, AuditoriaSessao, Category, Sector, Sequencia, Tab

aud_bp = Blueprint('auditoria', __name__, url_prefix='/auditoria')
MECANICOS = ('Davi', 'Rubens', 'Frederico', 'Luiz')
SETORES = ('Diretoria', 'PCP', 'Produção', 'Comercial')
MOTIVOS = {'MALHA': 'Malha', 'PROBLEMA_TECNICO': 'Problema técnico', 'OUTRO': 'Outro'}


def _usuario() -> str:
    return getattr(current_user, 'name', None) or getattr(current_user, 'email', None) or getattr(current_user, 'username', None) or 'Usuário'


def _autorizar():
    tab = Tab.query.filter_by(slug='programacao').first_or_404()
    if not tab.visible_to(current_user):
        abort(403)


def sessao_aberta(maquina: int):
    return AuditoriaSessao.query.filter_by(maquina=maquina, encerrado_em=None).order_by(AuditoriaSessao.id.desc()).first()


def _log(sessao, acao, maquina, s=None, **extra):
    db.session.add(AuditoriaLog(sessao_id=sessao.id if sessao else None, usuario=_usuario(), mecanico=sessao.mecanico if sessao else None, acao=acao, maquina=maquina,
                                artigo=s.artigo if s else None, op=s.op if s else None, dt_cliente=s.dt_cliente if s else None, **extra))


def _dados(a):
    return dict(id=a.id, quando=(a.criado_em.replace(tzinfo=timezone.utc).astimezone(timezone(timedelta(hours=-3)))).strftime('%d/%m/%Y %H:%M'), usuario=a.usuario or '', mecanico=a.mecanico or '',
                acao=a.acao, maquina=a.maquina, artigo=a.artigo or '', op=a.op or '', dt_cliente=a.dt_cliente.strftime('%d/%m/%Y') if a.dt_cliente else '',
                origem=a.origem or '', destino=a.destino or '')


@aud_bp.route('/iniciar', methods=['POST'])
@login_required
def iniciar():
    _autorizar()
    d = request.get_json(silent=True) or {}
    try:
        maquina = int(d.get('maquina'))
    except (TypeError, ValueError):
        abort(400)
    mecanico = re.sub(r'\s+', ' ', str(d.get('mecanico') or '')).strip()[:80]
    if not mecanico:
        return jsonify(erro='Informe o mecânico responsável.'), 400
    anterior = sessao_aberta(maquina)
    if anterior:
        anterior.encerrado_em = datetime.utcnow()
    sessao = AuditoriaSessao(maquina=maquina, mecanico=mecanico, iniciado_por=_usuario())
    db.session.add(sessao)
    db.session.flush()
    # as engrenagens ja verificadas continuam verdes: iniciar a auditoria NAO zera nada
    _log(sessao, 'INICIO', maquina)
    db.session.commit()
    return jsonify(ok=True, sessao=sessao.id)


@aud_bp.route('/encerrar', methods=['POST'])
@login_required
def encerrar():
    _autorizar()
    d = request.get_json(silent=True) or {}
    try:
        maquina = int(d.get('maquina'))
    except (TypeError, ValueError):
        abort(400)
    sessao = sessao_aberta(maquina)
    if sessao:
        sessao.encerrado_em = datetime.utcnow()
        _log(sessao, 'FIM', maquina)
        db.session.commit()
    return jsonify(ok=True)


@aud_bp.route('/verificar', methods=['POST'])
@login_required
def verificar():
    _autorizar()
    d = request.get_json(silent=True) or {}
    s = db.session.get(Sequencia, int(d.get('sequencia') or 0))
    if not s:
        abort(404)
    maquina = s.machine.number
    sessao = sessao_aberta(maquina)
    if not sessao:
        return jsonify(erro='Inicie a auditoria desta máquina para marcar as engrenagens.'), 409
    s.aud_ok = not s.aud_ok
    s.aud_em = datetime.utcnow() if s.aud_ok else None
    _log(sessao, 'VERIFICADO' if s.aud_ok else 'DESMARCADO', maquina, s, origem=str(maquina), destino=str(maquina))
    db.session.commit()
    return jsonify(ok=True, verificado=bool(s.aud_ok))


@aud_bp.route('/log')
@login_required
def log():
    _autorizar()
    q = AuditoriaLog.query.order_by(AuditoriaLog.id.desc())
    if request.args.get('maquina', '').isdigit():
        q = q.filter_by(maquina=int(request.args['maquina']))
    return jsonify(itens=[_dados(a) for a in q.limit(500).all()])


def registrar_movimentacoes(removidas: dict, novas: dict, maquinas_por_id: dict) -> None:
    """Chamado na leitura do PCP Hub: OP que saiu de uma maquina e apareceu em outra durante uma auditoria vira log (origem -> destino)."""
    abertas = {a.maquina for a in AuditoriaSessao.query.filter_by(encerrado_em=None).all()}
    if not abertas:
        return
    for op, (s_ant, m_ant) in removidas.items():
        if op not in novas:
            continue
        s_novo, m_novo = novas[op]
        if m_ant == m_novo or not ({m_ant, m_novo} & abertas):
            continue
        sessao = sessao_aberta(m_ant) or sessao_aberta(m_novo)
        db.session.add(AuditoriaLog(sessao_id=sessao.id, usuario='Sistema (PCP Hub)', mecanico=sessao.mecanico, acao='MOVIMENTACAO', maquina=m_novo, artigo=s_novo.artigo, op=op,
                                    dt_cliente=s_novo.dt_cliente, origem=str(m_ant), destino=str(m_novo)))


# ---------- Controle de Artigos ----------

def garantir_aba() -> None:
    cat = Category.query.filter_by(slug='planejamento').first()
    if not cat or Tab.query.filter_by(slug='controle-artigos').first():
        return
    ordem = max([t.order or 0 for t in cat.tabs] or [0]) + 1
    tab = Tab(category_id=cat.id, slug='controle-artigos', name_pt='Controle de Artigos', name_es='Control de Artículos', icon='⛔', icon_color='#d6455d', is_new=True, order=ordem)
    db.session.add(tab)
    tab.sectors = [s for s in Sector.query.all() if s.name in SETORES]
    db.session.commit()


def _norm_artigo(t) -> str:
    m = re.match(r'\s*(\S+)', str(t or ''))
    cod = re.sub(r'-.*$', '', m.group(1).upper()) if m else ''
    cod = re.sub(r'[^A-Z0-9]', '', cod)
    return (cod.lstrip('0') or '0') if cod.isdigit() else cod


def _dt(v):
    try:
        return datetime.fromisoformat(str(v).replace('Z', '+00:00')).astimezone(timezone.utc).replace(tzinfo=None) if v else None
    except ValueError:
        return None


def _maq(v):
    d = re.sub(r'\D', '', str(v or ''))
    return int(d) if d else None


def gravar_impedimentos(linhas: list) -> int:
    """Importacao unica do PCP Hub (so quando o Controle de Artigos do Gestao ainda esta vazio). Depois o Gestao e a fonte: nada e apagado nem sobrescrito."""
    if not linhas or ArtigoImpedimento.query.first():
        return 0
    for r in linhas:
        db.session.add(ArtigoImpedimento(id=str(r.get('id')), artigo=str(r.get('article') or '').strip(), artigo_norm=_norm_artigo(r.get('article')), op=r.get('op'),
                                         qt_solicitada=float(r.get('qt_solicitada') or 0), maquina=_maq(r.get('maquina_origem')), destino=str(r.get('maquina_destino') or ''),
                                         motivo=r.get('motivo'), justificativa=r.get('justificativa'), movido_por=r.get('movido_por_nome'), movido_em=_dt(r.get('movido_em')),
                                         ativo=bool(r.get('ativo')), desativado_por=r.get('desativado_por_nome'), desativado_em=_dt(r.get('desativado_em')),
                                         motivo_desativacao=r.get('motivo_desativacao'), origem_registro='hub'))
    db.session.commit()
    return len(linhas)


def bloqueio(artigo, numero_maquina) -> str:
    """Mensagem de bloqueio se o artigo tem impedimento ativo nesta maquina; senao ''."""
    try:
        numero = int(numero_maquina)
    except (TypeError, ValueError):
        return ''
    i = ArtigoImpedimento.query.filter_by(ativo=True, maquina=numero, artigo_norm=_norm_artigo(artigo)).order_by(ArtigoImpedimento.movido_em.desc()).first()
    if not i:
        return ''
    quando = i.movido_em.replace(tzinfo=timezone.utc).astimezone(timezone(timedelta(hours=-3))).strftime('%d/%m/%Y %H:%M') if i.movido_em else ''
    return f'O artigo {i.artigo} não roda na máquina {numero} (registrado em {quando} — {MOTIVOS.get(i.motivo, i.motivo)}: "{i.justificativa}" — por {i.movido_por or "?"}). Para liberar, desative o impedimento em Controle de Artigos.'


def _tab_ca():
    tab = Tab.query.filter_by(slug='controle-artigos').first_or_404()
    if not tab.visible_to(current_user):
        abort(403)
    return tab


@aud_bp.route('/impedimentos/registrar', methods=['POST'])
@login_required
def registrar_impedimento():
    _tab_ca()
    d = request.get_json(silent=True) or {}
    artigo = re.sub(r'\s+', ' ', str(d.get('artigo') or '')).strip()[:60]
    maquina = _maq(d.get('maquina'))
    destino = re.sub(r'\D', '', str(d.get('destino') or ''))
    motivo = str(d.get('motivo') or '').upper()
    just = re.sub(r'\s+', ' ', str(d.get('justificativa') or '')).strip()
    if not artigo or not maquina:
        return jsonify(erro='Informe o artigo e a máquina onde ele não roda.'), 400
    if motivo not in MOTIVOS:
        return jsonify(erro='Escolha o motivo (Malha, Problema técnico ou Outro).'), 400
    if len(just) < 10:
        return jsonify(erro='A justificativa precisa ter pelo menos 10 caracteres.'), 400
    if ArtigoImpedimento.query.filter_by(ativo=True, maquina=maquina, artigo_norm=_norm_artigo(artigo)).first():
        return jsonify(erro=f'O artigo {artigo} já está bloqueado na máquina {maquina}.'), 409
    try:
        qt = float(str(d.get('qt') or 0).replace(',', '.'))
    except ValueError:
        qt = 0
    db.session.add(ArtigoImpedimento(id=uuid.uuid4().hex, artigo=artigo, artigo_norm=_norm_artigo(artigo), op=str(d.get('op') or '').strip()[:30] or None, qt_solicitada=qt, maquina=maquina,
                                     destino=destino, motivo=motivo, justificativa=just, movido_por=_usuario(), movido_em=datetime.utcnow(), ativo=True, origem_registro='gestao'))
    db.session.commit()
    return jsonify(ok=True)


@aud_bp.route('/impedimentos/<id>/desativar', methods=['POST'])
@login_required
def desativar_impedimento(id):
    _tab_ca()
    d = request.get_json(silent=True) or {}
    motivo = re.sub(r'\s+', ' ', str(d.get('motivo') or '')).strip()
    if len(motivo) < 10:
        return jsonify(erro='A justificativa para liberar precisa ter pelo menos 10 caracteres.'), 400
    i = db.session.get(ArtigoImpedimento, id)
    if not i or not i.ativo:
        return jsonify(erro='Impedimento ativo não encontrado.'), 404
    i.ativo, i.desativado_por, i.desativado_em, i.motivo_desativacao = False, _usuario(), datetime.utcnow(), motivo
    db.session.commit()
    return jsonify(ok=True)


def impedidos_por_sequencia(sequencias, numero_maquina: int) -> dict:
    """{id da sequencia: texto do bloqueio} das OPs cujo artigo nao pode rodar nesta maquina (impedimento ativo)."""
    ativos = {}
    for i in ArtigoImpedimento.query.filter_by(ativo=True, maquina=numero_maquina).all():
        ativos.setdefault(i.artigo_norm, i)
    saida = {}
    for s in sequencias:
        i = ativos.get(_norm_artigo(s.artigo))
        if i:
            saida[s.id] = f'Artigo {i.artigo} não roda na máquina {numero_maquina} ({MOTIVOS.get(i.motivo, i.motivo)}): {i.justificativa} — {i.movido_por or ""}'
    return saida


def _item(i):
    br = lambda d: d.replace(tzinfo=timezone.utc).astimezone(timezone(timedelta(hours=-3))).strftime('%d/%m/%Y') if d else ''
    return dict(id=i.id, artigo=i.artigo, op=i.op or '', qt=int(i.qt_solicitada or 0), maquina=i.maquina, destino=i.destino or '', motivo=MOTIVOS.get(i.motivo, i.motivo or ''),
                justificativa=i.justificativa or '', por=i.movido_por or '', em=br(i.movido_em), ativo=bool(i.ativo), desativado_por=i.desativado_por or '', desativado_em=br(i.desativado_em),
                motivo_desativacao=i.motivo_desativacao or '')


@aud_bp.route('/artigos-dados')
@login_required
def artigos_dados():
    tab = Tab.query.filter_by(slug='controle-artigos').first_or_404()
    if not tab.visible_to(current_user):
        abort(403)
    itens = ArtigoImpedimento.query.order_by(ArtigoImpedimento.artigo_norm, ArtigoImpedimento.movido_em.desc()).all()
    from app.models import Machine
    return jsonify(itens=[_item(i) for i in itens], motivos=MOTIVOS, maquinas=[m.number for m in Machine.query.order_by(Machine.number).all()])
