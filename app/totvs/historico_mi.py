"""Historico de pedidos de venda do mercado interno lido so do TOTVS (base de 'clientes que nao compram' do Comercial MI).

Primeira vez: le mes a mes desde 01/01/2023. Depois, a cada INTERVALO_MIN (2) refaz os ultimos RAPIDA_DIAS e, de tempos em tempos, os ultimos RECENTE_DIAS."""
import json
import os
import threading
import time
import traceback
from datetime import date, datetime, timedelta

from app.totvs import api, entrada

ANO_INICIAL = int(os.getenv('HISTORICO_MI_ANO_INICIAL', '2023'))
INTERVALO_MIN = float(os.getenv('HISTORICO_MI_INTERVALO_MIN', '2'))
RAPIDA_DIAS = 10
RECENTE_DIAS = 75
COMPLETA_MIN = 20
VERSAO = 1
ARQUIVO = api.OUT_DIR / 'historico_mi.json'
ESTADO = dict(rodando=False, ultima=None, n=0, msg='', erro=None)
_trava = threading.Lock()
_ult_completa = 0.0


def ler() -> dict:
    try:
        d = json.loads(ARQUIVO.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    return d if d.get('versao') == VERSAO else {}


def _gravar(d: dict) -> None:
    ARQUIVO.parent.mkdir(parents=True, exist_ok=True)
    tmp = ARQUIVO.with_suffix('.tmp')
    tmp.write_text(json.dumps(d, ensure_ascii=False), encoding='utf-8')
    api.trocar_arquivo(tmp, ARQUIVO)


def _buscar_itens(cli, ini: date, fim: date) -> list:
    """Pedidos do periodo COM os itens (para saber o segmento: COLCHAO MI, CALCADO...)."""
    out, pag = [], 1
    while True:
        r = cli.post(entrada.ORDERS_URL, dict(filter=dict(branchCodeList=[entrada.EMPRESA], startOrderDate=f'{ini}T00:00:00', endOrderDate=f'{fim}T23:59:59'), expand='items', pageSize=200, page=pag))
        if r.status_code != 200:
            raise RuntimeError(f'pedidos {ini}..{fim}: HTTP {r.status_code} {r.text[:200]}')
        j = r.json()
        out += j.get('items') or []
        if not j.get('hasNext'):
            return out
        pag += 1


def _segmentos(cli, produtos: set, cache: dict) -> None:
    """Classificacao 504 (segmento) de cada produto, guardada em cache ({productCode: segmento})."""
    from app.totvs.artigos import _em_lotes, _produtos
    faltam = [c for c in produtos if c and c != 'None' and c not in cache]
    for lote in _em_lotes([int(c) for c in faltam if c.isdigit()]):
        achados = set()
        for x in _produtos(cli, lote):
            achados.add(str(x.get('productCode')))
            cache[str(x.get('productCode'))] = next((str(c.get('code') or '').strip() for c in x.get('classifications') or [] if c.get('typeCode') == 504), '')
        for c in lote:
            cache.setdefault(str(c), '')


def _segmento_do_pedido(p: dict, cache: dict) -> str:
    """Segmento que domina o pedido (maior quantidade)."""
    soma = {}
    for it in p.get('items') or []:
        seg = cache.get(str(it.get('productCode'))) or ''
        soma[seg] = soma.get(seg, 0.0) + entrada._num(it.get('quantity'))
    return max(soma, key=soma.get) if soma else ''


def atualizar(rapida: bool = False) -> bool:
    global _ult_completa
    if not _trava.acquire(blocking=False):
        return False
    ESTADO.update(rodando=True, erro=None, msg='Lendo pedidos no TOTVS...')
    try:
        cli = api.TotvsModaClient()
        cli.autenticar()
        dados = ler()
        pedidos, fantasia = dados.get('pedidos', {}), dados.get('clientes', {})
        hoje = date.today()
        precisa_segmento = any(len(v) < 6 for v in pedidos.values())          # pedidos gravados antes do filtro de segmento: refaz tudo uma vez (os dados antigos continuam valendo ate terminar)
        segmentos = dados.get('segmentos', {})
        if not pedidos or precisa_segmento:
            ini = date(ANO_INICIAL, 1, 1)
        else:
            ini = hoje - timedelta(days=RAPIDA_DIAS if rapida else RECENTE_DIAS)
        for a, b in entrada._janelas(ini, hoje):
            ESTADO['msg'] = f'Pedidos de {a:%m/%Y}...'
            novos = _buscar_itens(cli, a, b)
            _segmentos(cli, {str(it.get('productCode')) for p in novos for it in p.get('items') or []}, segmentos)
            for k in [k for k, v in pedidos.items() if a.isoformat() <= v[0] <= b.isoformat()]:
                pedidos.pop(k)
            for p in novos:
                entra, local = entrada.classificar(p)
                if entra and local == 'MI' and p.get('orderDate'):
                    pedidos[str(p['orderCode'])] = [str(p['orderDate'])[:10], entrada._valor(p), round(entrada._num(p.get('quantity')), 2),
                                                    p.get('customerCode'), str(p.get('customerName') or '')[:60], _segmento_do_pedido(p, segmentos)]
        try:
            fantasia = entrada._fantasias(cli, {v[3] for v in pedidos.values()}, fantasia)
        except Exception:  # noqa: BLE001
            traceback.print_exc()
        _gravar(dict(versao=VERSAO, atualizado=datetime.now().isoformat(timespec='seconds'), pedidos=pedidos, clientes=fantasia, segmentos=segmentos))
        if not rapida:
            _ult_completa = time.time()
        ESTADO.update(ultima=datetime.now().isoformat(timespec='seconds'), n=len(pedidos), msg=f'{len(pedidos)} pedidos')
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        ESTADO.update(erro=str(e)[:300], msg='Falha ao ler os pedidos - mantive os dados anteriores')
    finally:
        ESTADO['rodando'] = False
        _trava.release()
    return ESTADO['erro'] is None


def iniciar_agendador(app=None) -> None:
    if not entrada.configurado():
        return

    def loop():
        time.sleep(40)
        while True:
            try:
                atualizar(rapida=time.time() - _ult_completa < COMPLETA_MIN * 60)
            except Exception:  # noqa: BLE001
                traceback.print_exc()
            time.sleep(max(INTERVALO_MIN, 1) * 60)
    threading.Thread(target=loop, daemon=True, name='totvs-historico-mi').start()
