"""RPM REAL das ultimas 24 h de cada maquina: pecas que realmente passaram do local 23 para o 24 nas ultimas 24 h x voltas de cada peca / 1440 min.
E o RPM 'efetivo' da maquina (ja inclui paradas, trocas e baixa eficiencia), para comparar com o RPM de cadastro x eficiencia que a previsao usa."""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone

_cache = {'t': 0.0, 'dados': {}}


def por_maquina() -> dict:
    """{numero da maquina (str): dict(pecas, pecas_com_voltas, voltas, rpm, metros)} das ultimas 24 h; guardado por 60 s."""
    if time.time() - _cache['t'] < 60 and _cache['dados'] is not None:
        return _cache['dados']
    from app.main.routes import _movimentos_atual, _preparar_resposta
    from app.models import Sequencia
    hub, api_txt = _movimentos_atual()
    _e, resp = _preparar_resposta(hub, api_txt)
    corte = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=24)
    voltas_op = {str(s.op): float(s.prev_voltas) for s in Sequencia.query.filter(Sequencia.prev_voltas.isnot(None)).all() if s.op and s.prev_voltas}
    out = {}
    for x in json.loads(resp['plano']):
        try:
            d = datetime.fromisoformat(str(x.get('dt_real')).replace('Z', '+00:00'))
        except ValueError:
            continue
        d = d.astimezone(timezone.utc).replace(tzinfo=None) if d.tzinfo else d
        if d < corte:
            continue
        m = str(x.get('cod_maquina') or '').lstrip('0')
        if not m:
            continue
        o = out.setdefault(m, dict(pecas=0, pecas_com_voltas=0, voltas=0.0, metros=0.0))
        o['pecas'] += 1
        o['metros'] += float(x.get('qt_movimento') or 0)
        v = voltas_op.get(str(x.get('op')))
        if v:
            o['pecas_com_voltas'] += 1
            o['voltas'] += v
    for o in out.values():
        o['rpm'] = round(o['voltas'] / 1440, 2) if o['pecas_com_voltas'] else None
        o['metros'] = round(o['metros'])
    _cache.update(t=time.time(), dados=out)
    return out


def da_maquina(numero) -> dict | None:
    try:
        return por_maquina().get(str(int(numero)))
    except Exception:  # noqa: BLE001
        return None
