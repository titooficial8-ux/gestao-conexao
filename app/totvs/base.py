"""Base do faturamento (equivale a aba "0 - Real - 25_FAT 150" da planilha de Receitas).

As colunas J:AL da planilha sao o Relatorio 150 do TOTVS (vem da API). As colunas A:I e AM:AO sao
calculadas aqui com as mesmas regras das formulas da planilha, usando as tabelas de apoio (Tab_aux).
"""
from __future__ import annotations

import json
from datetime import date
from functools import lru_cache
from pathlib import Path

SEED = Path(__file__).resolve().parents[1] / 'seed_data' / 'bi_comercial'


def _ler(nome):
    p = SEED / f'{nome}.json'
    return json.loads(p.read_text(encoding='utf-8')) if p.exists() else []


def _k(v):
    """Chave de procura igual ao PROCV do Excel: ignora maiusculas/minusculas."""
    return str(v).strip().lower() if v is not None else ''


def _col(linhas, i):
    return [r[i] if i < len(r) else None for r in linhas[1:]]


def _mapa(linhas, ki, vi):
    """PROCV exato: vale a PRIMEIRA ocorrencia da chave."""
    m = {}
    for k, v in zip(_col(linhas, ki), _col(linhas, vi)):
        if k not in (None, '') and _k(k) not in m:
            m[_k(k)] = v
    return m


@lru_cache(maxsize=1)
def tabelas():
    t = _ler('tab_aux')
    return dict(
        seg=_mapa(t, 0, 1),        # SEGMENTO (150) -> tipo (COLCHAO, COLCHAO M.E., ...)
        rep=_mapa(t, 3, 4),        # REPRE (150) -> representante
        grupo=_mapa(t, 6, 7),      # FANTASIA -> grupo de cliente (ex.: ORTOBOM)
        cli=_mapa(t, 12, 13),      # CLIENTE (150) -> fantasia (plano B do grupo)
        pais=_mapa(t, 9, 10),      # CLIENTE M.E. -> pais
    )


@lru_cache(maxsize=1)
def condicoes():
    t = _ler('controle_0164')
    out = {}
    for r in t[1:]:
        if r and r[0] is not None and str(r[0]) not in out:
            out[str(r[0])] = (r[3] if len(r) > 3 else None, r[6] if len(r) > 6 else None)
    return out


@lru_cache(maxsize=1)
def ajustes():
    p = SEED / 'ajustes.json'
    return json.loads(p.read_text(encoding='utf-8')) if p.exists() else {}


def _data(v):
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0.0


def derivar(r: dict) -> dict:
    """r: uma linha do Relatorio 150 (chaves = nomes das colunas). Devolve a linha com as colunas calculadas."""
    t = tabelas()
    desc = str(r.get('DESC.') or '')
    tipo = 'DEV' if 'DEV' in desc else 'VDA'                 # A: =IFERROR(MID(Q,FIND("DEV",Q),3),"VDA")
    d = _data(r.get('DATA'))
    seg = t['seg'].get(_k(r.get('SEGMENTO')), '#N/A')           # E
    over = {_k(k): v for k, v in (ajustes().get('segmento_por_fantasia') or {}).items()}.get(_k(r.get('FANTASIA')))
    if over and seg == 'COLCHÃO':
        seg = over                                              # reclassificacao manual (ex.: cliente M.E. faturado como MI)
    rep = '#N/A' if seg == '#N/A' else 'NADA'
    if seg == 'COLCHÃO' and r.get('REPRE'):                      # F
        rep = t['rep'].get(_k(r.get('REPRE')), '#N/A')
    grupo = t['grupo'].get(_k(r.get('FANTASIA')))                 # G
    if grupo is None:
        grupo = t['cli'].get(_k(r.get('CLIENTE')), '#N/A')
    ext = '#N/A' if seg == '#N/A' else 'NADA'
    if seg == 'COLCHÃO M.E.':                                    # I
        ext = t['pais'].get(_k(r.get('CLIENTE')), '#N/A')
    q, liq, frete = _num(r.get('QUANT')), _num(r.get('TOT.LIQUID')), _num(r.get('Vl. Freterat'))
    icms, cof, pis = _num(r.get('Vl. Icms')), _num(r.get('Vl. Vlr Cofins')), _num(r.get('Vl. Vlr Pis'))
    sinal = -1 if tipo == 'DEV' else 1
    cond = condicoes().get(str(r.get('COD.CLI')))
    return dict(
        tipo=tipo, mes=d.month if d else None, ano=d.year if d else None, data=d, seg=seg, rep=rep, grupo=grupo, ext=ext,
        fantasia=r.get('FANTASIA') or '', cliente=r.get('CLIENTE') or '', repre=r.get('REPRE') or '', uf=r.get('UF') or '',
        esp=str(r.get('ESP.') or '').strip(), nfe=r.get('NFE'), emp=r.get('Emp'), oper=r.get('DESC.') or '', artigo=r.get('ARTIGO') or '',
        cod_cli=r.get('COD.CLI'),
        quant_raw=q,                                             # V
        quant=sinal * q,                                         # AM
        liquida=sinal * (liq + frete - icms - cof - pis),        # AN
        bruta=sinal * (liq + frete),                             # AO
        cond=cond[0] if cond else None, prazo=cond[1] if cond else None,
    )
