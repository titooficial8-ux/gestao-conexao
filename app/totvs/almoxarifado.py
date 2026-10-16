"""Almoxarifado: relacao de MATERIA-PRIMA (so MP) com saldo no local 34, direto da API do TOTVS (somente leitura).

  product/balances/search -> produtos com saldo no estoque 34 (ALMOX_ESTOQUE no .env, padrao 34)
  product/products/search -> dados do produto (isRawMaterial, unidade, nome); so fica quem e materia-prima

Atualiza sozinho a cada ALMOX_INTERVALO_MIN minutos (padrao 10) e guarda em instance/totvs/almoxarifado.json.
"""
import json
import os
import threading
import time
import traceback
from datetime import datetime

from . import api

BASE_URL = api.BASE_URL
BALANCES_URL = f'{BASE_URL}/api/totvsmoda/product/v2/balances/search'
PRODUCTS_URL = f'{BASE_URL}/api/totvsmoda/product/v2/products/search'
EMPRESA = int(os.getenv('PEDIDOS_EMPRESA', '1'))
ESTOQUE = int(os.getenv('ALMOX_ESTOQUE', '34'))
INTERVALO_MIN = max(1, int(os.getenv('ALMOX_INTERVALO_MIN', '10')))
ARQUIVO = api.OUT_DIR / 'almoxarifado.json'
VERSAO = 1
ESTADO = dict(rodando=False, ultima=None, erro=None, msg='')
_trava = threading.Lock()


def configurado() -> bool:
    return bool(os.getenv('TOTVS_CLIENT_ID') and os.getenv('TOTVS_CLIENT_SECRET'))


def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0.0


def _paginar(cli, url, corpo, tam=1000):
    pag, itens = 1, []
    while True:
        r = cli.post(url, dict(corpo, pageSize=tam, page=pag))
        if r.status_code != 200:
            raise RuntimeError(f'{url.split("/v2/")[1]}: HTTP {r.status_code} {r.text[:200]}')
        j = r.json()
        itens += j.get('items') or []
        if not j.get('hasNext') or pag >= 100:
            return itens
        pag += 1


def ler() -> dict:
    try:
        return json.loads(ARQUIVO.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def _gravar(d: dict) -> None:
    ARQUIVO.parent.mkdir(parents=True, exist_ok=True)
    tmp = ARQUIVO.with_suffix('.tmp')
    tmp.write_text(json.dumps(d, ensure_ascii=False), encoding='utf-8')
    tmp.replace(ARQUIVO)


def buscar(cli=None, amostra: bool = False) -> dict:
    """Consulta o TOTVS. amostra=True: diagnostico (devolve exemplos crus das duas consultas, sem gravar)."""
    if cli is None:
        cli = api.TotvsModaClient()
        cli.autenticar()
    opcao = dict(balances=[dict(branchCode=EMPRESA, stockCodeList=[ESTOQUE])])
    saldos = _paginar(cli, BALANCES_URL, dict(filter=dict(hasStock=True, branchStockCode=EMPRESA, stockCode=ESTOQUE), option=opcao), tam=50 if amostra else 1000) if not amostra else \
        cli.post(BALANCES_URL, dict(filter=dict(hasStock=True, branchStockCode=EMPRESA, stockCode=ESTOQUE), option=opcao, page=1, pageSize=5)).json().get('items') or []
    por_prod = {}
    for x in saldos:
        for b in x.get('balances') or []:
            if b.get('stockCode') == ESTOQUE:
                por_prod[x.get('productCode')] = dict(nome=x.get('productName'), saldo=_num(b.get('stock')), disponivel=_num(b.get('stock')) - _num(b.get('salesOrder')),
                                                       entradas=_num(b.get('inputTransaction')), saidas=_num(b.get('outputTransaction')))
    codigos = sorted(c for c in por_prod if c is not None)
    produtos = {}
    for i in range(0, len(codigos), 200):
        for p in _paginar(cli, PRODUCTS_URL, dict(filter=dict(productCodeList=codigos[i:i + 200]), option=dict(branchInfoCode=EMPRESA)), tam=500):
            produtos[p.get('productCode')] = p
    if amostra:
        return dict(estoque=ESTOQUE, saldos_amostra=saldos[:3], produtos_amostra=list(produtos.values())[:3], n_saldos=len(saldos))
    itens = []
    for cod, v in por_prod.items():
        p = produtos.get(cod) or {}
        if not p.get('isRawMaterial'):
            continue
        itens.append(dict(codigo=cod, nome=(p.get('productName') or v['nome'] or '')[:120], unidade=p.get('measuredUnit'), saldo=round(v['saldo'], 3),
                          disponivel=round(v['disponivel'], 3), entradas=round(v['entradas'], 3), saidas=round(v['saidas'], 3),
                          minimo=_num(p.get('minimumStockAmount')), maximo=_num(p.get('maximumStockAmount')), ideal=_num(p.get('idealStockAmount')), bloqueado=bool(p.get('isBlocked'))))
    itens.sort(key=lambda x: x['nome'])
    return dict(versao=VERSAO, estoque=ESTOQUE, atualizado=datetime.now().isoformat(timespec='seconds'), itens=itens,
                n_com_saldo=len(por_prod), n_materia_prima=len(itens))


def atualizar() -> bool:
    if not _trava.acquire(blocking=False):
        return False
    ESTADO.update(rodando=True, erro=None, msg='Consultando o almoxarifado no TOTVS...')
    try:
        d = buscar()
        _gravar(d)
        ESTADO.update(ultima=d['atualizado'], msg=f"{d['n_materia_prima']} materias-primas no local {ESTOQUE}")
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        ESTADO.update(erro=str(e)[:300], msg='Falha ao consultar - mantive os dados anteriores')
    finally:
        ESTADO['rodando'] = False
        _trava.release()
    return ESTADO['erro'] is None


def iniciar_agendador(app) -> None:
    if not configurado():
        return

    def loop():
        time.sleep(30)
        while True:
            try:
                atualizar()
            except Exception:  # noqa: BLE001
                traceback.print_exc()
            time.sleep(INTERVALO_MIN * 60)
    threading.Thread(target=loop, daemon=True, name='totvs-almoxarifado').start()
