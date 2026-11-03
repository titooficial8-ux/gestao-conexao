"""Comunicados oficiais enviados pelo Gestao (uma rota por comunicado, so administrador, com previa antes e protecao contra envio duplicado)."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from flask import Blueprint, Response, abort, current_app, request
from flask_login import current_user, login_required

comunicados_bp = Blueprint('comunicados', __name__, url_prefix='/comunicados')

LIDERES = dict(
    chave='lideres_turno',
    assunto='NOTA OFICIAL: ATENÇÃO LÍDERES DE TURNO',
    para=['thomas.barbieri@conexaomalhas.com.br', 'aline.pinheiro@conexaomalhas.com.br', 'homero.vieira@conexaomalhas.com.br', 'isabela.ramos@conexaomalhas.com.br', 'rogerio.passos@conexaomalhas.com.br'],
    cc=['christian.yarzon@conexaomalhas.com.br'],
    texto=(
        'Prezados,\n\n'
        'Gostaríamos de reforçar a importância do PREENCHIMENTO CORRETO E EM TEMPO REAL das informações das máquinas no Acompanhamento de Produção.\n\n'
        'Tudo o que a liderança registra no tablet durante o turno (paradas, manutenções, amostras, setups e demais ocorrências), junto com os apontamentos da equipe da Revisão Cru, '
        'alimenta o relatório do turno, enviado automaticamente ao Homero em até 10 minutos após o encerramento do turno.\n\n'
        'Por isso, o que for registrado de forma incompleta, atrasada ou incorreta aparece no relatório exatamente assim, e as análises e decisões que dependem dele ficam prejudicadas. '
        'É fundamental, portanto, que a liderança monitore as informações no tablet durante todo o turno.\n\n'
        'O QUE PEDIMOS À LIDERANÇA:\n'
        '- Acompanhar o tablet durante todo o turno e manter as ocorrências das máquinas atualizadas.\n'
        '- Abrir a ocorrência no momento em que ela começa e encerrá-la assim que a máquina volta a produzir, com o motivo correto.\n'
        '- Conferir, antes do fim do turno, se não há ocorrências abertas por engano nem máquinas sem informação.\n'
        '- Orientar a equipe da Revisão Cru para que os apontamentos sejam feitos corretamente e no mesmo turno.\n\n'
        'Contamos com o apoio de todos. Em caso de dúvida sobre o preenchimento, procurem o PCP ou a equipe de Sistemas.\n\n'
        'Atenciosamente,\n'
        'Gestão Conexão\n'
        'www.gestaoconexao.com.br'
    ),
)


def _arquivo() -> Path:
    return Path(current_app.instance_path) / 'comunicados.json'


def _estado() -> dict:
    try:
        return json.loads(_arquivo().read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


@comunicados_bp.route('/lideres-turno')
@login_required
def lideres_turno():
    """?  mostra a previa (nao envia). &enviar=1 envia para a lista oficial. &teste=email envia so para esse endereco, com [TESTE] no assunto. &forcar=1 reenvia mesmo ja enviado."""
    if not current_user.is_admin:
        abort(403)
    from app.relatorio_fat import envio_email as envio
    from app.relatorio_fat.agenda import agora_brasilia, registrar_envio
    c = LIDERES
    teste = (request.args.get('teste') or '').strip()
    para = envio.validar(teste) if teste else c['para']
    cc = [] if teste else c['cc']
    assunto = ('[TESTE] ' if teste else '') + c['assunto']
    corpo = c['texto'] + '\n\n--\n' + envio.AVISO_LEGAL
    ja = _estado().get(c['chave'])
    if request.args.get('enviar') != '1':
        return Response(f'PREVIA (nada foi enviado)\n\nAssunto: {assunto}\nPrioridade: ALTA\nPara: {", ".join(para)}\nCc: {", ".join(cc) or "-"}\n'
                        + (f'\nATENCAO: este comunicado ja foi enviado em {ja["quando"]} por {ja["por"]}. Para reenviar, acrescente &forcar=1.\n' if ja and not teste else '')
                        + f'\n{c["texto"]}\n\n(abaixo vem o aviso legal PT/EN/ES em letra pequena e cinza)\n\nPara enviar de verdade, acrescente &enviar=1 ao endereco.', mimetype='text/plain; charset=utf-8')
    if not envio.configurado():
        return Response('E-mail ainda nao configurado neste computador: faltam ' + ', '.join(envio.faltando()), status=400, mimetype='text/plain; charset=utf-8')
    if ja and not teste and request.args.get('forcar') != '1':
        return Response(f'Este comunicado ja foi enviado em {ja["quando"]} por {ja["por"]}. Nada foi enviado de novo. Para reenviar mesmo assim, acrescente &forcar=1.', status=409, mimetype='text/plain; charset=utf-8')
    try:
        rec = envio.enviar(para, assunto, corpo, [], prioridade_alta=True, cc=cc or None)
    except Exception as e:  # noqa: BLE001
        return Response(f'Nao foi possivel enviar: {e}', status=502, mimetype='text/plain; charset=utf-8')
    registrar_envio(current_app, assunto, para + cc, rec, 'comunicado (' + (getattr(current_user, 'name', '') or '?') + ')')
    if not teste:
        est = _estado()
        est[c['chave']] = dict(quando=agora_brasilia().strftime('%d/%m/%Y %H:%M'), por=getattr(current_user, 'name', '') or '?', para=para, cc=cc)
        Path(current_app.instance_path).mkdir(parents=True, exist_ok=True)
        _arquivo().write_text(json.dumps(est, ensure_ascii=False, indent=1), encoding='utf-8')
    return Response(f'ENVIADO (prioridade alta): {assunto}\nPara: {", ".join(para)}\nCc: {", ".join(cc) or "-"}', mimetype='text/plain; charset=utf-8')
