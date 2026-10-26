import threading
import os
from datetime import timedelta, datetime

from flask import Flask, session, render_template, url_for, request, redirect
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, current_user

db = SQLAlchemy()
login_manager = LoginManager()

BASE_DIR = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))

def tab_url(tab):
    return url_for('main.view_tab', slug=tab.slug)


def _em_rede(caminho: str) -> bool:
    """True se o arquivo esta em pasta de rede (caminho \\\\servidor\\... ou unidade mapeada, ex.: Z:)."""
    caminho = os.path.abspath(caminho)
    if caminho.startswith('\\\\'):
        return True
    if os.name == 'nt':
        try:
            import ctypes
            return ctypes.windll.kernel32.GetDriveTypeW(os.path.splitdrive(caminho)[0] + '\\') == 4        # DRIVE_REMOTE
        except Exception:  # noqa: BLE001
            return False
    return False


def create_app():
    from app.dns_fallback import instalar as _dns_fallback
    _dns_fallback()
    app = Flask(__name__)
    app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'conexao-gestao-chave-temporaria-trocar-em-producao')
    caminho_db = os.environ.get('GESTAO_DB') or os.path.join(BASE_DIR, 'gestao_conexao.db')       # GESTAO_DB: guardar o banco em outra pasta (ex.: disco local)
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + caminho_db
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    if os.environ.get('ATRAS_DO_PROXY') == '1':
        # Caddy (HTTPS) na frente: usa o endereco/protocolo reais nos links gerados (LinkedIn) e cookies so em HTTPS
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
        app.config['SESSION_COOKIE_SECURE'] = True
        app.config['REMEMBER_COOKIE_SECURE'] = True
        app.config['PREFERRED_URL_SCHEME'] = 'https'
    app.config['REMEMBER_COOKIE_DURATION'] = timedelta(days=30)       # 'Lembrar e-mail e manter conectado' do login
    app.config['MAX_CONTENT_LENGTH'] = int(2.1 * 1024 ** 3)           # upload de ate 2 GB (material institucional / videos)

    # varias threads gravam no banco o dia todo (pesagens, pedidos, baixas): sem WAL elas travam a leitura das telas
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {'connect_args': {'timeout': 30}}

    db.init_app(app)
    with app.app_context():
        from sqlalchemy import event

        usar_wal = os.environ.get('GESTAO_SQLITE_WAL') != '0' and not _em_rede(caminho_db)      # WAL nao funciona (e pode corromper) em pasta de rede

        @event.listens_for(db.engine, 'connect')
        def _sqlite_wal(conexao, _registro):
            c = conexao.cursor()
            if usar_wal:
                c.execute('PRAGMA journal_mode=WAL')
            c.execute('PRAGMA synchronous=NORMAL')
            c.execute('PRAGMA busy_timeout=30000')
            c.close()
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
    from app.totvs.routes import almox_bp, bi_bp, mov_bp, mp_bp, pedidos_bp
    app.register_blueprint(gateway_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(chamados_bp)
    app.register_blueprint(comercial_me_bp)
    app.register_blueprint(bi_bp)
    app.register_blueprint(pedidos_bp)
    app.register_blueprint(mov_bp)
    app.register_blueprint(almox_bp)
    app.register_blueprint(mp_bp)
    from app.pcphub import pcphub_bp
    app.register_blueprint(pcphub_bp)
    from app.indicadores.routes import ind_bp
    app.register_blueprint(ind_bp)
    from app.desenvolvimento.routes import dev_bp
    app.register_blueprint(dev_bp)
    from app.perfil import perfil_bp
    app.register_blueprint(perfil_bp)
    from app.presenca import presenca_bp
    app.register_blueprint(presenca_bp)
    from app.relatorio_fat.routes import rel_fat_bp
    app.register_blueprint(rel_fat_bp)

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

    @app.before_request
    def exigir_troca_de_senha():
        # senha provisoria: ate trocar, so a tela de troca (e sair) ficam disponiveis
        if current_user.is_authenticated and getattr(current_user, 'deve_trocar_senha', False):
            if request.endpoint not in ('auth.trocar_senha', 'auth.logout', 'auth.set_lang', 'presenca.ping', 'static'):
                return redirect(url_for('auth.trocar_senha'))

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
        from app.desenvolvimento import models as _dev_models  # noqa: F401  (tabelas do Desenvolvimento)
        from app.indicadores import models as _ind_models  # noqa: F401  (tabelas dos Indicadores)
        db.create_all()
        # colunas novas em bancos antigos (o create_all so cria tabelas que ainda nao existem)
        from sqlalchemy import inspect, text
        tabelas = set(inspect(db.engine).get_table_names())
        for tabela, novas in (('users', (('setor_id', 'INTEGER'), ('verificado', 'BOOLEAN DEFAULT 0'), ('deve_trocar_senha', 'BOOLEAN DEFAULT 0'), ('foto_versao', 'INTEGER DEFAULT 0'), ('capa_versao', 'INTEGER DEFAULT 0'), ('capa_x', 'INTEGER DEFAULT 50'), ('capa_y', 'INTEGER DEFAULT 50'), ('segundos_acesso', 'INTEGER DEFAULT 0'), ('selo_parabens_pendente', 'BOOLEAN DEFAULT 0'))), ('pedidos_relatorio', (('unidade', 'VARCHAR(120)'), ('ops_extra', 'VARCHAR(200)'))), ('pesagens_malharia', (('visto_em', 'DATETIME'), ('hora_real', 'DATETIME'), ('hora_origem', 'VARCHAR(10)'), ('trx_codigo', 'INTEGER'), ('peso_kg', 'FLOAT'))), ('pesagens_malharia_resumo', (('artigo_desc', 'VARCHAR(200)'), ('gramatura', 'FLOAT'), ('largura', 'FLOAT'), ('metro_ideal', 'FLOAT'), ('cliente_cod', 'INTEGER'))), ('baixas_fio', (('gramatura', 'FLOAT'), ('largura', 'FLOAT'))), ('sequencias', (('situacao', 'VARCHAR(20)'),)),
                                ('historico_producao', (('motivo_cancelamento', 'VARCHAR(500)'), ('justificado_por', 'VARCHAR(120)'), ('justificado_em', 'DATETIME')))):
            if tabela not in tabelas:
                continue
            colunas = {c['name'] for c in inspect(db.engine).get_columns(tabela)}
            for nome, tipo in novas:
                if nome not in colunas:
                    db.session.execute(text(f'ALTER TABLE {tabela} ADD COLUMN {nome} {tipo}'))
                    db.session.commit()
                    if (tabela, nome) == ('users', 'verificado'):  # selo inicial (depois e so pela tela de usuarios)
                        db.session.execute(text("UPDATE users SET verificado=1 WHERE lower(email)='christian.yarzon@conexaomalhas.com.br'"))
                        db.session.commit()
        if first_run:
            from app.seed import seed_data
            seed_data()
        # cargo 'Gestão P&D' (criado uma vez; a Dora ja entra com ele, depois e so pela tela de usuarios)
        from app.models import Role, User
        if not Role.query.filter_by(name='Gestão P&D').first():
            cargo_pd = Role(name='Gestão P&D')
            db.session.add(cargo_pd)
            db.session.flush()
            dora = User.query.filter(db.func.lower(User.email) == 'dora.appolinario@conexaomalhas.com.br').first()
            if dora:
                dora.role_id = cargo_pd.id
            db.session.commit()
        # novos cargos e ajustes de nome pedidos pela diretoria (cada cargo e criado uma vez e ja entra atribuido;
        # nomes so mudam se ainda estiverem como antes)
        def _pessoa(email=None, nome=None):
            if email:
                return User.query.filter(db.func.lower(User.email) == email).first()
            return User.query.filter_by(name=nome).first()
        for cargo, quem in (
            ('Mecânico Têxtil', dict(email='gustavo.bonfim@conexaomalhas.com.br')),
            ('Gestão Financeiro', dict(email='luciano@conexaomalhas.com.br')),
            ('Gestão Comercial', dict(nome='Marco - Gerente Comercial')),
            ('Gestão Desenvolvimento', dict(email='mauricio.stanczyk@conexaomalhas.com.br')),
        ):
            if not Role.query.filter_by(name=cargo).first():
                r = Role(name=cargo)
                db.session.add(r)
                db.session.flush()
                pessoa = _pessoa(**quem)
                if pessoa:
                    pessoa.role_id = r.id
        # correcao: Gustavo e Designer Têxtil e José e Mecânico Têxtil (uma vez, junto com a criacao do cargo)
        if not Role.query.filter_by(name='Designer Têxtil').first():
            designer = Role(name='Designer Têxtil')
            db.session.add(designer)
            db.session.flush()
            mecanico = Role.query.filter_by(name='Mecânico Têxtil').first()
            gustavo = _pessoa(email='gustavo.bonfim@conexaomalhas.com.br')
            jose = _pessoa(email='jose.alves@conexaomalhas.com.br')
            if gustavo:
                gustavo.role_id = designer.id
            if jose and mecanico:
                jose.role_id = mecanico.id
        for antigo, novo in (('Luciano (Administrador)', 'Luciano'), ('Marco - Gerente Comercial', 'Marco')):
            for u in User.query.filter_by(name=antigo).all():
                u.name = novo
        db.session.commit()
        # a aba 'Pedidos Comercial (ME)' agora se chama 'Comercial ME' (bancos ja criados nao passam pelo seed)
        from app.models import Tab
        aba_me = Tab.query.filter_by(slug='pedidos-comercial-me').first()
        if aba_me and (aba_me.name_pt, aba_me.name_es) != ('Comercial ME', 'Comercial ME'):
            aba_me.name_pt = aba_me.name_es = 'Comercial ME'
            db.session.commit()
        # nomes das abas do Comercial
        renomear = {'dashboard-comercial': 'BI Comercial', 'pedidos-comercial-mi': 'Comercial MI'}
        mudou = False
        for slug, nome in renomear.items():
            aba = Tab.query.filter_by(slug=slug).first()
            if aba and (aba.name_pt, aba.name_es) != (nome, nome):
                aba.name_pt = aba.name_es = nome
                mudou = True
        if mudou:
            db.session.commit()
        # a aba 'Dashboard Tempo Real' saiu da Producao
        aba_dash = Tab.query.filter_by(slug='dashboard-tempo-real').first()
        if aba_dash:
            aba_dash.sectors = []
            db.session.delete(aba_dash)
            db.session.commit()
        # o dashboard de materia-prima fica dentro da Programacao: remove a aba avulsa criada antes
        aba_mp = Tab.query.filter_by(slug='planejamento-mp').first()
        if aba_mp:
            aba_mp.sectors = []
            db.session.delete(aba_mp)
            db.session.commit()
        from app.desenvolvimento.routes import garantir_abas
        garantir_abas()                                                  # abas do Desenvolvimento (Cronograma, Cotacoes, Amostras, Projetos)
        from app.indicadores.routes import garantir_aba as garantir_indicadores
        garantir_indicadores()                                           # aba Indicadores (Producao)
        # contas iniciais por setor (idempotente)
        from app.usuarios_padrao import criar_usuarios_padrao
        criar_usuarios_padrao()

    # atualizacao diaria (08:00) do faturamento TOTVS. O 'python run.py' (debug) abre 2 processos: so o filho
    # do reloader (WERKZEUG_RUN_MAIN) agenda. Em outro servidor, defina BI_AGENDADOR=1 no .env.
    if os.environ.get('WERKZEUG_RUN_MAIN') == 'true' or os.environ.get('BI_AGENDADOR') == '1':
        from app.totvs.servico import iniciar_agendador
        iniciar_agendador()

        def _aquecer_slide():                            # calcula o slide mensal ja na partida: a tela BI COMERCIAL abre sem esperar
            try:
                with app.app_context():
                    from app.totvs.routes import dados_do_slide
                    dados_do_slide()
            except Exception:  # noqa: BLE001
                pass
        threading.Thread(target=_aquecer_slide, daemon=True, name='aquecer-slide').start()
        from app.totvs.pedidos import iniciar_agendador as iniciar_pedidos
        iniciar_pedidos(app)      # Relatorio Pedidos: a cada 2 min
        from app.totvs.entrada import iniciar_agendador as iniciar_entrada
        iniciar_entrada(app)      # entrada de pedidos por dia (2024-2026): a cada 2 min
        from app.totvs.movimentos import iniciar_agendador as iniciar_movimentos
        iniciar_movimentos(app)   # pesagens da malharia (local 23) das OPs da Programacao: a cada 2 min
        from app.totvs.almoxarifado import iniciar_agendador as iniciar_almox
        iniciar_almox(app)        # materia-prima do almoxarifado (saldo): a cada 1 min
        from app.totvs.empenho import iniciar_agendador as iniciar_empenho
        iniciar_empenho(app)      # consumo de materia-prima das OPs da Programacao: a cada 10 min
        from app.totvs.almoxarifado import iniciar_agendador_kardex as iniciar_almox_kardex
        iniciar_almox_kardex(app)  # saidas para a producao (kardex, op. 2011) por mes: a cada 5 min
        from app.indicadores.servico import iniciar_agendador as iniciar_indicadores
        iniciar_indicadores(app)  # Indicadores: pecas do acabamento (transacoes 2030/2031) a cada 10 min
        from app.pcphub import iniciar_agendador as iniciar_pcphub
        iniciar_pcphub(app)       # Programacao ao vivo a partir do PCP Hub: a cada 5 min (precisa de PCPHUB_EMAIL/PCPHUB_PASSWORD)
        from app.relatorio_fat.agenda import iniciar_agendador as iniciar_envio_relatorio
        iniciar_envio_relatorio(app)   # Relatorio de Faturamento por e-mail, 1x por dia (08:30)

    return app
