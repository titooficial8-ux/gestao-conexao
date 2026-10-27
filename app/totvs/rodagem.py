"""Historico de OPs por artigo, direto do TOTVS (so leitura): base para 'artigos que nao rodam ha N meses'.

production-order/v2/orders/search (ciclo 13, status 10/20/30: aguardando, em andamento e finalizada; expand=items) -> data da OP (insertDate),
cliente, produto e quantidade; product/v2/products/search (classificacao 506) -> codigo e nome do ARTIGO. Atualiza a cada RODAGEM_INTERVALO_MIN (2)."""
import json
import os
import threading
import time
import traceback
from datetime import datetime

from app import db
from app.models import ArtigoRodagem
from app.totvs import api
from app.totvs.artigos import artigos_de_produtos
from app.totvs.pedidos import CICLO, EMPRESA, OPS_URL, _paginar, configurado

INTERVALO_MIN = float(os.getenv('RODAGEM_INTERVALO_MIN', '2'))
ESTADO = dict(rodando=False, ultima=None, n_ops=0, n_artigos=0, msg='', erro=None)
_trava = threading.Lock()


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def buscar() -> dict:
    cli = api.TotvsModaClient()
    cli.autenticar()
    ops = _paginar(cli, OPS_URL, dict(filter=dict(branchCodeList=[EMPRESA], cycleCodeList=[CICLO], statusList=[10, 20, 30]), expand='items'))
    linhas, produtos = [], set()
    for o in ops:
        data = str(o.get('insertDate') or '')[:10]
        if not data:
            continue
        for it in o.get('items') or []:
            pc = it.get('productCode')
            if pc is None:
                continue
            produtos.add(pc)
            linhas.append((str(pc), data, _num(it.get('quantity')) or _num(o.get('quantity')), str(o.get('customerName') or '')[:60], str(o.get('orderCode'))))
    mapa = artigos_de_produtos(cli, produtos)
    por_artigo = {}
    for pc, data, qtd, cliente, op in linhas:
        cod, nome = mapa.get(pc, (pc, ''))
        d = por_artigo.setdefault(cod, dict(nome=nome, ops=[]))
        d['nome'] = d['nome'] or nome
        d['ops'].append([data, round(qtd, 1), cliente, op])
    ESTADO['n_ops'] = len(ops)
    return por_artigo


def gravar(por_artigo: dict) -> None:
    agora = datetime.utcnow()
    existentes = {a.cod_artigo: a for a in ArtigoRodagem.query.all()}
    for cod, d in por_artigo.items():
        a = existentes.pop(cod, None) or ArtigoRodagem(cod_artigo=cod)
        a.descricao = (d['nome'] or '')[:200]
        a.ops_json = json.dumps(sorted(d['ops'], key=lambda x: x[0]), ensure_ascii=False)
        a.atualizado_em = agora
        db.session.add(a)
    for velho in existentes.values():                  # artigo que sumiu do TOTVS
        db.session.delete(velho)
    db.session.commit()


def atualizar(app) -> bool:
    if not _trava.acquire(blocking=False):
        return False
    ESTADO.update(rodando=True, erro=None, msg='Lendo as OPs no TOTVS...')
    try:
        dados = buscar()
        if not dados:
            raise RuntimeError('O TOTVS nao devolveu OPs; mantive os dados anteriores.')
        with app.app_context():
            gravar(dados)
        ESTADO.update(ultima=datetime.now().isoformat(timespec='seconds'), n_artigos=len(dados), msg=f'{len(dados)} artigos')
    except Exception as e:  # noqa: BLE001
        try:
            with app.app_context():
                db.session.rollback()
        except Exception:  # noqa: BLE001
            pass
        traceback.print_exc()
        ESTADO.update(erro=str(e)[:300], msg='Falha ao ler as OPs - mantive os dados anteriores')
    finally:
        ESTADO['rodando'] = False
        _trava.release()
    return ESTADO['erro'] is None


def iniciar_agendador(app) -> None:
    if not configurado():
        return

    def loop():
        time.sleep(60)
        while True:
            try:
                atualizar(app)
            except Exception:  # noqa: BLE001
                traceback.print_exc()
            time.sleep(max(INTERVALO_MIN, 1) * 60)
    threading.Thread(target=loop, daemon=True, name='totvs-rodagem').start()
