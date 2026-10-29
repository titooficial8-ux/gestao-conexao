"""Leitura da API do Acompanhamento de Producao (ocorrencias das maquinas e do revisor: parada, manutencao, amostra, setup).

No .env da VPS (o token e o mesmo estilo dos setups; so leitura):
    ACOMP_URL=http://108.165.179.201:8090/api/v1        (opcional: usa SETUPS_URL se nao houver)
    ACOMP_TOKEN=<token Bearer da API>                    (opcional: usa SETUPS_TOKEN/SETUPS_PIN se nao houver)
"""
import os
import time
from datetime import datetime, timedelta

import requests

_cache = {}


def _url() -> str:
    return (os.getenv('ACOMP_URL') or os.getenv('SETUPS_URL') or 'http://108.165.179.201:8090/api/v1').rstrip('/')


def _token() -> str:
    return (os.getenv('ACOMP_TOKEN') or os.getenv('SETUPS_TOKEN') or os.getenv('SETUPS_PIN') or '').strip()


def configurado() -> bool:
    return bool(_token())


def chamar(caminho: str = '', params: dict = None):
    r = requests.get(f'{_url()}{caminho}', params=params, headers={'Accept': 'application/json', 'Authorization': f'Bearer {_token()}'}, timeout=(5, 20))
    if r.status_code != 200:
        raise RuntimeError(f'HTTP {r.status_code}: {r.text[:160]}')
    try:
        return r.json()
    except ValueError:
        raise RuntimeError('a API devolveu uma pagina em vez de JSON (servidor do Acompanhamento fora do ar?)')


def _lista(j) -> list:
    if isinstance(j, list):
        return j
    if isinstance(j, dict):
        for k in ('ocorrencias', 'items', 'data', 'results', 'dados', 'lista'):
            if isinstance(j.get(k), list):
                return j[k]
        for v in j.values():
            if isinstance(v, list) and (not v or isinstance(v[0], dict)):
                return v
    return []


def _local(v):
    """ISO da API -> datetime do horario de Brasilia (se vier com fuso, converte de UTC)."""
    if not v:
        return None
    try:
        d = datetime.fromisoformat(str(v).strip().replace('Z', '+00:00'))
    except ValueError:
        return None
    return (d - d.utcoffset()).replace(tzinfo=None) - timedelta(hours=3) if d.tzinfo else d


def abertas() -> dict:
    """Ocorrencias em andamento (cache de 15 s; se a API falhar, devolve a ultima leitura boa com o erro)."""
    agora = time.time()
    v = _cache.get('abertas')
    if v and agora - v['t'] < 15:
        return v['r']
    try:
        itens = _lista(chamar('/ocorrencias', {'status': 'andamento'}))
        r = dict(itens=[_normalizar(x) for x in itens], erro=None)
    except Exception as e:  # noqa: BLE001
        r = dict(itens=(v['r']['itens'] if v else []), erro=str(e)[:200])
    _cache['abertas'] = dict(t=agora, r=r)
    return r


def _normalizar(x: dict) -> dict:
    ini = _local(x.get('inicio'))
    mins = x.get('minutosAteAgora')
    if mins is None and ini:
        mins = max(int((datetime.now() - ini).total_seconds() // 60), 0)
    texto = ' '.join(str(x.get(k) or '') for k in ('tipo', 'motivo', 'maquina', 'abertaPor', 'setor', 'local', 'funcao', 'cargo')).lower()
    maq = x.get('maquina')
    revisor = 'revis' in texto or maq in (None, '', 0, '0')              # ocorrencia sem maquina = revisor (revisao cru)
    return dict(id=x.get('id'), maquina=str(maq).zfill(2) if str(maq or '').isdigit() and str(maq) != '0' else (str(maq) if maq else ''), tipo=str(x.get('tipo') or '').upper(), motivo=x.get('motivo') or '',
                inicio=ini.strftime('%H:%M') if ini else '', dia=ini.strftime('%d/%m') if ini else '', minutos=mins, aberta_por=x.get('abertaPor') or '', status=x.get('status') or '',
                turno=x.get('turnoInicio') or '', revisor=revisor, aguardando_peca=str(x.get('status') or '').upper() == 'AGUARDANDO_1A_PECA')
