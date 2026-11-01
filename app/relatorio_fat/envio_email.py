"""Envio do Relatorio de Faturamento por e-mail (SMTP). As credenciais ficam so no .env do computador, nunca no codigo."""
from __future__ import annotations

import os
import re
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import make_msgid

VARIAVEIS = ('SMTP_HOST', 'SMTP_USER', 'SMTP_PASSWORD')


def configurado() -> bool:
    return all(os.getenv(v) for v in VARIAVEIS)


def faltando() -> list[str]:
    return [v for v in VARIAVEIS if not os.getenv(v)]


def validar(destinos: str) -> list[str]:
    """Aceita varios e-mails separados por virgula, ponto e virgula ou espaco; devolve a lista valida."""
    lista = [x.strip() for x in re.split(r'[,;\s]+', destinos or '') if x.strip()]
    ruins = [x for x in lista if not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', x)]
    if not lista or ruins:
        raise ValueError('E-mail inválido: ' + (', '.join(ruins) if ruins else 'informe ao menos um destinatário.'))
    return lista


def _html_com_aviso(corpo: str) -> str | None:
    """Versao HTML do e-mail quando ele termina com o AVISO LEGAL: texto normal, uma linha cinza e o aviso em letra pequena e cinza (3 paragrafos), como no modelo aprovado."""
    import html as _h
    marca = '\n\n--\n'
    if marca not in corpo or 'AVISO LEGAL' not in corpo:
        return None
    principal, aviso = corpo.split(marca, 1)

    def paragrafos(txt, estilo):
        return ''.join(f'<p style="{estilo}">{_h.escape(par).replace(chr(10), "<br>")}</p>' for par in txt.split('\n\n') if par.strip())
    fonte = 'font-family:Calibri,Arial,sans-serif;'
    return ('<html><body style="margin:0;padding:0">'
            f'<div style="{fonte}font-size:11pt;color:#222">' + paragrafos(principal, 'margin:0 0 12px 0') + '</div>'
            '<hr style="border:0;border-top:1px solid #8c8c8c;margin:22px 0 16px 0">'
            f'<div style="{fonte}font-size:8pt;color:#4a4a4a;line-height:1.5">' + paragrafos(aviso, 'margin:0 0 14px 0') + '</div></body></html>')


def enviar(destinos: list[str], assunto: str, corpo: str, anexos: list[tuple[str, bytes, str]], prioridade_alta: bool = False) -> dict:
    """anexos = [(nome_do_arquivo, bytes, mime 'tipo/subtipo')]. Devolve {'message_id', 'recusados': {email: motivo}, 'servidor'} para o historico de envios."""
    if not configurado():
        raise RuntimeError('E-mail não configurado: preencha no .env ' + ', '.join(faltando()) + '.')
    host, porta = os.environ['SMTP_HOST'], int(os.getenv('SMTP_PORT', '587'))
    usuario, senha = os.environ['SMTP_USER'], os.environ['SMTP_PASSWORD']
    msg = EmailMessage()
    msg['Subject'] = assunto
    msg['From'] = os.getenv('SMTP_FROM') or usuario
    msg['To'] = ', '.join(destinos)
    msg['Message-ID'] = make_msgid(domain=(usuario.split('@')[-1] if '@' in usuario else None))
    if prioridade_alta:
        msg['X-Priority'] = '1 (Highest)'
        msg['Importance'] = 'High'
        msg['X-MSMail-Priority'] = 'High'
    msg.set_content(corpo)
    html = _html_com_aviso(corpo)
    if html:
        msg.add_alternative(html, subtype='html')
    for nome, dados, mime in anexos:
        tipo, sub = mime.split('/', 1)
        msg.add_attachment(dados, maintype=tipo, subtype=sub, filename=nome)
    ctx = ssl.create_default_context()
    if porta == 465:
        with smtplib.SMTP_SSL(host, porta, context=ctx, timeout=60) as s:
            s.login(usuario, senha)
            recusados = s.send_message(msg)
    else:
        with smtplib.SMTP(host, porta, timeout=60) as s:
            s.starttls(context=ctx)
            s.login(usuario, senha)
            recusados = s.send_message(msg)
    return dict(message_id=msg['Message-ID'], recusados={k: (v[1].decode(errors='replace') if isinstance(v[1], bytes) else str(v[1])) for k, v in (recusados or {}).items()},
                servidor=host, de=msg['From'], anexos=[(n, len(d)) for n, d, _ in anexos])


AVISO_LEGAL = (
    'AVISO LEGAL: Esta mensagem, incluindo seus anexos, é destinada exclusivamente para a(s) pessoa(s) a quem é dirigida, podendo conter informação confidencial e/ou privilegiada. '
    'Se você não for destinatário desta mensagem, desde já fica notificado de abster-se de utilizar a informação contida nesta mensagem de qualquer forma, sujeitando o infrator às penas da lei; '
    'notificar o remetente e eliminar o seu conteúdo de forma definitiva. Informações transmitidas por e-mail podem ser alteradas por terceiros, não havendo garantia de que sua integridade foi mantida '
    'e que esteja livre de vírus, interceptação ou interferência, não podendo ser imputada qualquer responsabilidade à Gestão Conexão com relação ao seu conteúdo;\n\n'
    'LEGAL NOTICE: This message, including its attachments, is intended exclusively for the people to whom it is addressed, and may contain confidential and/or privileged information. '
    'If you are not a recipient of this message, you are hereby notified to refrain from using the information contained in this message, subjecting the infringer to the penalties of the law. '
    'Information transmitted by e-mail may be changed by third parties, and there is no guarantee that its integrity has been maintained and that it is free of viruses, interception or interference, '
    'and no responsibility will be attributed to connection management in relation to its content;\n\n'
    'AVISO LEGAL: Este mensaje, incluyendo sus anexos, está destinado exclusivamente a las personas a quienes se dirige, y puede contener información confidencial y / o privilegiada. '
    'Si usted no es un destinatario de este mensaje, por la presente se le notifica que se abstenga de usar la información contenida en este mensaje, sometiendo al infractor a las penas de la ley. '
    'La información transmitida por correo electrónico puede ser modificada por terceros, y no hay garantía de que su integridad ha sido mantenida y que está libre de virus, interceptación o interferencia, '
    'y ninguna responsabilidad será atribuida a gestión de conexión en relación con su contenido.'
)


def corpo_padrao(ref: str, gerado: str, automatico: bool = False, titulo: str = 'Relatório de Faturamento') -> str:
    """Texto do e-mail do Relatorio de Faturamento (ref = 'Outubro/2026', gerado = '13/10/2026 11:15')."""
    dia, _, hora = gerado.partition(' ')
    return (
        'Prezados,\n\n'
        f'Segue em anexo o {titulo} de {ref}, gerado em {dia} às {hora}.\n\n'
        'Atenciosamente,\n'
        'Gestão Conexão\n'
        'www.gestaoconexao.com.br\n\n'
        '--\n' + AVISO_LEGAL
    )
