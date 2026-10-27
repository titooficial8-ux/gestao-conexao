"""Expedicao (Planejamento): NFs e estoque de expedicao, tudo lido do TOTVS (app/totvs/expedicao.py)."""
from flask import Blueprint, abort, jsonify, request
from flask_login import current_user, login_required

from app import db
from app.models import Category, Sector, Tab

exp_bp = Blueprint('expedicao', __name__, url_prefix='/expedicao')
SETORES = ('Diretoria', 'PCP', 'Produção', 'Comercial')


def garantir_aba() -> None:
    cat = Category.query.filter_by(slug='planejamento').first()
    if not cat or Tab.query.filter_by(slug='expedicao').first():
        return
    ordem = max([t.order or 0 for t in cat.tabs] or [0]) + 1
    tab = Tab(category_id=cat.id, slug='expedicao', name_pt='Expedição', name_es='Expedición', icon='\U0001F69A', icon_color='#0d9488', is_new=True, order=ordem)
    db.session.add(tab)
    tab.sectors = [s for s in Sector.query.all() if s.name in SETORES]
    db.session.commit()


def _autorizar():
    tab = Tab.query.filter_by(slug='expedicao').first_or_404()
    if not tab.visible_to(current_user):
        abort(403)


@exp_bp.route('/dados')
@login_required
def dados():
    _autorizar()
    from app.totvs import expedicao
    try:
        dias = max(1, min(int(request.args.get('dias', 30)), 90))
    except ValueError:
        dias = 30
    return jsonify(expedicao.painel(dias))


@exp_bp.route('/atualizar', methods=['POST'])
@login_required
def atualizar():
    _autorizar()
    import threading
    from app.totvs import expedicao
    threading.Thread(target=expedicao.atualizar, daemon=True).start()
    return jsonify(ok=True)


@exp_bp.route('/diagnostico')
@login_required
def diagnostico():
    """So administrador: exemplo cru de lote e de NF para conferir os nomes dos campos do TOTVS."""
    if not current_user.is_admin:
        abort(403)
    from app.totvs import expedicao
    d = expedicao.ler()
    return jsonify(amostra_lote=d.get('amostra_lote'), amostra_nf=d.get('amostra_nf'), n_produtos=len(d.get('estoque') or {}), n_notas=len(d.get('notas') or {}),
                   status_nf=sorted({n[3] for n in (d.get('notas') or {}).values()}), status_nfe=sorted({n[4] for n in (d.get('notas') or {}).values()}),
                   estado=expedicao.ESTADO)
