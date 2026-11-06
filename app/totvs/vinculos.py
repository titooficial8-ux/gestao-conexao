"""Ligacao REAL pedido <-> OP: vem do relatorio de pedidos do TOTVS (colunas Nr. Cicloop / Nr. Op), exportado em CSV e importado no Relatorio Pedidos.
A API do TOTVS nao entrega essa amarracao (feita no PCPFP141); o relatorio entrega. Quando houver arquivo importado, ele vale mais que a regra de deducao."""
from __future__ import annotations

import csv
import io
from datetime import datetime

from app import db
from app.models import VinculoOpPedido


def _num(v) -> float:
    try:
        return float(str(v or '0').strip().replace('.', '').replace(',', '.') or 0)
    except ValueError:
        return 0.0


def _decodificar(dados: bytes) -> str:
    for enc in ('utf-8-sig', 'cp1252', 'latin-1'):
        try:
            return dados.decode(enc)
        except UnicodeDecodeError:
            continue
    return dados.decode('latin-1', errors='replace')


def importar_csv(dados: bytes, por: str | None = None) -> dict:
    """Le o CSV do relatorio de pedidos (separador ;) e troca o vinculo guardado. Levanta ValueError se o arquivo nao tiver as colunas esperadas."""
    texto = _decodificar(dados)
    linhas = list(csv.reader(io.StringIO(texto), delimiter=';'))
    if not linhas:
        raise ValueError('Arquivo vazio.')
    cab = [c.strip().lower() for c in linhas[0]]

    def col(*nomes):
        for n in nomes:
            if n in cab:
                return cab.index(n)
        return None
    i_ped, i_prod, i_cic, i_op = col('cod. pedido'), col('cod. produto'), col('nr. cicloop'), col('nr. op')
    i_est, i_fin, i_pen = col('qt. estimada'), col('qt. finalizada'), col('qt. pendenteop')
    if None in (i_ped, i_prod, i_cic, i_op):
        raise ValueError('Não achei as colunas Cod. Pedido, Cod. Produto, Nr. Cicloop e Nr. Op: use o CSV do relatório de pedidos do TOTVS (038).')
    agora = datetime.utcnow()
    novos, vistos = [], set()
    for r in linhas[1:]:
        if len(r) <= max(i_ped, i_prod, i_cic, i_op):
            continue
        ped, prod, cic, op = r[i_ped].strip(), r[i_prod].strip(), r[i_cic].strip(), r[i_op].strip()
        if not ped or not prod:
            continue
        chave = (ped, prod, op)
        if chave in vistos:
            continue
        vistos.add(chave)
        novos.append(VinculoOpPedido(cod_pedido=ped, cod_produto=prod, ciclo=cic or None, op=op or None,
                                     qt_estimada=_num(r[i_est]) if i_est is not None and len(r) > i_est else 0.0,
                                     qt_finalizada=_num(r[i_fin]) if i_fin is not None and len(r) > i_fin else 0.0,
                                     qt_pendenteop=_num(r[i_pen]) if i_pen is not None and len(r) > i_pen else 0.0,
                                     importado_em=agora, importado_por=por))
    if not novos:
        raise ValueError('Nenhuma linha de pedido no arquivo.')
    try:
        VinculoOpPedido.query.delete()
        db.session.bulk_save_objects(novos)
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return dict(linhas=len(novos), com_op=sum(1 for v in novos if v.op), em=agora)


def carregar_fixos():
    """({(pedido, produto): [(ciclo, op, estimada, finalizada, pendente)]}, data do relatorio) — lista vazia = o TOTVS diz que o pedido esta sem OP."""
    fixos, corte = {}, None
    try:
        for v in VinculoOpPedido.query.all():
            lst = fixos.setdefault((str(v.cod_pedido), str(v.cod_produto)), [])
            if v.op:
                lst.append((v.ciclo or '13', v.op, v.qt_estimada or 0, v.qt_finalizada or 0, v.qt_pendenteop or 0))
            if v.importado_em and (corte is None or v.importado_em.date() > corte):
                corte = v.importado_em.date()
    except Exception:  # noqa: BLE001
        db.session.rollback()
        return {}, None
    return fixos, corte


def resumo() -> dict | None:
    try:
        n = VinculoOpPedido.query.count()
        if not n:
            return None
        ult = db.session.query(db.func.max(VinculoOpPedido.importado_em)).scalar()
        return dict(linhas=n, com_op=VinculoOpPedido.query.filter(VinculoOpPedido.op.isnot(None)).count(), em=ult)
    except Exception:  # noqa: BLE001
        db.session.rollback()
        return None
