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
