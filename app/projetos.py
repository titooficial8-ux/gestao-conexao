"""Gestao de Projetos (Gestao Sistema): acompanhamento dos projetos da equipe de Tecnologia/Informacao a partir da planilha de OKR.

A planilha (abas '09_2026', '10_2026'..., 'GESTAO CONEXAO', 'Desenvolvimentos', 'Regras de Dev API-SITE') e lida e guardada como um retrato (snapshot) no banco. A tela le o ultimo retrato;
quem e do TI/Sistema (ou administrador) envia uma planilha nova no proprio botao 'Atualizar base' e a tela passa a mostrar os novos numeros, ficando guardado quem atualizou e quando."""
from __future__ import annotations

import io
import json
import re
import unicodedata
from datetime import date, datetime
from pathlib import Path

from flask import Blueprint, abort, jsonify, request
from flask_login import current_user, login_required

from app import db
from app.models import ProjetosSnapshot, Tab

projetos_bp = Blueprint('projetos', __name__, url_prefix='/projetos')
SEMENTE = Path(__file__).parent / 'seed_data' / 'projetos_okr.json'
SETORES_EDITAM = ('TI/Sistema',)


def _norm(v) -> str:
    t = unicodedata.normalize('NFKD', str(v or '')).encode('ascii', 'ignore').decode().lower()
    return re.sub(r'\s+', ' ', t).strip()


def _val(v):
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, float):
        return round(v, 4)
    if isinstance(v, str):
        return v.strip() or None
    return v


def _linhas(ws):
    for i, r in enumerate(ws.iter_rows(values_only=True), 1):
        yield i, [_val(c) for c in r]


def _num(v):
    return v if isinstance(v, (int, float)) else 0


def _mes(ws) -> dict:
    """Aba '09_2026': tabela dos Key Results + historico de atualizacoes."""
    krs, geral, hist, modo = [], None, [], None
    for _, r in _linhas(ws):
        b = _norm(r[1] if len(r) > 1 else None)
        if _norm(r[0]) == '#' and b == 'key result':
            modo = 'kr' if 'pontos' in _norm(r[2] if len(r) > 2 else '') else 'hist'
            continue
        if modo == 'kr':
            if b == 'geral':
                geral = dict(pontos=_num(r[2]), partic=_num(r[3]), concluido=_num(r[4]), pontos_concluidos=_num(r[5]), status=r[6])
                modo = None
            elif r[1]:
                krs.append(dict(n=r[0], nome=r[1], pontos=_num(r[2]), partic=_num(r[3]), concluido=_num(r[4]), pontos_concluidos=_num(r[5]), status=r[6]))
        elif modo == 'hist' and r[1]:
            hist.append(dict(n=r[0], nome=r[1], data=r[2], concluido=_num(r[3]), pontos_concluidos=_num(r[4]), responsavel=r[5], status=r[6], obs=r[7] if len(r) > 7 else None))
        if _norm(r[0]).startswith('historico'):
            modo = None
    return dict(krs=krs, geral=geral, historico=hist)


def _modulos(ws) -> dict:
    """Aba 'GESTAO CONEXAO': resumo por modulo (1.000 pontos) e a lista de entregas de cada modulo."""
    resumo, geral, secoes, atual = [], None, {}, None
    for _, r in _linhas(ws):
        a, b = r[0], r[1] if len(r) > 1 else None
        if isinstance(a, str) and re.match(r'^\d+\.\s', a) and 'ponto' in _norm(a):
            atual = int(a.split('.')[0])
            secoes[atual] = []
            continue
        if isinstance(a, int) and atual is None and b:
            resumo.append(dict(n=a, nome=b, pontos=_num(r[2]), partic=_num(r[3]), concluido=_num(r[4]), pontos_concluidos=_num(r[5]), status=r[6], resumo_entregas=r[7]))
        elif atual is None and _norm(b).startswith('geral'):
            geral = dict(nome=b, pontos=_num(r[2]), concluido=_num(r[4]), pontos_concluidos=_num(r[5]), status=r[6])
        elif atual is not None:
            if _norm(b).startswith('total'):
                atual = None
            elif isinstance(a, int) and b:
                secoes[atual].append(dict(n=a, nome=b, inicio=r[2], concluido=_num(r[3]), pontos_concluidos=_num(r[4]), responsavel=r[5], status=r[6], obs=r[7] if len(r) > 7 else None))
    for m in resumo:
        m['entregas'] = secoes.get(m['n'], [])
    return dict(modulos=resumo, geral=geral)


def _solicitacoes(ws) -> list:
    saida, cab = [], None
    for _, r in _linhas(ws):
        if cab is None:
            if _norm(r[1] if len(r) > 1 else None) == 'solicitacao':
                cab = True
            continue
        if r[1]:
            saida.append(dict(n=r[0], nome=r[1], status=r[2], situacao=r[3], inicio=r[4], solicitada=r[5], depto=r[6], nivel=r[7], obs=r[8] if len(r) > 8 else None))
    return saida


def _etapas(ws) -> list:
    saida, cab = [], None
    for _, r in _linhas(ws):
        if cab is None:
            if _norm(r[1] if len(r) > 1 else None) == 'etapa':
                cab = True
            continue
        if r[1]:
            saida.append(dict(n=r[0], etapa=r[1], descricao=r[2]))
    return saida


def ler_planilha(conteudo: bytes, nome: str = '') -> dict:
    """Le a planilha de OKR e devolve o retrato que a tela mostra. Levanta ValueError se nao for o modelo esperado."""
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(conteudo), data_only=True)
    meses, mod, solic, etapas = {}, dict(modulos=[], geral=None), [], []
    for ws in wb:
        n = _norm(ws.title)
        if re.fullmatch(r'\d{2}_\d{4}', ws.title.strip()):
            meses[ws.title.strip().replace('_', '/')] = _mes(ws)
        elif n.startswith('gestao conexao'):
            mod = _modulos(ws)
        elif n.startswith('desenvolvimentos'):
            solic = _solicitacoes(ws)
        elif n.startswith('regras de dev'):
            etapas = _etapas(ws)
    if not mod['modulos'] and not meses and not solic:
        raise ValueError('A planilha não tem as abas esperadas (mês do OKR, GESTÃO CONEXÃO, Desenvolvimentos).')
    return dict(arquivo=nome, meses=dict(sorted(meses.items(), key=lambda kv: kv[0][3:] + kv[0][:2])), modulos=mod['modulos'], geral=mod['geral'], solicitacoes=solic, etapas=etapas)


def pode_editar(user=None) -> bool:
    user = user or current_user
    if not getattr(user, 'is_authenticated', False):
        return False
    return bool(user.is_admin or any(user.has_sector(s) for s in SETORES_EDITAM))


def _snapshot_atual():
    s = ProjetosSnapshot.query.order_by(ProjetosSnapshot.id.desc()).first()
    if s:
        return s
    if SEMENTE.exists():                                              # 1a vez: carrega a planilha de OKR que veio com o sistema
        s = ProjetosSnapshot(usuario='Carga inicial', arquivo='OKR_AGOSTO-OUTUBRO.xlsx', dados=SEMENTE.read_text(encoding='utf-8'))
        db.session.add(s)
        db.session.commit()
    return s


def _autorizar():
    tab = Tab.query.filter_by(slug='gestao-projetos').first_or_404()
    if not tab.visible_to(current_user):
        abort(403)


@projetos_bp.route('/dados')
@login_required
def dados():
    _autorizar()
    s = _snapshot_atual()
    if not s:
        return jsonify(vazio=True, pode_editar=pode_editar())
    d = json.loads(s.dados)
    return jsonify(dict(d, atualizado_por=s.usuario, atualizado_em=s.criado_em.strftime('%d/%m/%Y %H:%M') if s.criado_em else '', pode_editar=pode_editar(), versoes=ProjetosSnapshot.query.count()))


@projetos_bp.route('/atualizar', methods=['POST'])
@login_required
def atualizar():
    _autorizar()
    if not pode_editar():
        return jsonify(ok=False, erro='Somente o TI/Sistema e os administradores atualizam a base.'), 403
    f = request.files.get('arquivo')
    if not f or not f.filename.lower().endswith(('.xlsx', '.xlsm')):
        return jsonify(ok=False, erro='Envie a planilha de OKR (.xlsx).'), 400
    try:
        d = ler_planilha(f.read(), f.filename)
    except Exception as e:  # noqa: BLE001
        return jsonify(ok=False, erro=f'Não consegui ler a planilha: {str(e)[:200]}'), 400
    db.session.add(ProjetosSnapshot(usuario=getattr(current_user, 'name', None) or 'Usuário', arquivo=f.filename[:200], dados=json.dumps(d, ensure_ascii=False)))
    db.session.commit()
    return jsonify(ok=True, modulos=len(d['modulos']), solicitacoes=len(d['solicitacoes']), meses=list(d['meses']))
