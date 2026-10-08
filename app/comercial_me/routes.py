"""Aba COMERCIAL ME (exportacao): paineis e planilhas importadas por
importar_comercial_me.py (arquivos em app/seed_data/comercial_me)."""
from flask import Blueprint, abort, jsonify, request, session
from flask_login import current_user, login_required

from app.models import Tab

from . import paineis
from .dados import carregar, catalogo

comercial_me_bp = Blueprint('comercial_me', __name__, url_prefix='/comercial-me')

SLUG = 'pedidos-comercial-me'


def ambiente_atual():
    return 'GT' if session.get('login_pais') == 'GT' else 'BR'


def idioma_atual():
    return 'es' if session.get('lang') == 'es' else 'pt'


def _autorizar():
    tab = Tab.query.filter_by(slug=SLUG).first_or_404()
    if not tab.visible_to(current_user):
        abort(403)


@comercial_me_bp.route('/tabela/<id_>')
@login_required
def tabela(id_):
    _autorizar()
    if id_ not in {t['id'] for t in catalogo()}:  # barra qualquer id fora do catalogo
        abort(404)
    d = carregar(id_)
    resp = jsonify({k: d[k] for k in ('id', 'titulo', 'grupo', 'tipo', 'arquivo', 'aba', 'atualizado_em', 'colunas', 'linhas')})
    resp.headers['Cache-Control'] = 'private, max-age=60'
    return resp


@comercial_me_bp.route('/painel/<nome>')
@login_required
def painel(nome):
    _autorizar()
    if nome not in {p['id'] for p in paineis.disponiveis(idioma_atual())}:
        abort(404)
    filtros = {k: request.args.get(k, None) for k in ('ano', 'cliente', 'pais', 'origem')}
    resp = jsonify(paineis.montar(nome, idioma_atual(), filtros))
    resp.headers['Cache-Control'] = 'no-store'
    return resp
