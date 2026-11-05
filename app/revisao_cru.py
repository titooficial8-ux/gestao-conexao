"""Revisao Cru (maquinas de revisao ao lado da 45): ao clicar no quadrado, mostra quem esta revisando e as pecas avaliadas hoje (RL.PRODUCAO do Acompanhamento de Producao)."""
from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timedelta

from flask import Blueprint, abort, jsonify
from flask_login import login_required

revisao_bp = Blueprint('revisao_cru', __name__, url_prefix='/revisao-cru')


def _norm(*v) -> str:
    return re.sub(r'[^a-z0-9]+', ' ', ' '.join(str(x or '') for x in v).lower())


def _da_estacao(texto: str, n: int) -> bool:
    return bool(re.search(rf'\bcru\s*{n}\b|\brevisao\s*{n}\b|\brevisora?\s*{n}\b|\bestacao\s*{n}\b', _norm(texto)))


@revisao_bp.route('/<int:n>/dados')
@login_required
def dados(n):
    if n not in (1, 2):
        abort(404)
    from app import acompanhamento as ac
    if not ac.configurado():
        return jsonify(ok=False, erro='ACOMP_TOKEN não configurado no servidor.')
    hoje = (datetime.now() - timedelta(hours=6)).strftime('%Y-%m-%d')      # dia de producao comeca as 6h
    agora_l, pecas_l, erros = [], [], []
    ab = ac.abertas()
    if ab.get('erro'):
        erros.append('ocorrências: ' + ab['erro'])
    rev = [o for o in ab.get('itens', []) if o.get('revisor')]
    rl = ac.rl_producao_todas(hoje)
    if rl.get('erro'):
        erros.append('RL.PRODUÇÃO: ' + rl['erro'])
    pecas = rl.get('itens', [])
    # separa por estacao quando a API identifica "Revisao Cru 1/2"; senao mostra tudo e avisa
    ident = any(_da_estacao(f"{p.get('setor')} {p.get('revisor')}", k) for p in pecas for k in (1, 2)) or any(_da_estacao(f"{o.get('motivo')} {o.get('tipo')} {o.get('aberta_por')}", k) for o in rev for k in (1, 2))
    if ident:
        rev = [o for o in rev if _da_estacao(f"{o.get('motivo')} {o.get('tipo')} {o.get('aberta_por')}", n)]
        pecas = [p for p in pecas if _da_estacao(f"{p.get('setor')} {p.get('revisor')}", n)]
    pecas.sort(key=lambda p: p.get('em') or datetime.min, reverse=True)
    res = Counter(p.get('resultado') or '—' for p in pecas)
    metros = round(sum(float(p.get('metros_reais') or 0) for p in pecas), 1)
    return jsonify(ok=True, estacao=n, identificada=ident, erros=erros, dia=hoje,
                   revisando=[dict(nome=o.get('aberta_por'), tipo=o.get('tipo'), motivo=o.get('motivo'), desde=o.get('inicio'), minutos=o.get('minutos'), turno=o.get('turno')) for o in rev],
                   resumo=dict(pecas=len(pecas), metros=metros, resultados=dict(res), revisores=sorted({p.get('revisor') for p in pecas if p.get('revisor')})),
                   pecas=[dict(quando=p.get('quando'), revisor=p.get('revisor'), maquina=p.get('maquina'), op=p.get('op'), artigo=p.get('artigo'), peca=p.get('peca'), resultado=p.get('resultado'),
                               metros=p.get('metros_reais'), defeitos=', '.join(p.get('defeitos') or []), turno=p.get('turno')) for p in pecas[:40]])
