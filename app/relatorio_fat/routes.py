"""Botao 'Relatorio de Faturamento' (PowerPoint e PDF): so aparece e so funciona para quem esta na lista (padrao: Christian, Edmar e Luciano)."""
from __future__ import annotations

import os
import re
import unicodedata
from datetime import datetime

from flask import Blueprint, Response, abort, current_app, jsonify, redirect, request
from flask_login import current_user, login_required, login_user

from app.relatorio_fat import agenda, captura, dados, pptx_gen
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
    return redirect('/aba/dashboard-comercial?' + '&'.join(f'{k}={request.args[k]}' for k in ('ano', 'mes', 'visao') if request.args.get(k)))


def db_get(model, pk):
    from app import db
    return db.session.get(model, pk)


def _montar_pedidos(mes):
    """PDF da Entrada de Pedidos: mesma impressao da tela (cabecalho, rodape e cores do relatorio de faturamento), com o titulo Entrada de Pedidos."""
    hoje = datetime.now()
    mes = mes or hoje.month
    corpo = captura.gerar_pdf(_base_url(), _usuario_id(), hoje.year, mes, 'pedidos')
    nome = re.sub(r'[^A-Za-z0-9_]+', '_', f'Entrada_de_Pedidos_{_norm(dados.MESES[mes - 1])}_{hoje.year}')
    return corpo, f'{nome}.pdf', mes, hoje.year


def _montar(formato, ano, mes):
    """(bytes, nome do arquivo, mes, ano) ou None se ainda nao ha dados."""
    from datetime import datetime
    from app.totvs.routes import dados_do_slide
    d = dados_do_slide(ano, mes)
    if not d:
        return None
    if formato == 'pdf':
        corpo = captura.gerar_pdf(_base_url(), _usuario_id(), d['ano'], d['mes'])           # PDF = copia exata da tela do Gestao (nao ha outro modelo)
    else:
        corpo = pptx_gen.gerar(dados.montar_paginas(d), datetime.now().strftime('%d/%m/%Y %H:%M'))
    nome = re.sub(r'[^A-Za-z0-9_]+', '_', f'Relatorio_de_Faturamento_{_norm(dados.MESES[d["mes"] - 1])}_{d["ano"]}')
    return corpo, f'{nome}.{formato}', d['mes'], d['ano']


def _resumo_envio_automatico() -> str:
    """Texto: como esta o envio automatico neste computador (para quem, quando, ultimo envio)."""
    cfg = agenda.configuracao()
    est = agenda._ler(current_app)
    dias = ', '.join(['seg', 'ter', 'qua', 'qui', 'sex', 'sab', 'dom'][d] for d in sorted(cfg['dias']))
    return ('\n\nENVIO AUTOMATICO\n'
            f'Ligado: {"SIM" if cfg["ativo"] else "NAO"}\n'
            f'Horario (Brasilia): {cfg["hora"][0]:02d}:{cfg["hora"][1]:02d} (se o Gestao ligar depois de {cfg["ate"][0]:02d}:{cfg["ate"][1]:02d}, pula o dia)\n'
            f'Dias: {dias}\n'
            f'Formato: {cfg["formato"]}' + (' + PDF da Entrada de Pedidos (alta prioridade)' if os.getenv('RELATORIO_FAT_ENVIO_PEDIDOS', '1') != '0' else '') + '\n'
            f'Destinatarios ({len(cfg["destinos"].split(","))}): {cfg["destinos"]}\n'
            f'Origem da lista: {"RELATORIO_FAT_DESTINOS do arquivo .env" if os.getenv("RELATORIO_FAT_DESTINOS") else "lista padrao do sistema"}\n'
            f'Ultimo envio: {est.get("enviado_em") or "nenhum ainda"}' + (f' para {", ".join(est.get("para") or [])}' if est.get('para') else '') + '\n'
            f'Ultimo erro: {est.get("erro") or "nenhum"}')


@rel_fat_bp.route('/quem')
@login_required
def quem():
    """Texto: com que usuario voce esta logado, se ele ve os botoes do relatorio e quem esta na lista."""
    lista = os.getenv('RELATORIO_FAT_USUARIOS', 'christian,edmar,luciano')
    return Response(f'Usuario logado: {current_user.name} <{current_user.email}>\n'
                    f'Ve os botoes do Relatorio de Faturamento: {"SIM" if pode_gerar(current_user) else "NAO"}\n'
                    f'Lista atual (RELATORIO_FAT_USUARIOS): {lista}\n'
                    f'Regra: o nome ou o e-mail do usuario precisa conter um dos nomes da lista.\n'
                    f'E-mail de saida configurado (SMTP): {"SIM" if envio.configurado() else "NAO - faltam " + ", ".join(envio.faltando())}'
                    + _resumo_envio_automatico(),
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
    pedidos = j.get('visao') == 'pedidos'
    teste = (j.get('teste_para') or '').strip()                          # envio de teste: so para este e-mail (nao usa a lista fixa)
    try:
        if teste:
            destinos = envio.validar(teste)
        else:
            destinos = envio.validar(agenda.configuracao()['destinos'] if pedidos else j.get('para'))        # Entrada de Pedidos: sempre a lista do relatorio de faturamento
    except ValueError as e:
        return jsonify(ok=False, erro=str(e)), 400
    junto = pedidos and bool(j.get('junto_faturamento'))                 # Entrada de Pedidos + Relatorio de Faturamento no mesmo e-mail
    # (funcao que monta, rotulo do erro) de cada anexo
    tarefas = [(lambda: _montar_pedidos(j.get('mes')), 'PDF da Entrada de Pedidos')] if pedidos else []
    if junto or not pedidos:
        formatos = ['pdf'] if pedidos else {'pdf': ['pdf'], 'pptx': ['pptx'], 'ambos': ['pdf', 'pptx']}.get(j.get('formato', 'pdf'), ['pdf'])
        for f in formatos:
            tarefas.append(((lambda f=f: _montar(f, j.get('ano') if not pedidos else None, j.get('mes'))), f'{f.upper()} do Relatório de Faturamento'))
    anexos, ref = [], None
    for fn, rotulo in tarefas:
        try:
            r = fn()
        except Exception as e:  # noqa: BLE001
            return jsonify(ok=False, erro=f'Não foi possível gerar o {rotulo}: {e}'), 500
        if not r:
            return jsonify(ok=False, erro='Ainda não há dados de faturamento carregados do TOTVS.'), 404
        anexos.append((r[1], r[0], MIME['pdf' if r[1].endswith('.pdf') else 'pptx']))
        ref = f'{dados.MESES[r[2] - 1]}/{r[3]}'
    try:
        if junto:
            assunto, titulo = f'Relatório de Faturamento e Entrada de Pedidos — {ref}', 'Relatório de Faturamento e o Relatório de Entrada de Pedidos'
        elif pedidos:
            assunto, titulo = f'Entrada de Pedidos — {ref}', 'Relatório de Entrada de Pedidos'
        else:
            assunto, titulo = f'Relatório de Faturamento — {ref}', 'Relatório de Faturamento'
        if teste:
            assunto = '[TESTE] ' + assunto
        envio.enviar(destinos, assunto, envio.corpo_padrao(ref, agenda.agora_brasilia().strftime('%d/%m/%Y %H:%M'), titulo=titulo), anexos, prioridade_alta=pedidos)
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
    pedidos = request.args.get('visao') == 'pedidos'
    if pedidos and formato != 'pdf':
        abort(400)
    try:
        r_ = _montar_pedidos(request.args.get('mes', type=int)) if pedidos else _montar(formato, request.args.get('ano', type=int), request.args.get('mes', type=int))
    except Exception as e:  # noqa: BLE001
        return Response(f'Não foi possível gerar o {formato.upper()}: {e}', status=500, mimetype='text/plain; charset=utf-8')
    if not r_:
        return Response('Ainda não há dados de faturamento carregados do TOTVS. Abra o Dashboard Comercial e aguarde a primeira atualização.',
                        status=404, mimetype='text/plain; charset=utf-8')
    corpo, nome_arq = r_[0], r_[1]
    r = Response(corpo, mimetype=MIME[formato])
    r.headers['Content-Disposition'] = f'attachment; filename="{nome_arq}"'
    r.headers['Cache-Control'] = 'no-store'
    return r
