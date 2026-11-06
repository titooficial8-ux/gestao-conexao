"""Gamificacao (Gestao Producao): ranking de turnos e celulas lido do Acompanhamento de Producao (GET /gamificacao). So lideres e gestao da producao (e administrador)."""
from __future__ import annotations

from flask import Blueprint, abort, jsonify, request
from flask_login import current_user, login_required

from app.models import Tab

gamificacao_bp = Blueprint('gamificacao', __name__, url_prefix='/gamificacao')


def _autorizar():
    tab = Tab.query.filter_by(slug='gamificacao').first_or_404()
    if not tab.visible_to(current_user):
        abort(403)


@gamificacao_bp.route('/dados')
@login_required
def dados():
    _autorizar()
    from app import acompanhamento as ac
    if not ac.configurado():
        return jsonify(ok=False, erro='ACOMP_TOKEN/SETUPS_TOKEN não configurado no servidor.')
    params = {k: v for k, v in request.args.items()}
    try:
        r = ac._cached('gamificacao_' + repr(sorted(params.items())), 30, lambda: ac.chamar('/gamificacao', params or None))
    except Exception as e:  # noqa: BLE001
        return jsonify(ok=False, erro=str(e)[:300])
    return jsonify(ok=True, erro=r.get('erro'), dados=r.get('itens'))
