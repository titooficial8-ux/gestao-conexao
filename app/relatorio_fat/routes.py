"""Botao 'Relatorio de Faturamento' (PowerPoint e PDF): so aparece e so funciona para quem esta na lista (padrao: Christian e Edmar)."""
from __future__ import annotations

import os
import re
import unicodedata

from flask import Blueprint, Response, abort, request
from flask_login import current_user, login_required

from app.relatorio_fat import dados, pdf_gen, pptx_gen

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


@rel_fat_bp.route('/gerar')
@login_required
def gerar():
    if not pode_gerar(current_user):
        abort(403)
    formato = request.args.get('formato', 'pptx').lower()
    if formato not in MIME:
        abort(400)
    from app.totvs.routes import dados_do_slide
    d = dados_do_slide(request.args.get('ano', type=int), request.args.get('mes', type=int))
    if not d:
        return Response('Ainda não há dados de faturamento carregados do TOTVS. Abra o Dashboard Comercial e aguarde a primeira atualização.',
                        status=404, mimetype='text/plain; charset=utf-8')
    paginas = dados.montar_paginas(d)
    from datetime import datetime
    corpo = (pptx_gen if formato == 'pptx' else pdf_gen).gerar(paginas, datetime.now().strftime('%d/%m/%Y %H:%M'))
    nome = re.sub(r'[^A-Za-z0-9_]+', '_', f'Relatorio_de_Faturamento_{_norm(dados.MESES[d["mes"] - 1])}_{d["ano"]}')
    r = Response(corpo, mimetype=MIME[formato])
    r.headers['Content-Disposition'] = f'attachment; filename="{nome}.{formato}"'
    r.headers['Cache-Control'] = 'no-store'
    return r
