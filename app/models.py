from datetime import datetime

from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

from app import db

user_sectors = db.Table(
    'user_sectors',
    db.Column('user_id', db.Integer, db.ForeignKey('users.id'), primary_key=True),
    db.Column('sector_id', db.Integer, db.ForeignKey('sectors.id'), primary_key=True),
)

user_hidden_tabs = db.Table(
    'user_hidden_tabs',
    db.Column('user_id', db.Integer, db.ForeignKey('users.id'), primary_key=True),
    db.Column('tab_id', db.Integer, db.ForeignKey('tabs.id'), primary_key=True),
)

tab_sectors = db.Table(
    'tab_sectors',
    db.Column('tab_id', db.Integer, db.ForeignKey('tabs.id'), primary_key=True),
    db.Column('sector_id', db.Integer, db.ForeignKey('sectors.id'), primary_key=True),
)

role_categories = db.Table(
    'role_categories',
    db.Column('role_id', db.Integer, db.ForeignKey('roles.id'), primary_key=True),
    db.Column('category_id', db.Integer, db.ForeignKey('categories.id'), primary_key=True),
)


class Role(db.Model):
    """Cargo do usuario: define em quais categorias ele pode editar/movimentar
    itens (a visualizacao das abas continua sendo pelo Setor, como antes)."""
    __tablename__ = 'roles'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)

    edit_categories = db.relationship('Category', secondary=role_categories, backref='editor_roles')


class Sector(db.Model):
    __tablename__ = 'sectors'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)


class User(UserMixin, db.Model):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    country = db.Column(db.String(2), default='BR')  # 'BR' ou 'GT'
    status = db.Column(db.String(20), default='pendente')  # pendente, ativo, bloqueado
    is_admin = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    reset_password_requested = db.Column(db.Boolean, default=False)
    reset_password_requested_at = db.Column(db.DateTime, nullable=True)
    role_id = db.Column(db.Integer, db.ForeignKey('roles.id'), nullable=True)
    verificado = db.Column(db.Boolean, default=False)  # selo azul de perfil verificado
    foto_versao = db.Column(db.Integer, default=0)   # >0 quando tem foto de perfil (muda a cada troca, evita cache)
    capa_versao = db.Column(db.Integer, default=0)   # idem, para a capa do perfil
    capa_x = db.Column(db.Integer, default=50)       # enquadramento da capa (0-100, % do deslocamento), ajustado arrastando
    capa_y = db.Column(db.Integer, default=50)
    segundos_acesso = db.Column(db.Integer, default=0)  # tempo ativo no site (aba visivel), soma dos pings
    selo_parabens_pendente = db.Column(db.Boolean, default=False)  # mostrar a mensagem de parabens do selo
    deve_trocar_senha = db.Column(db.Boolean, default=False)  # senha provisoria: obriga trocar no proximo acesso
    setor_id = db.Column(db.Integer, db.ForeignKey('sectors.id'), nullable=True)  # setor principal (so organizacao; o acesso e por 'sectors')

    sectors = db.relationship('Sector', secondary=user_sectors, backref='users')
    role = db.relationship('Role')
    setor = db.relationship('Sector', foreign_keys=[setor_id])
    hidden_tabs = db.relationship('Tab', secondary=user_hidden_tabs, backref='hidden_from')

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def has_sector(self, sector_name):
        return any(s.name == sector_name for s in self.sectors)

    def can_edit_category(self, category):
        if self.is_admin:
            return True
        return bool(self.role) and category in self.role.edit_categories


class Machine(db.Model):
    """Maquina da malharia. Por enquanto so guarda o numero; artigos, clientes
    e status (comum/plano/xpremium/etc) entram numa proxima etapa."""
    __tablename__ = 'machines'

    STATUS_ATIVA = 'ativa'
    STATUS_MANUTENCAO = 'manutencao'
    STATUS_SEM_PROGRAMACAO = 'sem_programacao'
    STATUS_INOPERANTE = 'inoperante'
    # status que nao contam como "maquina ativa" nas estatisticas
    STATUS_FORA_DE_OPERACAO = (STATUS_MANUTENCAO, STATUS_INOPERANTE)

    id = db.Column(db.Integer, primary_key=True)
    number = db.Column(db.Integer, unique=True, nullable=False)
    order = db.Column(db.Integer, nullable=True)
    status = db.Column(db.String(20), default=STATUS_ATIVA, nullable=False)

    capacities = db.relationship('MachineCapacity', backref='machine', order_by='MachineCapacity.estrutura')

    @property
    def esta_ativa(self):
        return self.status not in self.STATUS_FORA_DE_OPERACAO

    def aceita(self, estrutura, largura, gramatura):
        """Confere se essa maquina roda a estrutura numa dada largura/gramatura,
        de acordo com o banco de capacidade importado da planilha."""
        estrutura = (estrutura or '').strip().upper()
        for cap in self.capacities:
            if not cap.ativa or cap.estrutura != estrutura:
                continue
            larguras = {round(v, 2) for v in (cap.largura1, cap.largura2, cap.largura3) if v}
            if round(largura, 2) not in larguras:
                continue
            if cap.gramatura_min <= gramatura <= cap.gramatura_max:
                return True
        return False


class MachineCapacity(db.Model):
    """Uma linha do banco de capacidade: em qual estrutura/largura/gramatura
    uma maquina pode rodar (importado da planilha maquinas_capacidade.csv)."""
    __tablename__ = 'machine_capacities'

    id = db.Column(db.Integer, primary_key=True)
    machine_id = db.Column(db.Integer, db.ForeignKey('machines.id'), nullable=False)
    estrutura = db.Column(db.String(10), nullable=False)
    largura1 = db.Column(db.Float, default=0)
    largura2 = db.Column(db.Float, default=0)
    largura3 = db.Column(db.Float, default=0)
    gramatura_min = db.Column(db.Float, default=0)
    gramatura_max = db.Column(db.Float, default=0)
    ativa = db.Column(db.Boolean, default=False)


class Programacao(db.Model):
    """Um artigo destinado a uma maquina. Fica 'aceita' se a estrutura/largura/
    gramatura do artigo bate com uma capacidade ativa da maquina, ou 'rejeitada'
    (com o motivo) caso contrario."""
    __tablename__ = 'programacoes'

    id = db.Column(db.Integer, primary_key=True)
    machine_id = db.Column(db.Integer, db.ForeignKey('machines.id'), nullable=False)
    artigo = db.Column(db.String(120))
    estrutura = db.Column(db.String(10), nullable=False)
    largura = db.Column(db.Float, nullable=False)
    gramatura = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(20), default='aceita')  # aceita, rejeitada
    motivo_rejeicao = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    created_by_id = db.Column(db.Integer, db.ForeignKey('users.id'))

    machine = db.relationship('Machine')
    created_by = db.relationship('User')


class Sequencia(db.Model):
    """Uma sequencia de producao de verdade, trazida do PCP Hub (sistema
    real de PCP). Representa um artigo programado numa maquina."""
    __tablename__ = 'sequencias'

    id = db.Column(db.Integer, primary_key=True)
    machine_id = db.Column(db.Integer, db.ForeignKey('machines.id'), nullable=False)
    sequence_order = db.Column(db.Integer)
    artigo = db.Column(db.String(30))
    cliente = db.Column(db.String(120))
    op = db.Column(db.String(30))
    status = db.Column(db.String(30))  # PRODUZINDO, EM_SEQUENCIA, PCP (e FINALIZADO quando a pesagem do cru acaba)
    situacao = db.Column(db.String(20))  # automatico pelo TOTVS: PRODUZINDO, MALHARIA, ESTOQUE, PCP (ou FINALIZADA)
    total_pcs = db.Column(db.Integer, default=0)
    pecas_produzidas = db.Column(db.Integer, default=0)
    qt_solicitada = db.Column(db.Integer, default=0)
    qt_avulsa = db.Column(db.Integer, default=0)
    cod_segmento = db.Column(db.String(60))
    dt_cliente = db.Column(db.Date, nullable=True)
    production_month = db.Column(db.String(20), nullable=True)
    termino_malharia = db.Column(db.Date, nullable=True)
    estrutura = db.Column(db.String(60), nullable=True)
    region = db.Column(db.String(2))
    synced_at = db.Column(db.DateTime, default=datetime.utcnow)
    # previsao de termino pelas voltas/RPM da maquina (app/previsao.py)
    pecas_dia = db.Column(db.Float, nullable=True)            # pecas que a maquina entrega em 24 h com este artigo (ja com a ineficiencia)
    termino_calculado = db.Column(db.Boolean, default=False)  # termino_malharia calculado aqui (o PCP Hub nao sobrescreve)
    prev_fonte = db.Column(db.String(30), nullable=True)      # de onde vieram as voltas/RPM: OP, maquina+artigo ou artigo
    prev_rpm = db.Column(db.Float, nullable=True)
    prev_voltas = db.Column(db.Float, nullable=True)

    machine = db.relationship('Machine', backref='sequencias')

    @property
    def pc_produzir(self):
        return max((self.total_pcs or 0) - (self.pecas_produzidas or 0), 0)


class PedidoRelatorio(db.Model):
    """Um pedido do 'Relatorio Pedidos' (Relatorio 38 do PCP Hub). Usado pra
    cruzar OP + artigo com as Sequencias e preencher o Dt.Cliente delas."""
    __tablename__ = 'pedidos_relatorio'

    id = db.Column(db.Integer, primary_key=True)
    cod_pedido = db.Column(db.String(30))
    nome_cliente = db.Column(db.String(120))
    unidade = db.Column(db.String(120))   # filial/unidade (ex.: Ortobom Olinda); so aparece/pesquisa no Relatorio Pedidos
    dt_pedido = db.Column(db.Date, nullable=True)
    dt_prevfaturamento = db.Column(db.Date, nullable=True)
    dt_chegada30 = db.Column(db.Date, nullable=True)
    tp_situacao = db.Column(db.String(40))
    cod_produto = db.Column(db.String(30))
    ds_produto = db.Column(db.String(200))
    cod_segmento = db.Column(db.String(60))
    estrutura = db.Column(db.String(60))
    qt_solicitada = db.Column(db.Float, default=0)
    qt_faturada = db.Column(db.Float, default=0)
    qt_pendente = db.Column(db.Float, default=0)
    nr_cicloop = db.Column(db.String(30))
    nr_op = db.Column(db.String(30))
    ops_extra = db.Column(db.String(200))   # outras OPs abertas do mesmo cliente+artigo (so para pesquisar/mostrar no Relatorio Pedidos)
    qt_finalizada = db.Column(db.Float, default=0)
    qt_pendenteop = db.Column(db.Float, default=0)
    qt_afaturar = db.Column(db.Float, default=0)
    qt_saldo = db.Column(db.Float, default=0)
    region = db.Column(db.String(2))
    synced_at = db.Column(db.DateTime, default=datetime.utcnow)


class ArticleClientMapping(db.Model):
    """Mapeamento manual artigo -> cliente (prioridade maxima), trazido do
    PCP Hub. Usado no preenchimento automatico do Novo Registro."""
    __tablename__ = 'article_client_mapping'

    id = db.Column(db.Integer, primary_key=True)
    cod_produto = db.Column(db.String(30), unique=True)
    nome_cliente = db.Column(db.String(120))


class ArticleEstruturaMapping(db.Model):
    """Mapeamento manual artigo -> estrutura (prioridade maxima sobre o
    relatorio de pedidos), trazido do PCP Hub."""
    __tablename__ = 'article_estrutura_mapping'

    id = db.Column(db.Integer, primary_key=True)
    article = db.Column(db.String(30), unique=True)
    estrutura = db.Column(db.String(60))


class ArticleClienteEspecial(db.Model):
    """Codigos de artigo que pertencem a um cliente especial (ORTOBOM,
    ECOFLEX), trazido do PCP Hub (ortobom_articles / ecoflex_articles)."""
    __tablename__ = 'article_cliente_especial'

    id = db.Column(db.Integer, primary_key=True)
    cod_produto = db.Column(db.String(30))
    cliente = db.Column(db.String(20))


class FilaPreProgramacao(db.Model):
    """Fila de pre-programacao (priority_queue do PCP Hub): OPs aguardando
    serem movidas para uma maquina, ou ja movidas (historico da fila)."""
    __tablename__ = 'fila_pre_programacao'

    id = db.Column(db.Integer, primary_key=True)
    op = db.Column(db.String(30))
    artigo = db.Column(db.String(30))
    cliente = db.Column(db.String(120))
    requested_quantity = db.Column(db.Float, default=0)
    total_pcs = db.Column(db.Integer, default=0)
    status = db.Column(db.String(20))  # WAITING, MOVED
    moved_to_machine = db.Column(db.String(10), nullable=True)
    moved_at = db.Column(db.DateTime, nullable=True)
    is_avulsa = db.Column(db.Boolean, default=False)
    cod_segmento = db.Column(db.String(60))
    production_month = db.Column(db.String(20), nullable=True)
    dt_prevfaturamento = db.Column(db.String(20), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    synced_at = db.Column(db.DateTime, default=datetime.utcnow)


class HistoricoProducao(db.Model):
    """Historico real de mudancas de status de producao (production_history
    do PCP Hub): quando uma OP virou PRODUZINDO, FINALIZADO, EXCLUIDO, etc."""
    __tablename__ = 'historico_producao'

    id = db.Column(db.Integer, primary_key=True)
    machine = db.Column(db.String(10))
    date = db.Column(db.Date, nullable=True)
    artigo = db.Column(db.String(30))
    cliente = db.Column(db.String(120))
    op = db.Column(db.String(30))
    quantity_pieces = db.Column(db.Integer, default=0)
    requested_quantity = db.Column(db.Integer, default=0)
    pieces_per_day = db.Column(db.Integer, default=0)
    old_status = db.Column(db.String(30))
    new_status = db.Column(db.String(30))
    changed_at = db.Column(db.DateTime, nullable=True)
    reason = db.Column(db.String(255), nullable=True)
    estimated_end_date = db.Column(db.Date, nullable=True)
    client_date = db.Column(db.Date, nullable=True)
    total_pcs = db.Column(db.Integer, nullable=True)
    motivo_cancelamento = db.Column(db.String(500), nullable=True)   # justificativa (so o PCP preenche)
    justificado_por = db.Column(db.String(120), nullable=True)
    justificado_em = db.Column(db.DateTime, nullable=True)
    synced_at = db.Column(db.DateTime, default=datetime.utcnow)


class Report32Movement(db.Model):
    """Pesagem real da malharia (Relatorio 32 / LOCAL 23 do PCP Hub) — cada
    linha e uma peca pesada. E a fonte de dados real do Dashboard Tempo Real."""
    __tablename__ = 'report_32_movement'

    id = db.Column(db.Integer, primary_key=True)
    ciclo = db.Column(db.String(20))
    op = db.Column(db.String(30), index=True)
    nr_lote = db.Column(db.String(30))
    nr_item = db.Column(db.String(30))
    cod_artigo = db.Column(db.String(200))
    metro_padrao = db.Column(db.Float)
    gramatura = db.Column(db.Float)
    largura = db.Column(db.Float)
    local_code = db.Column(db.String(10))
    qt_movimento = db.Column(db.Float)
    qt_pesos = db.Column(db.Float)
    cod_maquina = db.Column(db.String(10), index=True)
    dt_real = db.Column(db.DateTime, index=True)
    synced_at = db.Column(db.DateTime, default=datetime.utcnow)


class ArticleDailyCycle(db.Model):
    """Historico diario arquivado por maquina/OP (ciclo 06h -> 06h), trazido
    do PCP Hub (article_daily_cycles). Usado no Historico por Artigo para o
    'Arquivo diario por maquina' e 'Pecas por dia'."""
    __tablename__ = 'article_daily_cycles'

    id = db.Column(db.Integer, primary_key=True)
    article = db.Column(db.String(200), index=True)
    machine = db.Column(db.String(10), index=True)
    op = db.Column(db.String(30))
    cycle_date = db.Column(db.Date, index=True)
    pieces = db.Column(db.Integer, default=0)
    meters = db.Column(db.Float, default=0)
    synced_at = db.Column(db.DateTime, default=datetime.utcnow)


class QualitySettings(db.Model):
    """Metas/capacidades editaveis do Controle de Producao (quality_settings
    do PCP Hub, linha unica). Editada pela tela via popover."""
    __tablename__ = 'quality_settings'

    id = db.Column(db.Integer, primary_key=True)
    meta_dia = db.Column(db.Float, default=13000)
    meta_turno = db.Column(db.Float, default=4334)
    media_padrao = db.Column(db.Float, default=48)
    cap_maquinas = db.Column(db.Integer, default=28)
    cap_pecas_turno = db.Column(db.Integer, default=36, nullable=True)
    cap_metros_turno = db.Column(db.Float, nullable=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_by_email = db.Column(db.String(120), nullable=True)


class SavedDrawing(db.Model):
    """Controle de 'desenho salvo' da Malharia (aba Saved Drawing do PCP
    Hub): toda OP que entra EM_SEQUENCIA ou PRODUZINDO numa maquina precisa
    ter o desenho confirmado como salvo no pendrive daquela maquina. Linhas
    sao criadas/removidas automaticamente com base na Sequencia atual."""
    __tablename__ = 'saved_drawings'

    id = db.Column(db.Integer, primary_key=True)
    machine = db.Column(db.String(10), index=True)
    op = db.Column(db.String(30))
    article = db.Column(db.String(60))
    client = db.Column(db.String(120), nullable=True)
    is_saved = db.Column(db.Boolean, default=False)
    saved_by_name = db.Column(db.String(120), nullable=True)
    saved_by_email = db.Column(db.String(120), nullable=True)
    saved_at = db.Column(db.DateTime, nullable=True)
    observation = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class SavedDrawingHistory(db.Model):
    """Marca que uma combinacao maquina+artigo ja teve o desenho salvo algum
    dia (historico trazido do PCP Hub + novas marcacoes feitas aqui)."""
    __tablename__ = 'saved_drawings_history'

    id = db.Column(db.Integer, primary_key=True)
    machine = db.Column(db.String(10), index=True)
    article = db.Column(db.String(60), index=True)
    source = db.Column(db.String(60), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class SavedDrawingSaveLog(db.Model):
    """Auditoria de toda vez que alguem marcou/desmarcou um desenho como
    salvo — usado na aba Historico do Saved Drawing."""
    __tablename__ = 'saved_drawings_save_log'

    id = db.Column(db.Integer, primary_key=True)
    machine = db.Column(db.String(10))
    op = db.Column(db.String(30))
    article = db.Column(db.String(60))
    client = db.Column(db.String(120), nullable=True)
    observation = db.Column(db.Text, nullable=True)
    saved_by_name = db.Column(db.String(120), nullable=True)
    saved_by_email = db.Column(db.String(120), nullable=True)
    saved_at = db.Column(db.DateTime, default=datetime.utcnow)
    action = db.Column(db.String(20), default='saved')


class BaseEstrutura(db.Model):
    """Base Estrutura do PCP Hub: metragem padrao (ds_metros) de cada artigo
    (ds_produto). Usada pra resolver a metragem padrao de um artigo em varios
    calculos (RPM/producao, divergencias de gramatura etc)."""
    __tablename__ = 'base_estrutura'

    id = db.Column(db.Integer, primary_key=True)
    ds_produto = db.Column(db.String(200), index=True)
    ds_metros = db.Column(db.String(30))
    updated_at = db.Column(db.DateTime, default=datetime.utcnow)


class ArtigoTotvs(db.Model):
    """Dados do artigo lidos do TOTVS (so leitura): estrutura, composicao, gramatura, largura, metro ideal e segmento.
    Preenchido por app/totvs/artigos.py para todos os artigos da Programacao e dos Pedidos."""
    __tablename__ = 'artigos_totvs'

    id = db.Column(db.Integer, primary_key=True)
    cod_produto = db.Column(db.String(30), unique=True, index=True)
    descricao = db.Column(db.String(200))
    estrutura = db.Column(db.String(60))
    composicao = db.Column(db.String(200))
    gramatura = db.Column(db.Float)
    largura = db.Column(db.Float)
    metro_ideal = db.Column(db.Float)
    segmento = db.Column(db.String(60))
    classificacoes = db.Column(db.Text)   # texto cru das classificacoes do TOTVS (para conferir os nomes)
    campos = db.Column(db.Text)           # nomes dos campos adicionais vistos (idem)
    atualizado_em = db.Column(db.DateTime, default=datetime.utcnow)


class ProducaoRef(db.Model):
    """Base de voltas e RPM (lista producaoDIARIA): para cada OP/maquina/artigo, as voltas da peca e o RPM da maquina.
    tempo da peca (min) = voltas / RPM. Importada da planilha em instance/producao_diaria.xlsx."""
    __tablename__ = 'producao_ref'

    id = db.Column(db.Integer, primary_key=True)
    op = db.Column(db.String(30), index=True)
    maquina = db.Column(db.Integer, index=True)
    artigo = db.Column(db.String(30), index=True)
    estrutura = db.Column(db.String(20))
    rpm = db.Column(db.Float)
    voltas = db.Column(db.Float)
    tam_volta = db.Column(db.Float)
    metros_peca = db.Column(db.Float)
    referencia = db.Column(db.DateTime)   # data do registro na base (o mais recente de cada maquina+artigo vale)


class MotivoInativo(db.Model):
    """Motivo informado pelo comercial para um cliente que parou de comprar ou um artigo que parou de rodar (aba Comercial MI).
    cliente: Preco, Prazo de Entrega, Qualidade, Desenvolvimento. artigo: Tirou de linha, Preco, Problema de qualidade, Desenvolvimento."""
    __tablename__ = 'motivos_inativos'
    __table_args__ = (db.UniqueConstraint('tipo', 'chave', name='uq_motivo_tipo_chave'),)

    id = db.Column(db.Integer, primary_key=True)
    tipo = db.Column(db.String(10), index=True)        # 'cliente' | 'artigo'
    chave = db.Column(db.String(160), index=True)      # cliente (nome em maiusculas) ou codigo do artigo
    nome = db.Column(db.String(200))
    motivo = db.Column(db.String(40))
    obs = db.Column(db.Text)
    desenho = db.Column(db.String(200))                # desenho/codigo da amostra ligado ao artigo
    usuario = db.Column(db.String(120))
    atualizado_em = db.Column(db.DateTime, default=datetime.utcnow)


class ArtigoRodagem(db.Model):
    """Historico de OPs de cada artigo (lido do TOTVS): serve para saber quais artigos pararam de rodar."""
    __tablename__ = 'artigos_rodagem'

    id = db.Column(db.Integer, primary_key=True)
    cod_artigo = db.Column(db.String(40), unique=True, index=True)
    descricao = db.Column(db.String(200))
    ops_json = db.Column(db.Text)                      # [[data da OP, quantidade (m), cliente, numero da OP], ...]
    atualizado_em = db.Column(db.DateTime, default=datetime.utcnow)


class GramaturaMetrosRule(db.Model):
    """Regra gramatura -> metros esperados (gramatura_metros_rules do PCP
    Hub). Usada pra detectar divergencia entre a gramatura do artigo e a
    metragem padrao cadastrada na Base Estrutura."""
    __tablename__ = 'gramatura_metros_rules'

    id = db.Column(db.Integer, primary_key=True)
    gramatura_min = db.Column(db.Float)
    gramatura_max = db.Column(db.Float)
    metros = db.Column(db.Float)


class NonWorkingDay(db.Model):
    """Dia em que as maquinas nao vao rodar (feriado, parada programada) —
    usado pelo botao 'Dias sem Producao' da Programacao pra recalcular as
    datas previstas das OPs pulando esses dias."""
    __tablename__ = 'non_working_days'

    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False, index=True)
    machine_id = db.Column(db.Integer, db.ForeignKey('machines.id'), nullable=True)
    comment = db.Column(db.String(255), nullable=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    machine = db.relationship('Machine')


class Category(db.Model):
    __tablename__ = 'categories'

    id = db.Column(db.Integer, primary_key=True)
    slug = db.Column(db.String(50), unique=True, nullable=False)
    name_pt = db.Column(db.String(100), nullable=False)
    name_es = db.Column(db.String(100), nullable=False)
    icon = db.Column(db.String(10), default='\U0001F4C1')
    order = db.Column(db.Integer, default=0)

    tabs = db.relationship('Tab', backref='category', order_by='Tab.order')


class Tab(db.Model):
    __tablename__ = 'tabs'

    id = db.Column(db.Integer, primary_key=True)
    category_id = db.Column(db.Integer, db.ForeignKey('categories.id'), nullable=False)
    slug = db.Column(db.String(60), unique=True, nullable=False)
    name_pt = db.Column(db.String(120), nullable=False)
    name_es = db.Column(db.String(120), nullable=False)
    icon = db.Column(db.String(10), default='●')
    icon_color = db.Column(db.String(20), default='#2f7fe0')
    is_new = db.Column(db.Boolean, default=False)
    order = db.Column(db.Integer, default=0)

    sectors = db.relationship('Sector', secondary=tab_sectors, backref='tabs')

    def visible_to(self, user):
        if not user.is_authenticated:
            return False
        if user.is_admin:
            return True
        # Gestao Sistema e exclusiva do Desenvolvedor (is_admin), nao importa o setor
        if self.category.slug == 'sistema':
            return False
        if self in user.hidden_tabs:  # aba escondida para esta pessoa (tela Usuarios e Acessos)
            return False
        allowed_ids = {s.id for s in self.sectors}
        user_sector_ids = {s.id for s in user.sectors}
        return bool(allowed_ids & user_sector_ids)


class TechTicket(db.Model):
    """Chamado do portal de Tecnologia e I.A (mesmos campos do PCP Hub)."""
    __tablename__ = 'tech_tickets'

    id = db.Column(db.Integer, primary_key=True)
    region = db.Column(db.String(2), nullable=False, default='BR')
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    requester_name = db.Column(db.String(150))
    requester_email = db.Column(db.String(150))
    notify_email = db.Column(db.String(150))
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    category = db.Column(db.String(40), nullable=False, default='OUTROS')
    sector = db.Column(db.String(60))
    area = db.Column(db.String(150))
    priority = db.Column(db.String(10), nullable=False, default='MEDIA')
    status = db.Column(db.String(15), nullable=False, default='ABERTO')
    assigned_to = db.Column(db.String(150))
    resolution = db.Column(db.Text)
    resolved_at = db.Column(db.DateTime)
    # fluxo "Desenvolver"
    dev_service = db.Column(db.String(60))
    dev_request_type = db.Column(db.String(40))
    dev_target = db.Column(db.String(150))
    # nomes dos arquivos salvos em instance/uploads/chamados, separados por '|'
    images = db.Column(db.Text, default='')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = db.relationship('User')

    @property
    def image_list(self):
        return [i for i in (self.images or '').split('|') if i]


class PesagemMalharia(db.Model):
    """Pecas pesadas no local 23 (MALHARIA PESAGEM) de cada OP, atualizadas sozinhas pela API do TOTVS."""
    __tablename__ = 'pesagens_malharia'

    id = db.Column(db.Integer, primary_key=True)
    op = db.Column(db.String(30), index=True)
    ciclo = db.Column(db.Integer, default=13)
    item = db.Column(db.Integer)                 # numero da peca (ordem de pesagem)
    data = db.Column(db.Date, nullable=True)
    sequencia = db.Column(db.Integer)            # ordem do movimento no dia (TOTVS)
    acerto = db.Column(db.Float)                 # metros acima/abaixo do metro padrao
    metros = db.Column(db.Float, nullable=True)  # metro padrao + acerto
    visto_em = db.Column(db.DateTime, nullable=True)   # quando o Gestao viu a pesagem no TOTVS (reserva, se nao houver a hora real)
    hora_real = db.Column(db.DateTime, nullable=True)  # hora da baixa de fio da peca (transacao 2000 do TOTVS), horario do Brasil
    hora_origem = db.Column(db.String(10), nullable=True)  # 'trx' = hora confiavel; 'tardia' = baixa feita em outro dia; 'sem' = nao achei
    trx_codigo = db.Column(db.Integer, nullable=True)  # transacao 2000 ligada a peca
    peso_kg = db.Column(db.Float, nullable=True)  # kg da baixa de fio ligada a peca
    synced_at = db.Column(db.DateTime, default=datetime.utcnow)


class BaixaFio(db.Model):
    """Baixa de fio (transacao 2000 do TOTVS): uma por peca pesada no local 23, com a hora exata e o kg. E a fonte das pecas do Controle de Producao."""
    __tablename__ = 'baixas_fio'

    id = db.Column(db.Integer, primary_key=True)
    codigo = db.Column(db.Integer, unique=True, index=True)      # transactionCode
    data = db.Column(db.Date, nullable=True)                     # transactionDate
    mudou = db.Column(db.DateTime, index=True)                   # lastchangeDate (horario do Brasil): hora da pesagem
    cliente_cod = db.Column(db.Integer, nullable=True)
    kg = db.Column(db.Float, default=0.0)
    composicao = db.Column(db.String(200), nullable=True)        # codigos dos fios da baixa (identifica o artigo)
    casada = db.Column(db.Boolean, default=False)                # ligada a um movimento 23->24 (OP e metros conhecidos)
    op = db.Column(db.String(30), nullable=True)
    metros = db.Column(db.Float, nullable=True)
    metro_padrao = db.Column(db.Float, nullable=True)
    artigo_desc = db.Column(db.String(200), nullable=True)
    maquina = db.Column(db.Integer, nullable=True)
    cliente_nome = db.Column(db.String(120), nullable=True)
    gramatura = db.Column(db.Float, nullable=True)               # GRAMATURA M2 do artigo (campo adicional do produto no TOTVS)
    largura = db.Column(db.Float, nullable=True)                 # LARGURA PADRAO


class PesagemResumo(db.Model):
    """Resumo por OP do local 23 (quanto ainda esta no local, quanto ja saiu) e quando foi atualizado."""
    __tablename__ = 'pesagens_malharia_resumo'

    id = db.Column(db.Integer, primary_key=True)
    op = db.Column(db.String(30), unique=True, index=True)
    ciclo = db.Column(db.Integer, default=13)
    encontrada = db.Column(db.Boolean, default=True)
    maquina = db.Column(db.Integer, nullable=True)
    artigo_desc = db.Column(db.String(200), nullable=True)   # descricao completa do artigo no TOTVS (gramatura e largura saem daqui)
    gramatura = db.Column(db.Float, nullable=True)           # GRAMATURA M2 (campo adicional do produto na OP do TOTVS)
    largura = db.Column(db.Float, nullable=True)             # LARGURA PADRAO
    metro_ideal = db.Column(db.Float, nullable=True)         # METRO IDEAL LOTE = metro padrao do PCP Hub
    metro_padrao = db.Column(db.Float, nullable=True)
    cliente_cod = db.Column(db.Integer, nullable=True)       # codigo do cliente da OP no TOTVS: a baixa de fio da peca e do mesmo cliente
    pecas = db.Column(db.Integer, default=0)
    metros = db.Column(db.Float, nullable=True)
    no_local = db.Column(db.Float, default=0)
    saiu = db.Column(db.Float, default=0)
    quantidade_op = db.Column(db.Float, default=0)
    atualizado_em = db.Column(db.DateTime, default=datetime.utcnow)
