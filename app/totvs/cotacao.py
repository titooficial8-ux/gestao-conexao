"""Dolar PTAX (venda) do Banco Central, para mostrar o valor dos pedidos de exportacao (ME) tambem em US$.
Cotacao do dia do pedido; se for fim de semana/feriado vale a do ultimo dia util anterior. Guarda em instance/totvs/ptax.json."""
import json
import threading
import time
from datetime import date, timedelta

import requests

from . import api

ARQUIVO = api.OUT_DIR / 'ptax.json'
URL = ('https://olinda.bcb.gov.br/olinda/servico/PTAX/versao/v1/odata/CotacaoDolarPeriodo(dataInicial=@dataInicial,dataFinalCotacao=@dataFinalCotacao)'
       "?@dataInicial='{ini}'&@dataFinalCotacao='{fim}'&$format=json&$select=cotacaoVenda,dataHoraCotacao")
_trava = threading.Lock()
_tentou = 0.0
_MEM = {}


def _ler() -> dict:
    global _MEM
    if not _MEM:
        try:
            _MEM = json.loads(ARQUIVO.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            _MEM = {}
    return _MEM


def _atualizar() -> None:
    """Baixa as cotacoes que faltam (do ultimo dia guardado, ou de 01/01 do ano anterior, ate hoje). Falha de rede nao derruba nada."""
    global _tentou, _MEM
    if time.time() - _tentou < 600 or not _trava.acquire(blocking=False):
        return
    _tentou = time.time()
    try:
        d = dict(_ler())
        hoje = date.today()
        ini = date.fromisoformat(max(d)) + timedelta(days=1) if d else date(hoje.year - 1, 1, 1)
        if ini > hoje:
            return
        r = requests.get(URL.format(ini=ini.strftime('%m-%d-%Y'), fim=hoje.strftime('%m-%d-%Y')), timeout=20)
        r.raise_for_status()
        for x in r.json().get('value', []):
            d[str(x['dataHoraCotacao'])[:10]] = round(float(x['cotacaoVenda']), 4)
        if d != _MEM:
            ARQUIVO.parent.mkdir(parents=True, exist_ok=True)
            tmp = ARQUIVO.with_suffix('.tmp')
            tmp.write_text(json.dumps(d), encoding='utf-8')
            api.trocar_arquivo(tmp, ARQUIVO)
            _MEM = d
    except Exception:  # noqa: BLE001
        pass
    finally:
        _trava.release()


def taxa(dia: str) -> float | None:
    """PTAX venda do dia 'AAAA-MM-DD' (ou do ultimo dia util antes). None se ainda nao houver cotacao guardada."""
    d = _ler()
    if not d or dia > max(d):
        _atualizar()
        d = _ler()
    if not d:
        return None
    ks = [k for k in d if k <= dia]
    return d[max(ks)] if ks else d[min(d)]


def para_usd(valor_brl: float, dia: str) -> float | None:
    t = taxa(dia)
    return round(valor_brl / t, 2) if t and valor_brl else (0.0 if t else None)
