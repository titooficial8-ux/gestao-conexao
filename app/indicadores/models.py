from datetime import datetime

from app import db


class IndAcabamento(db.Model):
    """Peca que entrou no acabamento (transacao 2030/2031 do TOTVS): uma por peca, com a hora (Brasil), os metros e o kg."""
    __tablename__ = 'ind_acabamento'

    id = db.Column(db.Integer, primary_key=True)
    codigo = db.Column(db.Integer, unique=True, index=True)       # transactionCode
    operacao = db.Column(db.Integer)
    mudou = db.Column(db.DateTime, index=True)                    # lastchangeDate (horario do Brasil)
    produto = db.Column(db.Integer, index=True)
    metros = db.Column(db.Float, default=0.0)
    kg = db.Column(db.Float, nullable=True)                       # metros x gramatura x largura / 1000


class IndProduto(db.Model):
    """Gramatura e largura do produto no TOTVS (campos adicionais), para converter metros em kg."""
    __tablename__ = 'ind_produto'

    codigo = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(200))
    gramatura = db.Column(db.Float, nullable=True)
    largura = db.Column(db.Float, nullable=True)
    atualizado = db.Column(db.DateTime, default=datetime.utcnow)


class IndReprovaCru(db.Model):
    """Peca reprovada na revisao do cru (local 24): vem da lista de NCs (ncform) do SharePoint, uma linha por NC."""
    __tablename__ = 'ind_reprova_cru'

    id = db.Column(db.Integer, primary_key=True)
    sp_id = db.Column(db.Integer, unique=True, index=True)       # id do item no SharePoint
    data = db.Column(db.Date, index=True)                        # data informada pelo revisor (data1)
    maquina = db.Column(db.String(10))
    artigo = db.Column(db.String(30))
    op = db.Column(db.String(20))
    cliente = db.Column(db.String(120))
    metros = db.Column(db.Float, default=0.0)
    kg = db.Column(db.Float, default=0.0)
    turno = db.Column(db.Integer, nullable=True)                 # 1, 2 ou 3 (letra A, B ou C da NC)
    codigos = db.Column(db.String(80))                           # ex.: "R01,R06"
    defeito = db.Column(db.String(80))                           # nome do 1o codigo (tabela de defeitos)
    emitente = db.Column(db.String(80))
    criado = db.Column(db.DateTime)


class IndProdutoUn(db.Model):
    """Unidade e tipo do produto no TOTVS (para separar o tecido acabado, em metros, do resto do estoque)."""
    __tablename__ = 'ind_produto_un'

    codigo = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(200))
    unidade = db.Column(db.String(5))
    acabado = db.Column(db.Boolean, default=False)


class IndEstoqueItem(db.Model):
    """Peca (item de lote) no estoque de acabados / expedicao (relatorio 040: saldo 1 do lote). A hora em que ela aparece e a hora da entrada."""
    __tablename__ = 'ind_estoque_item'

    id = db.Column(db.Integer, primary_key=True)
    lote = db.Column(db.Integer, index=True)
    seq = db.Column(db.Integer)
    produto = db.Column(db.Integer, index=True)
    metros = db.Column(db.Float, default=0.0)
    visto_em = db.Column(db.DateTime, index=True)                 # 1a vez que apareceu com saldo (horario de Brasilia) = entrada
    saiu_em = db.Column(db.DateTime, nullable=True, index=True)   # quando o saldo zerou/sumiu = saida
    baseline = db.Column(db.Boolean, default=False)               # ja estava no estoque quando comecamos a acompanhar (nao conta como entrada)
    __table_args__ = (db.UniqueConstraint('lote', 'produto', 'seq', name='uq_ind_estoque_item'),)
