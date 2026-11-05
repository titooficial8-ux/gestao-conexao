"""Regras do Saved Drawing (desenho salvo no pendrive da maquina) usadas fora da propria aba:
- o desenho de um artigo vale POR MAQUINA: se a combinacao maquina+artigo ja foi salva algum dia (historico do PCP Hub + marcacoes daqui), nao precisa salvar de novo;
- quando o PCP destina uma OP a uma maquina (Pre-Programacao), transfere de maquina, ou o TOTVS coloca a OP em MALHARIA/ESTOQUE e o artigo NAO esta salvo naquela maquina,
  o Gestao avisa na hora para o PCP comunicar o Desenvolvimento e ja deixa a OP pendente na aba Saved Drawing para a equipe aprovar."""
from __future__ import annotations

import re
from datetime import datetime

from app import db
from app.models import SavedDrawing, SavedDrawingHistory


def _base(article) -> str:
    return re.split(r'[\s-]', (article or '').strip().upper())[0]


def ja_salvo(machine, article) -> bool:
    """O artigo ja teve o desenho salvo nessa maquina (historico ou marcacao atual)?"""
    m, a = str(machine or '').strip(), _base(article)
    if not m or not a:
        return True                                           # sem maquina/artigo nao ha o que avisar
    if any(_base(h.article) == a for h in SavedDrawingHistory.query.filter_by(machine=m).all()):
        return True
    return any(_base(r.article) == a for r in SavedDrawing.query.filter_by(machine=m, is_saved=True).all())


def registrar_pendente(machine, op, article, cliente=None, por=None):
    """Garante a linha pendente na aba Saved Drawing (marcada com a data do aviso, para nao sumir enquanto a OP estiver ativa na fila)."""
    m, o, a = str(machine).strip(), str(op or '').strip(), (article or '').strip()
    r = next((x for x in SavedDrawing.query.filter_by(machine=m, op=o).all() if (x.article or '').strip().upper() == a.upper()), None)
    if r is None:
        r = SavedDrawing(machine=m, op=o, article=a, client=cliente, is_saved=False, aviso_em=datetime.utcnow(), aviso_por=por)
        db.session.add(r)
    elif not r.aviso_em and not r.is_saved:
        r.aviso_em, r.aviso_por = datetime.utcnow(), por
    return r


def alerta(machine, op, article, cliente=None, por=None):
    """None se o desenho ja esta salvo naquela maquina; senao registra a pendencia e devolve o texto do aviso para o PCP."""
    if ja_salvo(machine, article):
        return None
    registrar_pendente(machine, op, article, cliente, por)
    return (f'ATENÇÃO: o artigo {article} (OP {op}) ainda NÃO tem desenho salvo na máquina {machine}. '
            f'Comunique a equipe de Desenvolvimento (Maurício e Gustavo): a OP já está na aba Saved Drawing aguardando a aprovação deles.')
