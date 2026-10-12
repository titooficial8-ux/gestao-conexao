import os
import re
import secrets
from datetime import datetime
from urllib.parse import urlencode

import requests
from flask import Blueprint, render_template, request, redirect, url_for, session, flash
from flask_login import login_user, logout_user, login_required, current_user

from app import db
from app.models import User

auth_bp = Blueprint('auth', __name__)

DOMINIO_CORPORATIVO = '@conexaomalhas.com.br'

LINKEDIN_CLIENT_ID = os.environ.get('LINKEDIN_CLIENT_ID')
LINKEDIN_CLIENT_SECRET = os.environ.get('LINKEDIN_CLIENT_SECRET')
LINKEDIN_AUTH_URL = 'https://www.linkedin.com/oauth/v2/authorization'
LINKEDIN_TOKEN_URL = 'https://www.linkedin.com/oauth/v2/accessToken'
LINKEDIN_USERINFO_URL = 'https://api.linkedin.com/v2/userinfo'


def _fixar_ambiente_do_usuario(user):
    """Quem nao e admin so entra no ambiente do proprio pais."""
    if not user.is_admin:
        session['login_pais'] = user.country
        session['lang'] = 'es' if user.country == 'GT' else 'pt'


def _pos_login_redirect():
    if session.get('acesso_tipo') == 'chamados':
        return redirect(url_for('chamados.home'))
    return redirect(url_for('main.dashboard'))


def _senha_atende_criterios(password):
    return (
        re.search(r'[A-Z]', password)
        and re.search(r'[0-9]', password)
        and re.search(r'[^A-Za-z0-9]', password)
    )


def _passa_verificacao_humana(form):
    """Honeypot: campo invisivel que só um robô preencheria."""
    return not form.get('website')


def _buscar_usuario_login(identificador):
    """Aceita e-mail completo OU apenas o primeiro nome do usuário."""
    identificador = identificador.strip()
    if not identificador:
        return None
    if '@' in identificador:
        return User.query.filter_by(email=identificador.lower()).first()

    candidatos = User.query.filter(User.name.ilike(f'{identificador}%')).all()
    for candidato in candidatos:
        primeiro_nome = candidato.name.strip().split(' ')[0]
        if primeiro_nome.lower() == identificador.lower():
            return candidato
    return None


@auth_bp.route('/idioma/<lang>')
def set_lang(lang):
    if lang in ('pt', 'es'):
        session['lang'] = lang
    return redirect(request.referrer or url_for('gateway.tipo'))


@auth_bp.route('/ambiente-sessao/<pais>')
def set_ambiente_sessao(pais):
    if pais in ('BR', 'GT'):
        pode_trocar = (
            not current_user.is_authenticated
            or current_user.is_admin
            or current_user.country == pais
        )
        if pode_trocar:
            session['lang'] = 'es' if pais == 'GT' else 'pt'
            session['login_pais'] = pais
    return redirect(request.referrer or url_for('main.dashboard'))


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return _pos_login_redirect()

    tipo = session.get('acesso_tipo', 'planejamento')
    pais = session.get('login_pais', 'BR')

    if request.method == 'POST':
        identificador = request.form.get('email', '')
        password = request.form.get('password', '')

        if not _passa_verificacao_humana(request.form):
            flash('verificacao_robo_invalida', 'error')
            return redirect(url_for('auth.login'))

        user = _buscar_usuario_login(identificador)

        if not user or not user.check_password(password):
            flash('email_ou_senha_invalidos', 'error')
            return redirect(url_for('auth.login'))
        if user.status == 'pendente':
            flash('conta_pendente', 'error')
            return redirect(url_for('auth.login'))
        if user.status == 'bloqueado':
            flash('conta_bloqueada', 'error')
            return redirect(url_for('auth.login'))

        login_user(user)
        _fixar_ambiente_do_usuario(user)
        return _pos_login_redirect()

    return render_template(
        'auth/login.html',
        tipo=tipo,
        pais=pais,
        linkedin_disponivel=bool(LINKEDIN_CLIENT_ID),
    )


@auth_bp.route('/esqueci-senha', methods=['GET', 'POST'])
def esqueci_senha():
    if current_user.is_authenticated:
        return _pos_login_redirect()

    tipo = session.get('acesso_tipo', 'planejamento')
    pais = session.get('login_pais', 'BR')

    if request.method == 'POST':
        identificador = request.form.get('email', '')

        if not _passa_verificacao_humana(request.form):
            flash('verificacao_robo_invalida', 'error')
            return redirect(url_for('auth.esqueci_senha'))

        user = _buscar_usuario_login(identificador)
        if user:
            user.reset_password_requested = True
            user.reset_password_requested_at = datetime.utcnow()
            db.session.commit()

        # mensagem generica: nao revela se o e-mail/usuario existe ou nao
        flash('esqueci_senha_solicitado', 'success')
        return redirect(url_for('auth.login'))

    return render_template('auth/esqueci_senha.html', tipo=tipo, pais=pais)


@auth_bp.route('/cadastro', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return _pos_login_redirect()

    pais = session.get('login_pais', 'BR')
    country = 'GT' if pais == 'GT' else 'BR'

    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')

        if not name or not email or not password:
            flash('preencha_todos_campos', 'error')
            return redirect(url_for('auth.register'))

        if not email.endswith(DOMINIO_CORPORATIVO):
            flash('email_dominio_invalido', 'error')
            return redirect(url_for('auth.register'))

        if not _senha_atende_criterios(password):
            flash('senha_criterios_invalidos', 'error')
            return redirect(url_for('auth.register'))

        if User.query.filter_by(email=email).first():
            flash('email_ja_cadastrado', 'error')
            return redirect(url_for('auth.register'))

        user = User(name=name, email=email, country=country, status='pendente')
        user.set_password(password)
        db.session.add(user)
        db.session.commit()

        flash('cadastro_realizado', 'success')
        return redirect(url_for('auth.login'))

    return render_template('auth/register.html', pais=pais)


@auth_bp.route('/conta/senha', methods=['GET', 'POST'])
@login_required
def trocar_senha():
    if request.method == 'POST':
        senha_atual = request.form.get('senha_atual', '')
        nova_senha = request.form.get('nova_senha', '')
        confirmar_senha = request.form.get('confirmar_senha', '')

        if not current_user.check_password(senha_atual):
            flash('senha_atual_incorreta', 'error')
            return redirect(url_for('auth.trocar_senha'))
        if len(nova_senha) < 6:
            flash('nova_senha_muito_curta', 'error')
            return redirect(url_for('auth.trocar_senha'))
        if nova_senha != confirmar_senha:
            flash('senhas_nao_conferem', 'error')
            return redirect(url_for('auth.trocar_senha'))

        current_user.set_password(nova_senha)
        db.session.commit()
        flash('senha_atualizada', 'success')
        return redirect(url_for('auth.trocar_senha'))

    return render_template('auth/trocar_senha.html')


@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    session.pop('acesso_tipo', None)
    return redirect(url_for('gateway.tipo'))


# ---------------------------------------------------------------------------
# Login com LinkedIn (OAuth 2.0 / OpenID Connect)
# Exige as variaveis de ambiente LINKEDIN_CLIENT_ID e LINKEDIN_CLIENT_SECRET,
# geradas em https://www.linkedin.com/developers/apps (produto "Sign In with
# LinkedIn using OpenID Connect"). A redirect URI cadastrada la precisa ser
# exatamente a mesma que _linkedin_redirect_uri() gera aqui.
# ---------------------------------------------------------------------------

def _linkedin_redirect_uri():
    return url_for('auth.linkedin_callback', _external=True)


@auth_bp.route('/login/linkedin')
def linkedin_login():
    if not LINKEDIN_CLIENT_ID:
        flash('linkedin_nao_configurado', 'error')
        return redirect(url_for('auth.login'))

    state = secrets.token_urlsafe(16)
    session['linkedin_state'] = state
    params = {
        'response_type': 'code',
        'client_id': LINKEDIN_CLIENT_ID,
        'redirect_uri': _linkedin_redirect_uri(),
        'scope': 'openid profile email',
        'state': state,
    }
    return redirect(f'{LINKEDIN_AUTH_URL}?{urlencode(params)}')


@auth_bp.route('/login/linkedin/callback')
def linkedin_callback():
    if request.args.get('error'):
        flash('linkedin_login_cancelado', 'error')
        return redirect(url_for('auth.login'))

    state = request.args.get('state')
    if not state or state != session.pop('linkedin_state', None):
        flash('linkedin_login_falhou', 'error')
        return redirect(url_for('auth.login'))

    code = request.args.get('code')
    if not code:
        flash('linkedin_login_falhou', 'error')
        return redirect(url_for('auth.login'))

    try:
        token_resp = requests.post(
            LINKEDIN_TOKEN_URL,
            data={
                'grant_type': 'authorization_code',
                'code': code,
                'redirect_uri': _linkedin_redirect_uri(),
                'client_id': LINKEDIN_CLIENT_ID,
                'client_secret': LINKEDIN_CLIENT_SECRET,
            },
            timeout=10,
        )
        token_resp.raise_for_status()
        access_token = token_resp.json().get('access_token')

        userinfo_resp = requests.get(
            LINKEDIN_USERINFO_URL,
            headers={'Authorization': f'Bearer {access_token}'},
            timeout=10,
        )
        userinfo_resp.raise_for_status()
        info = userinfo_resp.json()
    except requests.RequestException:
        flash('linkedin_login_falhou', 'error')
        return redirect(url_for('auth.login'))

    email = (info.get('email') or '').strip().lower()
    name = info.get('name') or email

    if not email:
        flash('linkedin_login_falhou', 'error')
        return redirect(url_for('auth.login'))

    user = User.query.filter_by(email=email).first()

    if not user:
        pais = session.get('login_pais', 'BR')
        user = User(
            name=name,
            email=email,
            country='GT' if pais == 'GT' else 'BR',
            status='pendente',
        )
        user.set_password(secrets.token_urlsafe(24))
        db.session.add(user)
        db.session.commit()
        flash('cadastro_realizado', 'success')
        return redirect(url_for('auth.login'))

    if user.status == 'pendente':
        flash('conta_pendente', 'error')
        return redirect(url_for('auth.login'))
    if user.status == 'bloqueado':
        flash('conta_bloqueada', 'error')
        return redirect(url_for('auth.login'))

    login_user(user)
    _fixar_ambiente_do_usuario(user)
    return _pos_login_redirect()
