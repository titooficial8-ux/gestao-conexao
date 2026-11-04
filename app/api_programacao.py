"""API somente-leitura da PROGRAMACAO (tudo que esta programado, por maquina) para outros sistemas.

Autenticacao: cabecalho  Authorization: Bearer <token>  (ou  X-API-Key: <token>), com o token definido no .env da VPS:
    GESTAO_API_TOKEN=<texto longo e secreto>
Sem o token no .env a API fica desligada (503). Um administrador logado no navegador tambem pode abrir (para testar).

GET /api/v1/programacao                 -> TODAS as maquinas (inclusive as sem programacao) e suas sequencias (nao finalizadas/canceladas)
    ?maquina=48                         -> so uma maquina
    ?op=3135                            -> so uma OP
    ?status=PRODUZINDO|EM_SEQUENCIA|PCP -> filtra pelo status
    ?incluir_finalizadas=1              -> inclui FINALIZADO/CANCELADO
GET /api/v1/programacao/<op>            -> uma OP
"""
from __future__ import annotations

import hmac
import os

from flask import Blueprint, jsonify, request
from flask_login import current_user

from app import db  # noqa: F401
from app.models import Machine, Sequencia

api_prog_bp = Blueprint('api_prog', __name__, url_prefix='/api/v1')


def _autorizado() -> bool:
    token = (os.getenv('GESTAO_API_TOKEN') or '').strip()
    enviado = (request.headers.get('X-API-Key') or '').strip()
    auth = request.headers.get('Authorization') or ''
    if auth.lower().startswith('bearer '):
        enviado = auth[7:].strip()
    if token and enviado and hmac.compare_digest(token.encode(), enviado.encode()):
        return True
    return bool(current_user.is_authenticated and current_user.is_admin)


def _d(v):
    return v.isoformat() if v else None


def _linha(q: Sequencia) -> dict:
    try:
        from app.main.routes import resolve_metros_from_article
        ds_metros = float(str(resolve_metros_from_article(q.artigo) or '').replace(',', '.') or 0) or None
    except Exception:  # noqa: BLE001
        ds_metros = None
    return dict(
        id=q.id, ordem=q.sequence_order, op=q.op, artigo=q.artigo, cliente=q.cliente, estrutura=q.estrutura, ds_metros=ds_metros,
        status=q.status, situacao=q.situacao, total_pcs=(q.total_pcs or 0) + (q.pecas_extra or 0), pecas_produzidas=q.pecas_produzidas or 0, pc_produzir=q.pc_produzir,
        qt_solicitada=q.qt_solicitada, qt_avulsa=q.qt_avulsa, mes_producao=q.production_month, dt_cliente=_d(q.dt_cliente), termino_malharia=_d(q.termino_malharia),
        previsao_termino=_d(q.prev_termino_em), pecas_dia=q.pecas_dia, observacao_pcp=q.obs_pcp, origem=q.origem_reg or 'totvs', atualizado_em=_d(q.synced_at))


@api_prog_bp.route('/programacao')
@api_prog_bp.route('/programacao/<op>')
def programacao(op=None):
    if not (os.getenv('GESTAO_API_TOKEN') or '').strip() and not (current_user.is_authenticated and current_user.is_admin):
        return jsonify(erro='API desligada: defina GESTAO_API_TOKEN no .env do servidor.'), 503
    if not _autorizado():
        return jsonify(erro='Não autorizado: envie Authorization: Bearer <token>.'), 401
    op = op or request.args.get('op')
    q = Sequencia.query.join(Machine, Sequencia.machine_id == Machine.id)
    if request.args.get('maquina', '').isdigit():
        q = q.filter(Machine.number == int(request.args['maquina']))
    if op:
        q = q.filter(Sequencia.op == str(op))
    if request.args.get('status'):
        q = q.filter(Sequencia.status == request.args['status'].upper())
    if request.args.get('incluir_finalizadas') != '1' and not op:
        q = q.filter(db.or_(Sequencia.status.is_(None), Sequencia.status.notin_(['FINALIZADO', 'CANCELADO'])))
    linhas = q.order_by(Machine.number, Sequencia.sequence_order, Sequencia.id).all()
    maquinas = {}
    if not op and not request.args.get('status'):                   # TODAS as maquinas, mesmo as sem nada programado (sequencias vazias)
        qm = Machine.query.order_by(Machine.number)
        if request.args.get('maquina', '').isdigit():
            qm = qm.filter(Machine.number == int(request.args['maquina']))
        for m in qm.all():
            maquinas[m.number] = dict(maquina=m.number, status_maquina=m.status, sequencias=[])
    for s in linhas:
        m = s.machine
        g = maquinas.setdefault(m.number, dict(maquina=m.number, status_maquina=m.status, sequencias=[]))
        g['sequencias'].append(_linha(s))
    lista = [maquinas[k] for k in sorted(maquinas)]
    return jsonify(total_maquinas=len(lista), total_sequencias=len(linhas), maquinas=lista)
