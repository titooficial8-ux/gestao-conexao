"""Estrutura, composicao e gramatura dos artigos, direto do TOTVS (so leitura).

Para cada artigo da Programacao (Sequencias e fila) e dos Pedidos abertos:
  - product/v2/products/search (expand=classifications): estrutura (tipo 505), segmento (504) e composicao
    (classificacao cujo tipo tem 'COMPOS' no nome, ou o tipo indicado em ARTIGOS_TIPO_COMPOSICAO no .env);
  - production-order/v2/orders/search (expand=items): campos adicionais do produto (GRAMATURA, LARGURA, METRO IDEAL, e
    tambem ESTRUTURA / COMPOSICAO quando existirem la).
Resultado em artigos_totvs. A estrutura vale: 1) mapeamento manual, 2) TOTVS (aqui), 3) Relatorio Pedidos.
Diagnostico dos nomes: /movimentos-op/artigo-campos?cod=<codigo do artigo> (so administrador)."""
import os
import threading
import time
import traceback
from datetime import datetime, timedelta

from app import db
from app.models import ArtigoTotvs, FilaPreProgramacao, PedidoRelatorio, Sequencia
from app.totvs import api
from app.totvs.pedidos import CICLO, EMPRESA, OPS_URL, PRODUCTS_URL, _em_lotes, _paginar, configurado

INTERVALO_MIN = float(os.getenv('ARTIGOS_INTERVALO_MIN', '30'))
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
    c = (str(artigo or '').split() or [''])[0]
    return c if c.isdigit() else None


def _codigos_e_ops():
    cods, ops = set(), set()
    for a, op, st in db.session.query(Sequencia.artigo, Sequencia.op, Sequencia.status):
        if _cod(a):
            cods.add(_cod(a))
            if op and str(op).isdigit() and st not in ('FINALIZADO', 'CANCELADO'):
                ops.add(int(op))
    for (a,) in db.session.query(FilaPreProgramacao.artigo):
        if _cod(a):
            cods.add(_cod(a))
    for (c,) in db.session.query(PedidoRelatorio.cod_produto).filter(PedidoRelatorio.qt_saldo > 0):
        if _cod(c):
            cods.add(_cod(c))
    return cods, ops


def _tipo_nome(c: dict) -> str:
    return ' '.join(str(v) for k, v in c.items() if 'type' in k.lower() and isinstance(v, str)).upper()


def ler_classificacoes(prod: dict) -> dict:
    saida, cru = {}, []
    for c in prod.get('classifications') or []:
        tipo, nome = c.get('typeCode'), c.get('name') or c.get('description') or ''
        cru.append(f'{tipo}|{_tipo_nome(c)}|{c.get("code")}={nome}')
        if tipo == 504:
            saida['segmento'] = c.get('code')
        elif tipo == 505:
            saida['estrutura'] = nome
        elif (TIPO_COMPOSICAO and str(tipo) == TIPO_COMPOSICAO) or 'COMPOS' in _tipo_nome(c):
            saida['composicao'] = nome or c.get('code')
    saida['classificacoes'] = '; '.join(cru)[:4000]
    saida['descricao'] = (prod.get('productName') or prod.get('name') or '')[:200]
    return saida


def ler_campos_adicionais(item: dict) -> dict:
    saida, nomes = {}, []
    for f in item.get('productAdditionalField') or []:
        nome, valor = str(f.get('name') or '').upper(), f.get('value')
        nomes.append(f'{nome}={valor}')
        if nome.startswith('GRAMATURA'):
            saida['gramatura'] = _num(valor)
        elif nome.startswith('LARGURA'):
            saida['largura'] = _num(valor)
        elif nome.startswith('METRO IDEAL'):
            saida['metro_ideal'] = _num(valor)
        elif nome.startswith('ESTRUTURA') and valor:
            saida['estrutura_campo'] = str(valor)
        elif nome.startswith('COMPOSI') and valor:
            saida['composicao_campo'] = str(valor)
    saida['campos'] = '; '.join(nomes)[:4000]
    return saida


def buscar(cods: list, ops: list) -> dict:
    cli = api.TotvsModaClient()
    cli.autenticar()
    dados = {}
    for lote in _em_lotes([int(c) for c in cods]):
        for x in _paginar(cli, PRODUCTS_URL, dict(filter=dict(productCodeList=lote), option=dict(branchInfoCode=EMPRESA), expand='classifications')):
            dados.setdefault(str(x.get('productCode')), {}).update(ler_classificacoes(x))
    for lote in _em_lotes(ops):
        for op in _paginar(cli, OPS_URL, dict(filter=dict(branchCodeList=[EMPRESA], cycleCodeList=[CICLO], orderCodeList=lote), expand='items')):
            for it in op.get('items') or []:
                d = dados.setdefault(str(it.get('productCode')), {})
                for k, v in ler_campos_adicionais(it).items():
                    if v not in (None, '') and (k not in d or d[k] in (None, '')):
                        d[k] = v
    for d in dados.values():                       # campo adicional so entra quando a classificacao nao trouxe
        if d.get('estrutura_campo') and not d.get('estrutura'):
            d['estrutura'] = d['estrutura_campo']
        if d.get('composicao_campo') and not d.get('composicao'):
            d['composicao'] = d['composicao_campo']
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


def atualizar(app) -> bool:
    if not _trava.acquire(blocking=False):
        return False
    ESTADO.update(rodando=True, erro=None, msg='Lendo artigos no TOTVS...')
    try:
        with app.app_context():
            cods, ops = _codigos_e_ops()
            limite = datetime.utcnow() - timedelta(hours=VALIDADE_H)
            recentes = {a for (a,) in db.session.query(ArtigoTotvs.cod_produto).filter(ArtigoTotvs.atualizado_em > limite)}
            pendentes = sorted(cods - recentes)
            if not pendentes and not ops:
                ESTADO.update(msg='Artigos em dia', ultima=datetime.now().isoformat(timespec='seconds'))
                return True
            # as OPs abertas sempre entram (a gramatura vem delas); os artigos so quando vencidos
            dados = buscar(pendentes, sorted(ops))
            n = gravar({c: d for c, d in dados.items() if c in cods})
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
