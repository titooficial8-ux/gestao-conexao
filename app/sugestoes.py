"""Sugestoes de melhoria: qualquer usuario envia pelo botao 'Sugestao' do Painel; a equipe de TI/Sistema (e administradores) recebe no sino de notificacao."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from flask import Blueprint, abort, jsonify, request, send_file
import io
from flask_login import current_user, login_required

from app import db
from app.models import Sugestao, SugestaoAnexo

sugestoes_bp = Blueprint('sugestoes', __name__, url_prefix='/sugestoes')
BRT = timezone(timedelta(hours=-3))


def _ve_sugestoes() -> bool:
    """Somente o Christian trata as sugestoes (recebe o sino e muda o status)."""
    return (current_user.email or '').strip().lower().startswith('christian.yarzon@')


def _setor_do_usuario() -> str:
    if getattr(current_user, 'setor', None):
        return current_user.setor.name
    nomes = [s.name for s in (current_user.sectors or [])]
    return ' / '.join(nomes[:3]) + ('…' if len(nomes) > 3 else '') if nomes else '—'


@sugestoes_bp.route('/eu')
@login_required
def eu():
    nome = (current_user.name or '').strip()
    return jsonify(nome=nome, primeiro=nome.split(' - ')[0].split()[0] if nome else '', setor=_setor_do_usuario())


MAX_ANEXO = 8 * 1024 * 1024
MAX_ANEXOS = 6


@sugestoes_bp.route('/enviar', methods=['POST'])
@login_required
def enviar():
    """Aceita multipart (texto + arquivos/prints colados) ou JSON so com texto."""
    d = request.form if request.form or request.files else (request.get_json(silent=True) or {})
    texto = str(d.get('texto') or '').strip()
    arqs = [f for f in request.files.getlist('arquivos') if f and f.filename]
    if len(texto) < (3 if arqs else 10):
        return jsonify(erro='Conte um pouco mais da sua sugestão (mínimo de 10 caracteres).' if not arqs else 'Escreva ao menos uma frase sobre o anexo.'), 400
    if len(texto) > 3000:
        return jsonify(erro='Use no máximo 3000 caracteres.'), 400
    if len(arqs) > MAX_ANEXOS:
        return jsonify(erro=f'Anexe no máximo {MAX_ANEXOS} arquivos.'), 400
    blobs = []
    for f in arqs:
        dados = f.read()
        if len(dados) > MAX_ANEXO:
            return jsonify(erro=f'"{f.filename}" passa de 8 MB.'), 400
        blobs.append((f.filename[:200], (f.mimetype or 'application/octet-stream')[:100], dados))
    x = Sugestao(usuario_id=current_user.id, nome=current_user.name, setor=_setor_do_usuario(), texto=texto)
    db.session.add(x)
    db.session.flush()
    for nome, mime, dados in blobs:
        db.session.add(SugestaoAnexo(sugestao_id=x.id, nome=nome, mime=mime, dados=dados))
    db.session.commit()
    return jsonify(ok=True)


@sugestoes_bp.route('/anexo/<int:aid>')
@login_required
def anexo(aid):
    a = db.session.get(SugestaoAnexo, aid) or abort(404)
    x = db.session.get(Sugestao, a.sugestao_id) or abort(404)
    if not (_ve_sugestoes() or x.usuario_id == current_user.id):
        abort(403)
    img = (a.mime or '').startswith('image/') and (a.mime or '') != 'image/svg+xml'
    return send_file(io.BytesIO(a.dados or b''), mimetype=a.mime if img else 'application/octet-stream', as_attachment=not img, download_name=a.nome)


def _json(x: Sugestao) -> dict:
    anexos = [dict(id=a.id, nome=a.nome or 'arquivo', img=(a.mime or '').startswith('image/') and a.mime != 'image/svg+xml') for a in SugestaoAnexo.query.filter_by(sugestao_id=x.id).all()]
    em = x.criada_em.replace(tzinfo=timezone.utc).astimezone(BRT) if x.criada_em else None
    return dict(id=x.id, nome=x.nome or '', setor=x.setor or '', texto=x.texto or '', status=x.status, em=em.strftime('%d/%m/%Y %H:%M') if em else '', por=x.tratada_por or '', anexos=anexos)


@sugestoes_bp.route('/notificacoes')
@login_required
def notificacoes():
    """Sino: o Christian ve todas (novas = NOVA); o autor ve so as proprias (novas = respostas ainda nao vistas)."""
    if _ve_sugestoes():
        novas = Sugestao.query.filter_by(status='NOVA').count()
        itens = Sugestao.query.order_by(Sugestao.criada_em.desc()).limit(30).all()
        return jsonify(pode=True, gestor=True, novas=novas, itens=[_json(x) for x in itens])
    q = Sugestao.query.filter_by(usuario_id=current_user.id)
    itens = q.order_by(Sugestao.criada_em.desc()).limit(30).all()
    if not itens:
        return jsonify(pode=False, novas=0, itens=[])
    novas = sum(1 for x in itens if x.status != 'NOVA' and not x.vista_autor)
    return jsonify(pode=True, gestor=False, novas=novas, itens=[_json(x) for x in itens])


@sugestoes_bp.route('/vistas', methods=['POST'])
@login_required
def vistas():
    Sugestao.query.filter(Sugestao.usuario_id == current_user.id, Sugestao.status != 'NOVA').update({'vista_autor': True})
    db.session.commit()
    return jsonify(ok=True)


@sugestoes_bp.route('/<int:sid>/status', methods=['POST'])
@login_required
def mudar_status(sid):
    if not _ve_sugestoes():
        abort(403)
    x = db.session.get(Sugestao, sid) or abort(404)
    st = str((request.get_json(silent=True) or {}).get('status') or '').upper()
    if st not in ('NOVA', 'EM_ANALISE', 'CONCLUIDA'):
        return jsonify(erro='Status inválido.'), 400
    x.status, x.tratada_por, x.tratada_em, x.vista_autor = st, getattr(current_user, 'name', None), datetime.utcnow(), False
    db.session.commit()
    return jsonify(ok=True)
