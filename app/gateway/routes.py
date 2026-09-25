from flask import Blueprint, render_template, redirect, url_for, session, abort
from flask_login import current_user

gateway_bp = Blueprint('gateway', __name__)

TIPOS_VALIDOS = ('planejamento', 'chamados')
PAISES_VALIDOS = ('BR', 'GT')


@gateway_bp.route('/')
def tipo():
    if current_user.is_authenticated:
        if session.get('acesso_tipo') == 'chamados':
            return redirect(url_for('chamados.home'))
        return redirect(url_for('main.dashboard'))

    # tela inicial e sempre em portugues; nao deixa o idioma de uma
    # visita anterior (ex: quem escolheu Guatemala) vazar pra ca e
    # confundir o navegador (ativa a traducao automatica sozinha)
    session.pop('lang', None)
    session.pop('login_pais', None)
    return render_template('gateway/tipo.html')


@gateway_bp.route('/ambiente/<tipo>')
def ambiente(tipo):
    if tipo not in TIPOS_VALIDOS:
        abort(404)
    return render_template('gateway/ambiente.html', tipo=tipo)


@gateway_bp.route('/ambiente/<tipo>/<pais>')
def escolher_ambiente(tipo, pais):
    if tipo not in TIPOS_VALIDOS or pais not in PAISES_VALIDOS:
        abort(404)
    session['acesso_tipo'] = tipo
    session['lang'] = 'es' if pais == 'GT' else 'pt'
    session['login_pais'] = pais
    return redirect(url_for('auth.login'))
