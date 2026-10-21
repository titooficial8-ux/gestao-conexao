from __future__ import annotations

import threading
from datetime import date

from flask import Blueprint, abort, current_app, jsonify, request
from flask_login import current_user, login_required

from app import db
from app.indicadores import servico
from app.models import Category, Sector, Tab

ind_bp = Blueprint('indicadores', __name__, url_prefix='/indicadores')

SETORES = ('Diretoria', 'PCP', 'Produção', 'Qualidade', 'Comercial')


def garantir_aba() -> None:
    """Cria a aba Indicadores (Producao) se ainda nao existir, visivel para Diretoria, PCP, Producao, Qualidade e Comercial."""
    cat = Category.query.filter_by(slug='producao').first()
    if not cat or Tab.query.filter_by(slug='indicadores').first():
        return
    ordem = max([t.order or 0 for t in cat.tabs] or [0]) + 1
    tab = Tab(category_id=cat.id, slug='indicadores', name_pt='Indicadores', name_es='Indicadores', icon='\U0001F4C8', icon_color='#1f9d8f', is_new=True, order=ordem)
    db.session.add(tab)
    tab.sectors = [s for s in Sector.query.all() if s.name in SETORES]
    db.session.commit()


def _autorizar():
    tab = Tab.query.filter_by(slug='indicadores').first_or_404()
    if not tab.visible_to(current_user):
        abort(403)


@ind_bp.route('/reuniao')
@login_required
def reuniao():
    _autorizar()
    try:
        dia = date.fromisoformat(request.args.get('dia') or date.today().isoformat())
    except ValueError:
        abort(400)
    try:
        return jsonify(servico.reuniao(dia))
    except Exception as e:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        return jsonify(erro=f'{type(e).__name__}: {str(e)[:200]}'), 500


@ind_bp.route('/mensal')
@login_required
def mensal():
    _autorizar()
    try:
        ano, mes = [int(x) for x in (request.args.get('mes') or date.today().strftime('%Y-%m')).split('-')]
    except ValueError:
        abort(400)
    try:
        return jsonify(servico.mensal(ano, mes))
    except Exception as e:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        return jsonify(erro=f'{type(e).__name__}: {str(e)[:200]}'), 500


@ind_bp.route('/atualizar', methods=['POST'])
@login_required
def atualizar():
    _autorizar()
    app = current_app._get_current_object()
    threading.Thread(target=lambda: (servico.atualizar_acabamento(app), servico.atualizar_reprova_cru(app)), daemon=True, name='indicadores-agora').start()
    return jsonify(ok=True)


@ind_bp.route('/nc-diagnostico')
@login_required
def nc_diagnostico():
    """So administrador: mostra se o SharePoint (lista ncform) responde, as colunas e 3 linhas de exemplo, para mapear o Reprova Cru."""
    if not current_user.is_admin:
        abort(403)
    from app.indicadores import sharepoint
    if not sharepoint.configurado():
        return jsonify(ok=False, erro='Faltam no .env: ' + ', '.join(sharepoint.faltando())), 200
    try:
        linhas = sharepoint.itens(limite=1000)
        return jsonify(ok=True, total_lido=len(linhas), colunas=sharepoint.colunas(), exemplos=linhas[:3])
    except Exception as e:  # noqa: BLE001
        return jsonify(ok=False, erro=str(e)[:400])


@ind_bp.route('/reprova-cru')
@login_required
def reprova_cru():
    _autorizar()
    try:
        ano, mes = [int(x) for x in (request.args.get('mes') or date.today().strftime('%Y-%m')).split('-')]
    except ValueError:
        abort(400)
    try:
        return jsonify(servico.reprova_cru_mensal(ano, mes))
    except Exception as e:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        return jsonify(erro=f'{type(e).__name__}: {str(e)[:200]}'), 500


@ind_bp.route('/setups-diagnostico')
@login_required
def setups_diagnostico():
    """So administrador: mostra o que a API de setups (acompanhamento de producao) responde, para conferir os campos."""
    if not current_user.is_admin:
        abort(403)
    return jsonify(servico.setups_diagnostico())
