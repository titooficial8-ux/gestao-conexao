"""Estrutura, composicao e gramatura dos artigos, direto do TOTVS (so leitura).

Para cada artigo da Programacao (Sequencias e fila) e dos Pedidos abertos:
  - product/v2/products/search (expand=classifications): estrutura (tipo 505), segmento (504) e composicao
    (classificacao cujo tipo tem 'COMPOS' no nome, ou o tipo indicado em ARTIGOS_TIPO_COMPOSICAO no .env);
  - production-order/v2/orders/search (expand=items): campos adicionais do produto (GRAMATURA, LARGURA, METRO IDEAL, e
    tambem ESTRUTURA / COMPOSICAO quando existirem la).
Resultado em artigos_totvs. A estrutura vale: 1) mapeamento manual, 2) TOTVS (aqui), 3) Relatorio Pedidos.
Diagnostico dos nomes: /movimentos-op/artigo-campos?cod=<codigo do artigo> (so administrador)."""
import os
import re
import threading
import unicodedata
import time
import traceback
from datetime import datetime, timedelta

from app import db
from app.models import ArtigoTotvs, FilaPreProgramacao, PedidoRelatorio, Sequencia
from app.totvs import api
from app.totvs.empenho import _qtd_item
from app.totvs.pedidos import CICLO, EMPRESA, OPS_URL, PRODUCTS_URL, _em_lotes, _paginar, configurado

INTERVALO_MIN = float(os.getenv('ARTIGOS_INTERVALO_MIN', '10'))
VALIDADE_H = float(os.getenv('ARTIGOS_VALIDADE_H', '6'))
TIPO_COMPOSICAO = os.getenv('ARTIGOS_TIPO_COMPOSICAO')           # typeCode da classificacao de composicao, se o nome nao bastar
ESTADO = dict(rodando=False, ultima=None, n=0, msg='', erro=None)
_trava = threading.Lock()


def _num(v):
    try:
        return float(str(v).replace(',', '.'))
    except (TypeError, ValueError):
        return None


def _cod(artigo):
    """Codigo do artigo como aparece na Programacao (primeiro 'pedaco' do texto, ex.: 2830026 ou 21876-A)."""
    m = re.match(r'\s*(\w[\w\-.]*)', str(artigo or ''))
    return m.group(1) if m else None


def _levantamento():
    """(artigos, OPs abertas, {OP: artigo}, produtos dos pedidos). Atencao: o codigo do ARTIGO da Programacao nao e o productCode do TOTVS:
    o artigo fica na classificacao 'CLAS ARTIGO' (tipo 506) do produto; a OP conhece os dois."""
    cods, ops, op_artigo, prod_pedidos = set(), set(), {}, set()
    for a, op, st in db.session.query(Sequencia.artigo, Sequencia.op, Sequencia.status):
        if _cod(a):
            cods.add(_cod(a))
            if op and str(op).isdigit() and st not in ('FINALIZADO', 'CANCELADO'):
                ops.add(int(op))
                op_artigo[str(op)] = _cod(a)
    for (a,) in db.session.query(FilaPreProgramacao.artigo):
        if _cod(a):
            cods.add(_cod(a))
    for (c,) in db.session.query(PedidoRelatorio.cod_produto).filter(PedidoRelatorio.qt_saldo > 0):
        if str(c or '').isdigit():
            prod_pedidos.add(str(c))                      # cod_produto do pedido e o productCode interno
    return cods, ops, op_artigo, prod_pedidos


def _codigos_e_ops():
    cods, ops, _, _ = _levantamento()
    return cods, ops


def _sem_acento(t: str) -> str:
    return unicodedata.normalize('NFKD', str(t or '')).encode('ascii', 'ignore').decode().upper().strip()


def nome_e_metragem_padrao(nome: str) -> bool:
    """Campo adicional que guarda a metragem padrao do artigo: 'METRO IDEAL LOTE', 'METRAGEM PADRAO', 'METRO PADRAO', 'METRAGEM IDEAL'..."""
    n = _sem_acento(nome)
    return ('METRO' in n or 'METRAGEM' in n or n.startswith('MTS') or n.startswith('MT ')) and any(k in n for k in ('IDEAL', 'PADRAO', 'LOTE'))


def _tipo_nome(c: dict) -> str:
    return ' '.join(str(v) for k, v in c.items() if 'type' in k.lower() and isinstance(v, str)).upper()


def ler_classificacoes(prod: dict) -> dict:
    saida, cru = {}, []
    for c in prod.get('classifications') or []:
        tipo, nome = c.get('typeCode'), c.get('name') or c.get('description') or ''
        cru.append(f'{tipo}|{_tipo_nome(c)}|{c.get("code")}={nome}')
        if tipo == 506:
            saida['codigo_artigo'] = str(c.get('code') or '').strip() or None
        if tipo == 504:
            saida['segmento'] = c.get('code')
        elif tipo == 505:
            saida['estrutura'] = nome
        elif (TIPO_COMPOSICAO and str(tipo) == TIPO_COMPOSICAO) or 'COMPOS' in _tipo_nome(c):
            saida['composicao'] = nome or c.get('code')
    saida['classificacoes'] = '; '.join(cru)[:4000]
    saida['descricao'] = (prod.get('productName') or prod.get('name') or '')[:200]
    for k, v in prod.items():                         # campos adicionais do proprio produto (quando a API devolve)
        if 'additional' in k.lower() and isinstance(v, list):
            extra = ler_campos_adicionais({'productAdditionalField': v})
            for kk, vv in extra.items():
                if vv not in (None, '') and kk not in saida:
                    saida[kk] = vv
    return saida


def gramatura_largura_do_nome(texto: str) -> dict:
    """Reserva: o nome do artigo no TOTVS traz '220G 2,20L' (gramatura e largura) quando o campo adicional nao existe."""
    g = re.search(r'(\d{2,3})\s*G\b', texto or '', re.I)
    l = re.search(r'(\d+[.,]\d+)\s*L\b', texto or '', re.I)
    saida = {}
    if g:
        saida['gramatura'] = float(g.group(1))
    if l:
        saida['largura'] = _num(l.group(1))
    return saida


def composicao_da_op(op: dict):
    """Composicao de fios do artigo a partir dos materiais (fios) da OP: 'FIO A 96% + FIO B 4%' (percentual pelo peso,
    quando a quantidade vem; senao so os nomes)."""
    mats = ((op.get('materials') or {}).get('orderMaterials')) or []
    itens = {}
    for m in mats:
        nome = (m.get('materialProductName') or str(m.get('materialProductCode') or '')).strip()
        if not nome:
            continue
        q = _qtd_item(m) or 0
        itens[nome] = itens.get(nome, 0) + q
    if not itens:
        return None
    total = sum(itens.values())
    if total > 0 and all(v > 0 for v in itens.values()):
        return ' + '.join(f'{n} {round(v / total * 100)}%' for n, v in sorted(itens.items(), key=lambda kv: -kv[1]))[:200]
    return ' + '.join(sorted(itens))[:200]


def ler_campos_adicionais(item: dict) -> dict:
    saida, nomes = {}, []
    for f in item.get('productAdditionalField') or []:
        nome, valor = _sem_acento(f.get('name')), f.get('value')
        nomes.append(f'{nome}={valor}')
        if nome.startswith('GRAMATURA'):
            saida['gramatura'] = _num(valor)
        elif nome.startswith('LARGURA'):
            saida['largura'] = _num(valor)
        elif nome_e_metragem_padrao(nome):
            saida['metro_ideal'] = _num(valor)
        elif nome.startswith('ESTRUTURA') and valor:
            saida['estrutura_campo'] = str(valor)
        elif nome.startswith('COMPOSI') and valor:
            saida['composicao_campo'] = str(valor)
    saida['campos'] = '; '.join(nomes)[:4000]
    return saida


def _produtos(cli, lote):
    base = dict(filter=dict(productCodeList=lote), option=dict(branchInfoCode=EMPRESA))
    try:                                              # tenta trazer tambem os campos adicionais do produto
        return _paginar(cli, PRODUCTS_URL, dict(base, expand='classifications,additionalFields'))
    except RuntimeError:
        return _paginar(cli, PRODUCTS_URL, dict(base, expand='classifications'))


def _ops(cli, lote):
    base = dict(filter=dict(branchCodeList=[EMPRESA], cycleCodeList=[CICLO], orderCodeList=lote))
    try:                                              # itens (campos adicionais) + materiais (fios) da OP
        return _paginar(cli, OPS_URL, dict(base, expand='items,materials'))
    except RuntimeError:
        return _paginar(cli, OPS_URL, dict(base, expand='items'))


def codigos_de_artigo(cli, codigos_internos) -> dict:
    """{productCode interno: codigo do artigo (classificacao 506)}."""
    saida = {}
    for lote in _em_lotes([int(c) for c in codigos_internos if str(c).isdigit()]):
        for x in _produtos(cli, lote):
            saida[str(x.get('productCode'))] = ler_classificacoes(x).get('codigo_artigo') or str(x.get('productCode'))
    return saida


def buscar(ops: list, op_artigo: dict, produtos_extra=()) -> dict:
    """Dados por CODIGO DE ARTIGO (como na Programacao). Os campos adicionais (gramatura, largura, metragem padrao, composicao)
    vem das OPs abertas; as classificacoes (estrutura, segmento) do produto, achado pelo productCode que a OP informa."""
    cli = api.TotvsModaClient()
    cli.autenticar()
    por_produto, artigo_do_produto = {}, {}
    for lote in _em_lotes(ops):
        for op in _ops(cli, lote):
            comp = composicao_da_op(op)
            art = op_artigo.get(str(op.get('orderCode')))
            for it in op.get('items') or []:
                pc = str(it.get('productCode'))
                d = por_produto.setdefault(pc, {})
                for k, v in ler_campos_adicionais(it).items():
                    if v not in (None, '') and (k not in d or d[k] in (None, '')):
                        d[k] = v
                if comp and not d.get('composicao_fios'):
                    d['composicao_fios'] = comp
                if art:
                    artigo_do_produto[pc] = art
    dados = {}
    internos = {int(c) for c in por_produto if c.isdigit()} | {int(c) for c in produtos_extra if str(c).isdigit()}
    vistos = set()
    for lote in _em_lotes(sorted(internos)):
        for x in _produtos(cli, lote):
            pc = str(x.get('productCode'))
            cl = ler_classificacoes(x)
            art = artigo_do_produto.get(pc) or cl.get('codigo_artigo')
            if not art:
                continue
            vistos.add(pc)
            d = dados.setdefault(art, {})
            d.update({k: v for k, v in cl.items() if v not in (None, '')})
            for k, v in por_produto.get(pc, {}).items():
                if v not in (None, ''):
                    d[k] = v
    for pc, campos in por_produto.items():              # produto que a consulta nao devolveu: guarda ao menos os campos da OP
        if pc not in vistos and artigo_do_produto.get(pc):
            dados.setdefault(artigo_do_produto[pc], {}).update({k: v for k, v in campos.items() if v not in (None, '')})
    for d in dados.values():                       # o que veio so de campo/OP entra quando a classificacao nao trouxe
        if d.get('estrutura_campo') and not d.get('estrutura'):
            d['estrutura'] = d['estrutura_campo']
        if d.get('composicao_campo') and not d.get('composicao'):
            d['composicao'] = d['composicao_campo']
        if d.get('composicao_fios') and not d.get('composicao'):
            d['composicao'] = d['composicao_fios']
        if d.get('gramatura') is None or d.get('largura') is None:     # reserva: gramatura/largura escritas no nome do artigo
            nome = ' '.join(filter(None, [d.get('descricao'), d.get('classificacoes')]))
            for k, v in gramatura_largura_do_nome(nome).items():
                if d.get(k) is None:
                    d[k] = v
    return dados


def gravar(dados: dict) -> int:
    agora = datetime.utcnow()
    existentes = {a.cod_produto: a for a in ArtigoTotvs.query.filter(ArtigoTotvs.cod_produto.in_(list(dados))).all()}
    for cod, d in dados.items():
        a = existentes.get(cod) or ArtigoTotvs(cod_produto=cod)
        for k in ('descricao', 'estrutura', 'composicao', 'gramatura', 'largura', 'metro_ideal', 'segmento', 'classificacoes', 'campos'):
            if d.get(k) not in (None, ''):
                setattr(a, k, d[k])
        a.atualizado_em = agora
        db.session.add(a)
    db.session.commit()
    return len(dados)


_ult_completa = 0.0
_limpo = False                                         # na 1a leitura de cada partida troca os dados antigos (chaveados pelo codigo errado) pelos novos


def atualizar(app) -> bool:
    global _ult_completa, _limpo
    if not _trava.acquire(blocking=False):
        return False
    ESTADO.update(rodando=True, erro=None, msg='Lendo artigos no TOTVS...')
    try:
        with app.app_context():
            cods, ops, op_artigo, prod_pedidos = _levantamento()
            completa = time.time() - _ult_completa > VALIDADE_H * 3600          # produtos dos pedidos (sem OP) so de vez em quando
            dados = buscar(sorted(ops), op_artigo, prod_pedidos if completa else ())
            if completa:
                _ult_completa = time.time()
            if dados and not _limpo:
                ArtigoTotvs.query.delete()
                db.session.commit()
                _limpo = True
            n = gravar(dados)
            ESTADO.update(n=n, msg=f'{n} artigos atualizados', ultima=datetime.now().isoformat(timespec='seconds'))
    except Exception as e:  # noqa: BLE001
        db.session.rollback()
        traceback.print_exc()
        ESTADO.update(erro=str(e)[:300], msg='Falha ao ler artigos - mantive os dados anteriores')
    finally:
        ESTADO.update(rodando=False)
        _trava.release()
    return ESTADO['erro'] is None


def iniciar_agendador(app) -> None:
    if not configurado():
        return

    def loop():
        time.sleep(40)
        while True:
            try:
                atualizar(app)
            except Exception:  # noqa: BLE001
                traceback.print_exc()
            time.sleep(max(INTERVALO_MIN, 1) * 60)
    threading.Thread(target=loop, daemon=True, name='totvs-artigos').start()


def dados_do_artigo(artigo):
    """ArtigoTotvs do artigo (primeiro codigo numerico do texto) ou None."""
    c = _cod(artigo)
    return ArtigoTotvs.query.filter_by(cod_produto=c).first() if c else None
