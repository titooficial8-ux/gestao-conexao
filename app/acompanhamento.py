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


def _hm(v):
    d = _local(v)
    return d.strftime('%d/%m, %H:%M') if d else ''


def _rl_item(x: dict) -> dict:
    """Peca avaliada pelo revisor (RL.PRODUCAO) no formato da tela. `liberacao` = o que o lider fez (pedido, analisando, liberado)."""
    lib = x.get('liberacao') or {}
    maq = x.get('maquina')
    em = _local(x.get('em'))
    return dict(id=x.get('id'), maquina=str(maq).zfill(2) if str(maq or '').isdigit() else str(maq or ''), op=str(x.get('op') or ''), artigo=str(x.get('artigo') or ''), peca=x.get('peca'),
                resultado=str(x.get('resultado') or '').upper(), status=str(x.get('status') or '').upper(), em=em, quando=em.strftime('%d/%m %H:%M') if em else '', turno=x.get('turno'),
                revisor=x.get('revisor') or '', setor=x.get('setor') or '', metros_reais=x.get('metrosReais'), metros_reprovados=x.get('metrosReprovados') or 0, metros_passou=x.get('metrosPassou'),
                diferenca=x.get('diferenca'), padrao=x.get('metragemPadrao'),
                defeitos=[f"{d.get('codigo', '')} {d.get('nome', '')}".strip() for d in (x.get('defeitos') or []) if isinstance(d, dict)],
                lib_status=str(lib.get('status') or '').upper(), pedido_em=_hm(lib.get('pedidoEm')), analisando_em=_hm(lib.get('analisandoEm')), analisando_por=lib.get('analisandoPor') or '',
                liberado_em=_hm(lib.get('liberadoEm')), liberado_por=lib.get('liberadoPor') or '')


def _cached(chave, ttl, fn):
    agora = time.time()
    v = _cache.get(chave)
    if v and agora - v['t'] < ttl:
        return v['r']
    try:
        r = dict(itens=fn(), erro=None)
    except Exception as e:  # noqa: BLE001
        r = dict(itens=(v['r']['itens'] if v else []), erro=str(e)[:200])
    _cache[chave] = dict(t=agora, r=r)
    return r


def liberacoes(status: str = 'pendente', de: str = None) -> dict:
    """Pecas com defeito que chamaram o lider (PEÇA REGISTRADA): pendente (aguardando/analisando), liberado ou todas."""
    def ler():
        p = dict(status=status)
        if de:
            p['de'] = de
        return [_rl_item(x) for x in _lista(chamar('/liberacoes', p))]
    return _cached(f'lib_{status}_{de}', 15, ler)


def rl_producao(maquina: int, de: str) -> dict:
    """Pecas avaliadas pelo revisor na maquina desde `de` (AAAA-MM-DD)."""
    def ler():
        return [_rl_item(x) for x in _lista(chamar('/rlproducao', {'maquina': maquina, 'de': de}))]
    return _cached(f'rl_{maquina}_{de}', 30, ler)
