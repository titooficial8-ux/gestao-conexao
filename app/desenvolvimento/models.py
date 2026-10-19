"""Tabelas do setor de Desenvolvimento: cronograma de tarefas, cotacoes, projetos e cronograma de desenhos (colecao de campanhas)."""
from datetime import datetime

from app import db


class DevTarefa(db.Model):
    """Cronograma: o que cada pessoa do Desenvolvimento esta realizando."""
    __tablename__ = 'dev_tarefas'
    STATUS = ('A_FAZER', 'EM_ANDAMENTO', 'AGUARDANDO', 'CONCLUIDO')

    id = db.Column(db.Integer, primary_key=True)
    titulo = db.Column(db.String(200), nullable=False)
    descricao = db.Column(db.Text)
    projeto = db.Column(db.String(120))
    responsavel_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    inicio = db.Column(db.Date)
    prazo = db.Column(db.Date)
    status = db.Column(db.String(15), nullable=False, default='A_FAZER')
    prioridade = db.Column(db.String(10), nullable=False, default='MEDIA')
    progresso = db.Column(db.Integer, default=0)
    criado_por_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)
    atualizado_em = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    concluido_em = db.Column(db.DateTime)

    responsavel = db.relationship('User', foreign_keys=[responsavel_id])


class Cotacao(db.Model):
    """Cotacao pedida pelo Comercial e respondida pelo Desenvolvimento, com aprovacao do Comercial."""
    __tablename__ = 'dev_cotacoes'
    # fluxo: SOLICITADA -> EM_ANALISE -> COTADA -> (APROVADA | REPROVADA | ALTERACAO -> EM_ANALISE ...) ; CANCELADA em qualquer ponto
    STATUS = ('SOLICITADA', 'EM_ANALISE', 'COTADA', 'APROVADA', 'REPROVADA', 'ALTERACAO', 'CANCELADA')

    id = db.Column(db.Integer, primary_key=True)
    cliente = db.Column(db.String(150), nullable=False)
    artigo = db.Column(db.String(250), nullable=False)
    quantidade = db.Column(db.Float)
    unidade = db.Column(db.String(10), default='mts')
    prazo_desejado = db.Column(db.Date)
    observacoes = db.Column(db.Text)
    status = db.Column(db.String(15), nullable=False, default='SOLICITADA')
    solicitante_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    responsavel_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    valor_cotado = db.Column(db.Float)
    prazo_entrega = db.Column(db.Date)
    resposta = db.Column(db.Text)
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)
    atualizado_em = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    cotado_em = db.Column(db.DateTime)
    decidido_em = db.Column(db.DateTime)

    solicitante = db.relationship('User', foreign_keys=[solicitante_id])
    responsavel = db.relationship('User', foreign_keys=[responsavel_id])
    eventos = db.relationship('CotacaoEvento', backref='cotacao', order_by='CotacaoEvento.criado_em', cascade='all, delete-orphan')


class CotacaoEvento(db.Model):
    """Historico da cotacao: quem fez o que e quando (solicitacao, resposta, aprovacao, alteracao...)."""
    __tablename__ = 'dev_cotacao_eventos'

    id = db.Column(db.Integer, primary_key=True)
    cotacao_id = db.Column(db.Integer, db.ForeignKey('dev_cotacoes.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    acao = db.Column(db.String(30), nullable=False)
    texto = db.Column(db.Text)
    status_para = db.Column(db.String(15))
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship('User')


class DevProjeto(db.Model):
    __tablename__ = 'dev_projetos'
    STATUS = ('PLANEJADO', 'EM_ANDAMENTO', 'PAUSADO', 'CONCLUIDO')

    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(200), nullable=False)
    descricao = db.Column(db.Text)
    responsavel_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    inicio = db.Column(db.Date)
    prazo = db.Column(db.Date)
    status = db.Column(db.String(15), nullable=False, default='PLANEJADO')
    prioridade = db.Column(db.String(10), nullable=False, default='MEDIA')
    progresso = db.Column(db.Integer, default=0)
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)
    atualizado_em = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    responsavel = db.relationship('User', foreign_keys=[responsavel_id])


class CampanhaDesenho(db.Model):
    """Colecao de Campanhas - Cronograma de Desenhos: cada desenho de uma colecao/campanha, com responsavel e prazo."""
    __tablename__ = 'dev_campanha_desenhos'
    STATUS = ('A_FAZER', 'EM_CRIACAO', 'APROVACAO', 'APROVADO', 'SALVO')

    id = db.Column(db.Integer, primary_key=True)
    campanha = db.Column(db.String(150), nullable=False)
    desenho = db.Column(db.String(200), nullable=False)
    cliente = db.Column(db.String(150))
    responsavel_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    inicio = db.Column(db.Date)
    prazo = db.Column(db.Date)
    status = db.Column(db.String(15), nullable=False, default='A_FAZER')
    observacao = db.Column(db.Text)
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)
    atualizado_em = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    responsavel = db.relationship('User', foreign_keys=[responsavel_id])


class DevArquivo(db.Model):
    """Biblioteca de arquivos do Desenvolvimento: laudos, produtos tecnologicos (PDF e videos) e material institucional (post de agencia etc.)."""
    __tablename__ = 'dev_arquivos'

    id = db.Column(db.Integer, primary_key=True)
    secao = db.Column(db.String(20), nullable=False, index=True)       # laudos | produtos_pdf | produtos_video | institucional
    titulo = db.Column(db.String(200), nullable=False)
    descricao = db.Column(db.Text)
    categoria = db.Column(db.String(60))
    referencia = db.Column(db.String(200))                              # artigo / cliente ao qual o arquivo se refere
    nome_original = db.Column(db.String(255), nullable=False)
    caminho = db.Column(db.String(400), nullable=False)                 # relativo a instance/dev_arquivos
    tamanho = db.Column(db.BigInteger, default=0)
    mimetype = db.Column(db.String(120))
    enviado_por_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    enviado_em = db.Column(db.DateTime, default=datetime.utcnow)

    enviado_por = db.relationship('User', foreign_keys=[enviado_por_id])
