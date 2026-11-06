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


@gamificacao_bp.route('/entrar', methods=['POST'])
@login_required
def entrar():
    """Guarda o setor escolhido na sessao: quem ja escolheu nao ve a abertura de novo ate sair (logout) ou trocar de setor."""
    _autorizar()
    from flask import session
    from app.models import gamificacao_meus_setores
    setor = (request.get_json(silent=True) or {}).get('setor')
    if setor in ('Produção', 'PCP') and setor in gamificacao_meus_setores(current_user):
        session['gm_setor'] = setor
        return jsonify(ok=True)
    return jsonify(ok=False), 400


@gamificacao_bp.route('/sair', methods=['POST'])
@login_required
def sair():
    from flask import session
    session.pop('gm_setor', None)
    return jsonify(ok=True)


@gamificacao_bp.route('/dados')
@login_required
def dados():
    _autorizar()
    from app import acompanhamento as ac
    if not ac.configurado():
        return jsonify(ok=False, erro='ACOMP_TOKEN/SETUPS_TOKEN não configurado no servidor.')
    params = {k: v for k, v in request.args.items()}
    try:
        r = ac._cached('gamificacao_' + repr(sorted(params.items())), 10, lambda: ac.chamar_gamificacao(params or None))
    except Exception as e:  # noqa: BLE001
        return jsonify(ok=False, erro=str(e)[:300])
    return jsonify(ok=True, erro=r.get('erro'), dados=r.get('itens'))
