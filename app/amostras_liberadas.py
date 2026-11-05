"""Cronograma de Amostras > 'Amostras liberadas e ordem de trabalho': recebe do agente do Desenvolvimento (relatorio_diario.py, 7h30) a foto diaria das cotacoes com
AMOSTRA LIBERADA (etapa na Timeline, OS, amostra, categoria, prazo em dias uteis, ordem de trabalho por responsavel e tarefas) e mostra o mesmo relatorio na aba.

POST /dev/amostras-liberadas/enviar   (Authorization: Bearer <GESTAO_API_TOKEN>, JSON)  -> grava a foto do dia
GET  /dev/amostras-liberadas/dados    ?dia=AAAA-MM-DD (padrao: o mais recente) + lista de dias disponiveis
GET  /dev/amostras-liberadas/excel    ?dia=...                                  -> Excel com tudo
"""
from __future__ import annotations

import hmac
import io
import json
import os
from datetime import date, datetime, timedelta

from flask import Blueprint, abort, jsonify, request, send_file
from flask_login import current_user, login_required

from app import db
from app.models import AmostrasLiberadasSnapshot, Tab

am_lib_bp = Blueprint('amostras_liberadas', __name__, url_prefix='/dev/amostras-liberadas')
MANTER_DIAS = 120
GRUPOS = ('atrasada', 'vence', 'prazo', 'corrigir', 'entregue')


def _token_ok() -> bool:
    token = (os.getenv('GESTAO_API_TOKEN') or '').strip()
    auth = request.headers.get('Authorization') or ''
    enviado = auth[7:].strip() if auth.lower().startswith('bearer ') else (request.headers.get('X-API-Key') or '').strip()
    return bool(token and enviado and hmac.compare_digest(token.encode(), enviado.encode()))


def _autorizar():
    tab = Tab.query.filter_by(slug='cronograma-amostras').first_or_404()
    if not tab.visible_to(current_user):
        abort(403)


def _limpa(p: dict) -> dict:
    """Valida o essencial e guarda so os campos conhecidos (o agente e outro programa: nao confiar no formato)."""
    itens = []
    for x in (p.get('itens') or [])[:3000]:
        if not isinstance(x, dict):
            continue
        g = str(x.get('grupo') or '')
        itens.append(dict(pais=str(x.get('pais') or 'OUT')[:5], id=str(x.get('id') or '')[:20], cliente=str(x.get('cliente') or '')[:80], artigo=str(x.get('artigo') or '')[:60],
                          etapa=str(x.get('etapa') or '')[:40], os=str(x.get('os') or '')[:30], amostra=str(x.get('amostra') or '')[:30], cat=x.get('cat') if isinstance(x.get('cat'), (int, float)) else None,
                          limite=str(x.get('limite') or '')[:10] or None, folga=x.get('folga') if isinstance(x.get('folga'), (int, float)) else None, situacao=str(x.get('situacao') or '')[:60],
                          problema=str(x.get('problema') or '')[:200], grupo=g if g in GRUPOS else 'corrigir', resp=str(x.get('resp') or '')[:40] or None,
                          ordem=int(x['ordem']) if isinstance(x.get('ordem'), (int, float)) else None))
    tarefas = []
    for t in (p.get('tarefas') or [])[:500]:
        if isinstance(t, dict):
            tarefas.append(dict(pais=str(t.get('pais') or '')[:5], responsavel=str(t.get('responsavel') or '')[:40], ordem=int(t['ordem']) if isinstance(t.get('ordem'), (int, float)) else None,
                                descricao=str(t.get('descricao') or '')[:400], prioridade=str(t.get('prioridade') or '')[:20], status=str(t.get('status') or '')[:30], desde=str(t.get('desde') or '')[:10]))
    velhas = {str(k)[:5]: int(v) for k, v in (p.get('seq_velhas') or {}).items() if isinstance(v, (int, float))}
    return dict(versao=1, itens=itens, tarefas=tarefas, seq_velhas=velhas, total_liberadas=len(itens))


@am_lib_bp.route('/enviar', methods=['POST'])
def enviar():
    if not (_token_ok() or (current_user.is_authenticated and current_user.is_admin)):
        return jsonify(erro='Não autorizado: envie Authorization: Bearer <token>.'), 401
    p = request.get_json(silent=True)
    if not isinstance(p, dict) or not isinstance(p.get('itens'), list):
        return jsonify(erro='JSON inválido: esperado {dia, gerado_em, itens: [...], tarefas: [...]}.'), 400
    try:
        dia = date.fromisoformat(str(p.get('dia') or '')[:10])
    except ValueError:
        return jsonify(erro='Campo "dia" inválido (AAAA-MM-DD).'), 400
    limpo = _limpa(p)
    s = AmostrasLiberadasSnapshot.query.filter_by(dia=dia).first() or AmostrasLiberadasSnapshot(dia=dia)
    s.gerado_em, s.recebido_em, s.dados = str(p.get('gerado_em') or '')[:40], datetime.utcnow(), json.dumps(limpo, ensure_ascii=False)
    db.session.add(s)
    AmostrasLiberadasSnapshot.query.filter(AmostrasLiberadasSnapshot.dia < dia - timedelta(days=MANTER_DIAS)).delete()
    db.session.commit()
    return jsonify(ok=True, dia=dia.isoformat(), itens=len(limpo['itens']), tarefas=len(limpo['tarefas']))


def _escolhe(dia_txt):
    q = AmostrasLiberadasSnapshot.query
    if dia_txt:
        try:
            return q.filter_by(dia=date.fromisoformat(dia_txt[:10])).first()
        except ValueError:
            return None
    return q.order_by(AmostrasLiberadasSnapshot.dia.desc()).first()


@am_lib_bp.route('/dados')
@login_required
def dados():
    _autorizar()
    s = _escolhe(request.args.get('dia'))
    dias = [d.dia.isoformat() for d in AmostrasLiberadasSnapshot.query.with_entities(AmostrasLiberadasSnapshot.dia).order_by(AmostrasLiberadasSnapshot.dia.desc()).limit(60).all()]
    if not s:
        return jsonify(ok=True, vazio=True, dias=dias)
    return jsonify(ok=True, vazio=False, dia=s.dia.isoformat(), gerado_em=s.gerado_em, recebido_em=s.recebido_em.isoformat(timespec='seconds') if s.recebido_em else None, dias=dias, **json.loads(s.dados))


@am_lib_bp.route('/excel')
@login_required
def excel():
    _autorizar()
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    s = _escolhe(request.args.get('dia'))
    if not s:
        abort(404)
    d = json.loads(s.dados)
    rot = {'atrasada': 'Atrasada', 'vence': 'Vence em até 3 d.u.', 'prazo': 'No prazo', 'corrigir': 'Dados a corrigir', 'entregue': 'Entregue'}
    wb = Workbook()
    ws = wb.active
    ws.title = 'Amostras liberadas'
    cab = ['País', 'Situação', 'Responsável', 'Ordem', 'ID', 'Cliente', 'Artigo', 'Etapa (Timeline)', 'OS', 'Amostra', 'Categoria', 'Limite', 'Folga (d.u.)', 'Problema']
    ws.append(cab)
    for c in ws[1]:
        c.font, c.fill = Font(bold=True, color='FFFFFF'), PatternFill('solid', fgColor='2672C4')
    ordem = {g: i for i, g in enumerate(GRUPOS)}
    for x in sorted(d['itens'], key=lambda x: (x['pais'], ordem.get(x['grupo'], 9), x['folga'] if x['folga'] is not None else 9999, x['id'])):
        ws.append([x['pais'], rot.get(x['grupo'], ''), x['resp'] or '', x['ordem'], x['id'], x['cliente'], x['artigo'], x['etapa'], x['os'], x['amostra'], x['cat'],
                   date.fromisoformat(x['limite']).strftime('%d/%m/%Y') if x['limite'] else '', x['folga'], x['problema']])
    ws.freeze_panes = 'A2'
    ws.auto_filter.ref = ws.dimensions
    if d['tarefas']:
        w2 = wb.create_sheet('Tarefas')
        w2.append(['País', 'Responsável', 'Ordem', 'Descrição', 'Prioridade', 'Status', 'Desde'])
        for t in d['tarefas']:
            w2.append([t['pais'], t['responsavel'], t['ordem'], t['descricao'], t['prioridade'], t['status'], t['desde']])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name=f'amostras_liberadas_{s.dia.isoformat()}.xlsx', mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
