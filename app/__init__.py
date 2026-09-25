import os
from datetime import datetime

from flask import Flask, session, render_template, url_for, request
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, current_user

db = SQLAlchemy()
login_manager = LoginManager()

BASE_DIR = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))

def tab_url(tab):
    return url_for('main.view_tab', slug=tab.slug)


def create_app():
    app = Flask(__name__)
    app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'conexao-gestao-chave-temporaria-trocar-em-producao')
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(BASE_DIR, 'gestao_conexao.db')
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

    db.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = 'auth.login'
    login_manager.login_message = 'faca_login_para_continuar'
    login_manager.login_message_category = 'error'

    from app.models import User, Category

    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    from app.gateway.routes import gateway_bp
    from app.auth.routes import auth_bp
    from app.main.routes import main_bp
    from app.admin.routes import admin_bp
    from app.chamados.routes import chamados_bp
    app.register_blueprint(gateway_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(chamados_bp)

    app.jinja_env.globals['tab_url'] = tab_url

    from app.i18n import t as translate

    @app.context_processor
    def inject_globals():
        lang = session.get('lang', 'pt')
        pais_atual = 'GT' if lang == 'es' else 'BR'
        # o botao ativo (Planejamento/Chamados) segue a area que o usuario
        # esta navegando de fato, nao so a escolha inicial da sessao
        if request.blueprint == 'chamados':
            acesso_tipo = 'chamados'
        elif request.blueprint == 'main':
            acesso_tipo = 'planejamento'
        else:
            acesso_tipo = session.get('acesso_tipo', 'planejamento')
        nav_categories = []
        if current_user.is_authenticated:
            cats = Category.query.order_by(Category.order).all()
            for cat in cats:
                cat.visible_tabs = [tab for tab in cat.tabs if tab.visible_to(current_user)]
            nav_categories = cats
        return dict(
            t=lambda key: translate(key, lang),
            lang=lang,
            pais_atual=pais_atual,
            acesso_tipo=acesso_tipo,
            nav_categories=nav_categories,
            current_year=datetime.utcnow().year,
        )

    @app.errorhandler(403)
    def forbidden(_e):
        return render_template('errors/403.html'), 403

    @app.errorhandler(404)
    def not_found(_e):
        return render_template('errors/404.html'), 404

    with app.app_context():
        db_path = os.path.join(BASE_DIR, 'gestao_conexao.db')
        first_run = not os.path.exists(db_path)
        db.create_all()
        if first_run:
            from app.seed import seed_data
            seed_data()

    return app
