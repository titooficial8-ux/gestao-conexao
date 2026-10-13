"""Envio do Relatorio de Faturamento por e-mail (SMTP). As credenciais ficam so no .env do computador, nunca no codigo."""
from __future__ import annotations

import os
import re
import smtplib
import ssl
from email.message import EmailMessage

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


def enviar(destinos: list[str], assunto: str, corpo: str, anexos: list[tuple[str, bytes, str]]) -> None:
    """anexos = [(nome_do_arquivo, bytes, mime 'tipo/subtipo')]."""
    if not configurado():
        raise RuntimeError('E-mail não configurado: preencha no .env ' + ', '.join(faltando()) + '.')
    host, porta = os.environ['SMTP_HOST'], int(os.getenv('SMTP_PORT', '587'))
    usuario, senha = os.environ['SMTP_USER'], os.environ['SMTP_PASSWORD']
    msg = EmailMessage()
    msg['Subject'] = assunto
    msg['From'] = os.getenv('SMTP_FROM') or usuario
    msg['To'] = ', '.join(destinos)
    msg.set_content(corpo)
    for nome, dados, mime in anexos:
        tipo, sub = mime.split('/', 1)
        msg.add_attachment(dados, maintype=tipo, subtype=sub, filename=nome)
    ctx = ssl.create_default_context()
    if porta == 465:
        with smtplib.SMTP_SSL(host, porta, context=ctx, timeout=60) as s:
            s.login(usuario, senha)
            s.send_message(msg)
    else:
        with smtplib.SMTP(host, porta, timeout=60) as s:
            s.starttls(context=ctx)
            s.login(usuario, senha)
            s.send_message(msg)


def corpo_padrao(ref: str, gerado: str, automatico: bool = False) -> str:
    """Texto do e-mail do Relatorio de Faturamento (ref = 'Outubro/2026', gerado = '13/10/2026 11:15')."""
    como = 'enviado automaticamente pelo Gestão Conexão' if automatico else 'gerado pelo Gestão Conexão'
    return (
        'Prezados,\n\n'
        f'Segue em anexo o Relatório de Faturamento de {ref}, {como} em {gerado}.\n\n'
        'O relatório traz:\n'
        '  • Resumo do mês: receita bruta, deduções, devoluções, receita líquida e quanto falta para bater o plano;\n'
        '  • Volume em metros: total, devoluções, líquido e comparação com o plano;\n'
        '  • Receita, metros, quilos e preço médio por segmento, comparados ao plano e a 2025;\n'
        '  • Representantes, Ortobom por filial e Mercado internacional.\n\n'
        'Os números vêm direto das notas fiscais do TOTVS e do plano de vendas, e o mês corrente é parcial: '
        'ele considera as notas emitidas até o momento da geração.\n\n'
        'Qualquer dúvida, é só falar.\n\n'
        'Atenciosamente,\n'
        'Gestão Conexão\n'
        'Conexão Malhas e Desenvolvimento\n'
        'www.gestaoconexao.com.br\n\n'
        'Esta é uma mensagem gerada pelo sistema. Se for preciso responder, use o e-mail da equipe.'
    )
