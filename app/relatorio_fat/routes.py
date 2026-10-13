"""Botao 'Relatorio de Faturamento' (PowerPoint e PDF): so aparece e so funciona para quem esta na lista (padrao: Christian e Edmar)."""
from __future__ import annotations

import os
import re
import unicodedata

from flask import Blueprint, Response, abort, jsonify, request
from flask_login import current_user, login_required

from app.relatorio_fat import dados, pdf_gen, pptx_gen
from app.relatorio_fat import envio_email as envio

rel_fat_bp = Blueprint('relatorio_faturamento', __name__, url_prefix='/relatorio-faturamento')

MIME = {'pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation', 'pdf': 'application/pdf'}


def _norm(txt: str) -> str:
    return unicodedata.normalize('NFKD', txt or '').encode('ascii', 'ignore').decode().lower()


def pode_gerar(user) -> bool:
    """Nome ou e-mail do usuario contem um dos nomes de RELATORIO_FAT_USUARIOS (padrao: christian,edmar)."""
    if not getattr(user, 'is_authenticated', False):
        return False
    nomes = [_norm(n).strip() for n in os.getenv('RELATORIO_FAT_USUARIOS', 'christian,edmar').split(',') if n.strip()]
    alvo = _norm(f'{user.name} {user.email}')
    return any(n in alvo for n in nomes)


def _montar(formato, ano, mes):
    """(bytes, nome do arquivo, mes, ano) ou None se ainda nao ha dados."""
    from datetime import datetime
    from app.totvs.routes import dados_do_slide
    d = dados_do_slide(ano, mes)
    if not d:
        return None
    paginas = dados.montar_paginas(d)
    corpo = (pptx_gen if formato == 'pptx' else pdf_gen).gerar(paginas, datetime.now().strftime('%d/%m/%Y %H:%M'))
    nome = re.sub(r'[^A-Za-z0-9_]+', '_', f'Relatorio_de_Faturamento_{_norm(dados.MESES[d["mes"] - 1])}_{d["ano"]}')
    return corpo, f'{nome}.{formato}', d['mes'], d['ano']


@rel_fat_bp.route('/enviar', methods=['POST'])
@login_required
def enviar():
    """Gera o relatorio e manda por e-mail (PDF, PowerPoint ou os dois) para os destinatarios informados."""
    if not pode_gerar(current_user):
        abort(403)
    j = request.get_json(silent=True) or {}
    if not envio.configurado():
        return jsonify(ok=False, erro='E-mail ainda não configurado neste computador. Preencha no arquivo .env: ' + ', '.join(envio.faltando()) + ' (veja o .env.example) e reinicie o Gestão.'), 400
    try:
        destinos = envio.validar(j.get('para'))
    except ValueError as e:
        return jsonify(ok=False, erro=str(e)), 400
    formatos = {'pdf': ['pdf'], 'pptx': ['pptx'], 'ambos': ['pdf', 'pptx']}.get(j.get('formato', 'pdf'), ['pdf'])
    anexos, ref = [], None
    for f in formatos:
        r = _montar(f, j.get('ano'), j.get('mes'))
        if not r:
            return jsonify(ok=False, erro='Ainda não há dados de faturamento carregados do TOTVS.'), 404
        anexos.append((r[1], r[0], MIME[f]))
        ref = f'{dados.MESES[r[2] - 1]}/{r[3]}'
    try:
        envio.enviar(destinos, f'Relatório de Faturamento — {ref}',
                     f'Olá,\n\nSegue o Relatório de Faturamento de {ref}, gerado pelo Gestão Conexão.\n\nwww.gestaoconexao.com.br', anexos)
    except Exception as e:  # noqa: BLE001
        return jsonify(ok=False, erro=f'Não foi possível enviar: {e}'), 502
    return jsonify(ok=True, enviado_para=destinos, anexos=[a[0] for a in anexos])


@rel_fat_bp.route('/gerar')
@login_required
def gerar():
    if not pode_gerar(current_user):
        abort(403)
    formato = request.args.get('formato', 'pptx').lower()
    if formato not in MIME:
        abort(400)
    r_ = _montar(formato, request.args.get('ano', type=int), request.args.get('mes', type=int))
    if not r_:
        return Response('Ainda não há dados de faturamento carregados do TOTVS. Abra o Dashboard Comercial e aguarde a primeira atualização.',
                        status=404, mimetype='text/plain; charset=utf-8')
    corpo, nome_arq = r_[0], r_[1]
    r = Response(corpo, mimetype=MIME[formato])
    r.headers['Content-Disposition'] = f'attachment; filename="{nome_arq}"'
    r.headers['Cache-Control'] = 'no-store'
    return r
