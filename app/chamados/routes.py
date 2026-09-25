from flask import Blueprint, render_template, abort, session
from flask_login import login_required, current_user

from app.decorators import admin_required

chamados_bp = Blueprint('chamados', __name__, url_prefix='/chamados')


def _ambiente_atual():
    return session.get('login_pais', 'BR')


@chamados_bp.route('/')
@login_required
def home():
    return render_template('chamados/home.html')


@chamados_bp.route('/novo-chamado')
@login_required
def novo_chamado():
    return render_template('chamados/placeholder.html', titulo_key='chamados_novo_titulo')


@chamados_bp.route('/suporte-brasil')
@login_required
def suporte_brasil():
    if _ambiente_atual() != 'BR':
        abort(403)
    return render_template('chamados/placeholder.html', titulo_key='chamados_suporte_br_titulo')


@chamados_bp.route('/suporte-guatemala')
@login_required
def suporte_guatemala():
    if _ambiente_atual() != 'GT':
        abort(403)
    return render_template('chamados/placeholder.html', titulo_key='chamados_suporte_gt_titulo')


@chamados_bp.route('/dashboard')
@login_required
@admin_required
def dashboard():
    return render_template('chamados/placeholder.html', titulo_key='chamados_dashboard_titulo')


@chamados_bp.route('/auditoria')
@login_required
@admin_required
def auditoria():
    return render_template('chamados/placeholder.html', titulo_key='auditoria_sistemica_titulo')
