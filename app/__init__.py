import os
from datetime import timedelta, datetime

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
    from app.comercial_me.routes import comercial_me_bp
    from app.totvs.routes import bi_bp, mov_bp, pedidos_bp
    app.register_blueprint(gateway_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(chamados_bp)
    app.register_blueprint(comercial_me_bp)
    app.register_blueprint(bi_bp)
    app.register_blueprint(pedidos_bp)
    app.register_blueprint(mov_bp)

    app.jinja_env.globals['tab_url'] = tab_url

    from app.i18n import t as translate

    @app.context_processor
    def inject_globals():
        lang = session.get('lang', 'pt')
        pais_atual = session.get('login_pais') or ('GT' if lang == 'es' else 'BR')
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

    @app.template_filter('local')
    def _filtro_local(dt):
        """Datas guardadas em UTC (utcnow / PCP Hub) -> horario do ambiente do usuario (Brasil -3, Guatemala -6)."""
        from app.chamados.routes import FUSO_HORAS
        if not dt:
            return dt
        return dt + timedelta(hours=FUSO_HORAS.get(session.get('login_pais', 'BR'), -3))

    @app.template_filter('qtd')
    def _filtro_qtd(v):
        """Quantidade no padrao brasileiro, com ate 3 casas e sem zeros sobrando (686,52 / 1.250)."""
        try:
            txt = f'{float(v or 0):,.3f}'.rstrip('0').rstrip('.')
        except (TypeError, ValueError):
            return '0'
        return txt.replace(',', '#').replace('.', ',').replace('#', '.')

    with app.app_context():
        db_path = os.path.join(BASE_DIR, 'gestao_conexao.db')
        first_run = not os.path.exists(db_path)
        db.create_all()
        # colunas novas em bancos antigos (o create_all so cria tabelas que ainda nao existem)
        from sqlalchemy import inspect, text
        tabelas = set(inspect(db.engine).get_table_names())
        for tabela, novas in (('pedidos_relatorio', (('unidade', 'VARCHAR(120)'), ('ops_extra', 'VARCHAR(200)'))), ('pesagens_malharia', (('visto_em', 'DATETIME'), ('hora_real', 'DATETIME'), ('hora_origem', 'VARCHAR(10)'), ('trx_codigo', 'INTEGER'), ('peso_kg', 'FLOAT'))), ('pesagens_malharia_resumo', (('artigo_desc', 'VARCHAR(200)'),)), ('sequencias', (('situacao', 'VARCHAR(20)'),)),
                                ('historico_producao', (('motivo_cancelamento', 'VARCHAR(500)'), ('justificado_por', 'VARCHAR(120)'), ('justificado_em', 'DATETIME')))):
            if tabela not in tabelas:
                continue
            colunas = {c['name'] for c in inspect(db.engine).get_columns(tabela)}
            for nome, tipo in novas:
                if nome not in colunas:
                    db.session.execute(text(f'ALTER TABLE {tabela} ADD COLUMN {nome} {tipo}'))
                    db.session.commit()
        if first_run:
            from app.seed import seed_data
            seed_data()
        # a aba 'Pedidos Comercial (ME)' agora se chama 'Comercial ME' (bancos ja criados nao passam pelo seed)
        from app.models import Tab
        aba_me = Tab.query.filter_by(slug='pedidos-comercial-me').first()
        if aba_me and (aba_me.name_pt, aba_me.name_es) != ('Comercial ME', 'Comercial ME'):
            aba_me.name_pt = aba_me.name_es = 'Comercial ME'
            db.session.commit()
        # nomes das abas do Comercial
        renomear = {'dashboard-comercial': 'BI COMERCIAL', 'pedidos-comercial-mi': 'COMERCIAL MI'}
        mudou = False
        for slug, nome in renomear.items():
            aba = Tab.query.filter_by(slug=slug).first()
            if aba and (aba.name_pt, aba.name_es) != (nome, nome):
                aba.name_pt = aba.name_es = nome
                mudou = True
        if mudou:
            db.session.commit()
        # contas iniciais por setor (idempotente)
        from app.usuarios_padrao import criar_usuarios_padrao
        criar_usuarios_padrao()

    # atualizacao diaria (08:00) do faturamento TOTVS. O 'python run.py' (debug) abre 2 processos: so o filho
    # do reloader (WERKZEUG_RUN_MAIN) agenda. Em outro servidor, defina BI_AGENDADOR=1 no .env.
    if os.environ.get('WERKZEUG_RUN_MAIN') == 'true' or os.environ.get('BI_AGENDADOR') == '1':
        from app.totvs.servico import iniciar_agendador
        iniciar_agendador()
        from app.totvs.pedidos import iniciar_agendador as iniciar_pedidos
        iniciar_pedidos(app)      # Relatorio Pedidos: a cada 2 min
        from app.totvs.movimentos import iniciar_agendador as iniciar_movimentos
        iniciar_movimentos(app)   # pesagens da malharia (local 23) das OPs da Programacao: a cada 2 min

    return app
