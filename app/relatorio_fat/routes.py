"""Botao 'Relatorio de Faturamento' (PowerPoint e PDF): so aparece e so funciona para quem esta na lista (padrao: Christian, Edmar e Luciano)."""
from __future__ import annotations

import os
import re
import unicodedata

from flask import Blueprint, Response, abort, current_app, jsonify, redirect, request
from flask_login import current_user, login_required, login_user

from app.relatorio_fat import captura, dados, pdf_gen, pptx_gen
from app.relatorio_fat import envio_email as envio

rel_fat_bp = Blueprint('relatorio_faturamento', __name__, url_prefix='/relatorio-faturamento')

MIME = {'pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation', 'pdf': 'application/pdf'}


def _norm(txt: str) -> str:
    return unicodedata.normalize('NFKD', txt or '').encode('ascii', 'ignore').decode().lower()


def pode_gerar(user) -> bool:
    """Nome ou e-mail do usuario contem um dos nomes de RELATORIO_FAT_USUARIOS (padrao: christian,edmar)."""
    if not getattr(user, 'is_authenticated', False):
        return False
    nomes = [_norm(n).strip() for n in os.getenv('RELATORIO_FAT_USUARIOS', 'christian,edmar,luciano').split(',') if n.strip()]
    alvo = _norm(f'{user.name} {user.email}')
    return any(n in alvo for n in nomes)


def _base_url() -> str:
    """Endereco do proprio Gestao (APP_URL no .env; senao o endereco da requisicao; senao localhost:5000)."""
    if os.getenv('APP_URL'):
        return os.getenv('APP_URL')
    try:
        return request.host_url
    except RuntimeError:
        return 'http://127.0.0.1:5000'


def _usuario_id() -> int:
    """Usuario com quem o Chromium entra para ler a tela: o logado (se autorizado) ou o primeiro da lista do relatorio."""
    from app.models import User
    if getattr(current_user, 'is_authenticated', False) and pode_gerar(current_user):
        return current_user.id
    for u in User.query.order_by(User.id).all():
        if pode_gerar(u):
            return u.id
    raise RuntimeError('nenhum usuario autorizado')


@rel_fat_bp.route('/entrar')
def entrar():
    """Uso interno: o Chromium do relatorio entra com um token de uso unico (valido por 2 min) e cai no Slide mensal."""
    from app.models import User
    uid = captura.consumir_token(request.args.get('t', ''))
    u = db_get(User, uid) if uid else None
    if not u:
        abort(403)
    login_user(u)
    return redirect('/aba/dashboard-comercial?' + '&'.join(f'{k}={request.args[k]}' for k in ('ano', 'mes') if request.args.get(k)))


def db_get(model, pk):
    from app import db
    return db.session.get(model, pk)


def _montar(formato, ano, mes):
    """(bytes, nome do arquivo, mes, ano) ou None se ainda nao ha dados."""
    from datetime import datetime
    from app.totvs.routes import dados_do_slide
    d = dados_do_slide(ano, mes)
    if not d:
        return None
    corpo = None
    if formato == 'pdf' and captura.disponivel():
        try:
            corpo = captura.gerar_pdf(_base_url(), _usuario_id(), d['ano'], d['mes'])     # copia exata da tela do Gestao
        except Exception as e:  # noqa: BLE001
            current_app.logger.warning('Relatorio: captura da tela falhou (%s); usando o PDF padrao.', e)
    if corpo is None:
        paginas = dados.montar_paginas(d)
        corpo = (pptx_gen if formato == 'pptx' else pdf_gen).gerar(paginas, datetime.now().strftime('%d/%m/%Y %H:%M'))
    nome = re.sub(r'[^A-Za-z0-9_]+', '_', f'Relatorio_de_Faturamento_{_norm(dados.MESES[d["mes"] - 1])}_{d["ano"]}')
    return corpo, f'{nome}.{formato}', d['mes'], d['ano']


@rel_fat_bp.route('/quem')
@login_required
def quem():
    """Texto: com que usuario voce esta logado, se ele ve os botoes do relatorio e quem esta na lista."""
    lista = os.getenv('RELATORIO_FAT_USUARIOS', 'christian,edmar,luciano')
    return Response(f'Usuario logado: {current_user.name} <{current_user.email}>\n'
                    f'Ve os botoes do Relatorio de Faturamento: {"SIM" if pode_gerar(current_user) else "NAO"}\n'
                    f'Lista atual (RELATORIO_FAT_USUARIOS): {lista}\n'
                    f'Regra: o nome ou o e-mail do usuario precisa conter um dos nomes da lista.\n'
                    f'E-mail de saida configurado (SMTP): {"SIM" if envio.configurado() else "NAO - faltam " + ", ".join(envio.faltando())}',
                    mimetype='text/plain; charset=utf-8')


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
