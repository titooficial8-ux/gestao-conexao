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


def aplicar_agora() -> dict:
    """Aplica a ligacao importada NA HORA nas linhas do Relatorio Pedidos (sem esperar a busca lenta no TOTVS) e acerta a Dt.Cliente das OPs da Programacao.
    Pedido com OP no relatorio recebe essa OP; OP que o relatorio deu a um pedido sai de qualquer outro; pedido que o relatorio mostra sem OP fica sem OP."""
    from collections import defaultdict
    from app.models import PedidoRelatorio
    fixos, _corte = carregar_fixos()
    if not fixos:
        return dict(ajustadas=0, dt_cliente=0)
    reservadas = {str(e[1]) for lst in fixos.values() for e in lst}
    por_chave = defaultdict(list)
    linhas = PedidoRelatorio.query.all()
    for r in linhas:
        por_chave[(str(r.cod_pedido), str(r.cod_produto))].append(r)
    ajustadas, colunas = 0, [c.name for c in PedidoRelatorio.__table__.columns if c.name != 'id']
    for chave, lst in fixos.items():
        rs = por_chave.get(chave)
        if not rs:
            continue
        for i, r in enumerate(rs):
            novo = lst[i] if i < len(lst) else None
            op, cic = (str(novo[1]), str(novo[0])) if novo else ('', '')
            if (r.nr_op or '') != op or (r.nr_cicloop or '') != cic:
                r.nr_op, r.nr_cicloop, ajustadas = op, cic, ajustadas + 1
            if novo:
                r.qt_pendenteop, r.qt_finalizada = novo[4] or r.qt_pendenteop, novo[3] if novo[3] else r.qt_finalizada
        for novo in lst[len(rs):]:                                            # o relatorio tem mais OPs que linhas: cria a linha da OP
            base = rs[0]
            nova = PedidoRelatorio(**{c: getattr(base, c) for c in colunas})
            nova.nr_op, nova.nr_cicloop, nova.qt_pendenteop, nova.qt_finalizada = str(novo[1]), str(novo[0]), novo[4] or 0, novo[3] or 0
            db.session.add(nova)
            ajustadas += 1
    for chave, rs in por_chave.items():                                       # OP reservada para outro pedido nao pode ficar aqui
        if chave in fixos:
            continue
        for r in rs:
            if r.nr_op and str(r.nr_op) in reservadas:
                r.nr_op, r.nr_cicloop, ajustadas = '', '', ajustadas + 1
    db.session.commit()
    from app.totvs.pedidos import sincronizar_dt_cliente
    return dict(ajustadas=ajustadas, dt_cliente=sincronizar_dt_cliente())
