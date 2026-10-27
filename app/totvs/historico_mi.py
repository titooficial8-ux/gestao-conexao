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
        if not pedidos:
            ini = date(ANO_INICIAL, 1, 1)
        else:
            ini = hoje - timedelta(days=RAPIDA_DIAS if rapida else RECENTE_DIAS)
        for a, b in entrada._janelas(ini, hoje):
            ESTADO['msg'] = f'Pedidos de {a:%m/%Y}...'
            novos = entrada._buscar(cli, a, b)
            for k in [k for k, v in pedidos.items() if a.isoformat() <= v[0] <= b.isoformat()]:
                pedidos.pop(k)
            for p in novos:
                entra, local = entrada.classificar(p)
                if entra and local == 'MI' and p.get('orderDate'):
                    pedidos[str(p['orderCode'])] = [str(p['orderDate'])[:10], entrada._valor(p), round(entrada._num(p.get('quantity')), 2),
                                                    p.get('customerCode'), str(p.get('customerName') or '')[:60]]
        try:
            fantasia = entrada._fantasias(cli, {v[3] for v in pedidos.values()}, fantasia)
        except Exception:  # noqa: BLE001
            traceback.print_exc()
        _gravar(dict(versao=VERSAO, atualizado=datetime.now().isoformat(timespec='seconds'), pedidos=pedidos, clientes=fantasia))
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
