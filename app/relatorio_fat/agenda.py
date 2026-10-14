"""Envio automatico do Relatorio de Faturamento por e-mail, uma vez por dia no horario combinado.

Padrao: de segunda a sexta, as 08:00 (horario de Brasilia), em PDF, para os e-mails de RELATORIO_FAT_DESTINOS (lista abaixo). Ajustes no .env:
  RELATORIO_FAT_ENVIO=1                  (0 desliga o envio automatico)
  RELATORIO_FAT_ENVIO_HORA=08:00         (horario de Brasilia)
  RELATORIO_FAT_ENVIO_ATE=12:00          (se o Gestao so ligar depois disso, pula o dia em vez de mandar tarde)
  RELATORIO_FAT_ENVIO_DIAS=0,1,2,3,4    (0 = segunda ... 6 = domingo)
  RELATORIO_FAT_ENVIO_FORMATO=pdf        (pdf, pptx ou ambos)
  RELATORIO_FAT_DESTINOS=a@x.com,b@y.com
Nao repete no mesmo dia: a data do ultimo envio fica em instance/relatorio_fat_envio.json. Se o envio falhar (e-mail nao configurado, sem
dados do TOTVS...), tenta de novo a cada 30 minutos ate o limite do dia (ENVIO_ATE).
"""
from __future__ import annotations

import json
import os
import threading
import time
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

DESTINOS_PADRAO = ','.join([
    'luiz.lago@conexaomalhas.com.br', 'marco@conexaomalhas.com.br', 'carlos.ruiz@conexaomalhas.com.br', 'homero.vieira@conexaomalhas.com.br',
    'anderson@conexaomalhas.com.br', 'luciano@conexaomalhas.com.br', 'edmar.santos@conexaomalhas.com.br', 'mauricio@conexaomalhas.com.br',
    'dora.appolinario@conexaomalhas.com.br', 'luciane.frias@conexaomalhas.com.br'])
FUSO = ZoneInfo('America/Sao_Paulo')


def agora_brasilia() -> datetime:
    """Hora de Brasilia (sem fuso no objeto), qualquer que seja o fuso do computador."""
    return datetime.now(FUSO).replace(tzinfo=None)

ESTADO = dict(ultimo_envio=None, ultima_tentativa=None, erro=None)
_trava = threading.Lock()


def _arquivo(app) -> Path:
    return Path(app.instance_path) / 'relatorio_fat_envio.json'


def _ler(app) -> dict:
    try:
        return json.loads(_arquivo(app).read_text(encoding='utf-8'))
    except Exception:  # noqa: BLE001
        return {}


def _gravar(app, d: dict) -> None:
    try:
        _arquivo(app).parent.mkdir(parents=True, exist_ok=True)
        _arquivo(app).write_text(json.dumps(d), encoding='utf-8')
    except OSError:
        pass


def _hm(txt, padrao):
    try:
        h, m = [int(x) for x in str(txt).strip().split(':')]
        return h, m
    except ValueError:
        return padrao


def configuracao() -> dict:
    h, m = _hm(os.getenv('RELATORIO_FAT_ENVIO_HORA', '08:00'), (8, 0))
    try:
        dias = {int(x) for x in os.getenv('RELATORIO_FAT_ENVIO_DIAS', '0,1,2,3,4').split(',') if x.strip() != ''}
    except ValueError:
        dias = {0, 1, 2, 3, 4}
    return dict(ativo=os.getenv('RELATORIO_FAT_ENVIO', '1') != '0', hora=(h, m), dias=dias,
                ate=_hm(os.getenv('RELATORIO_FAT_ENVIO_ATE', '12:00'), (12, 0)),
                formato=os.getenv('RELATORIO_FAT_ENVIO_FORMATO', 'pdf').lower(),
                destinos=os.getenv('RELATORIO_FAT_DESTINOS', DESTINOS_PADRAO))


def enviar_agora(app) -> dict:
    """Gera o relatorio do mes mais recente e envia. Devolve {'ok', 'enviado_para'|'erro'}."""
    from app.relatorio_fat import dados, envio_email as envio
    from app.relatorio_fat.routes import MIME, _montar
    cfg = configuracao()
    if not envio.configurado():
        return dict(ok=False, erro='E-mail não configurado: preencha no .env ' + ', '.join(envio.faltando()))
    destinos = envio.validar(cfg['destinos'])
    formatos = {'pdf': ['pdf'], 'pptx': ['pptx'], 'ambos': ['pdf', 'pptx']}.get(cfg['formato'], ['pdf'])
    with app.app_context():
        anexos, ref = [], None
        for f in formatos:
            r = _montar(f, None, None)
            if not r:
                return dict(ok=False, erro='Ainda não há dados de faturamento carregados do TOTVS.')
            anexos.append((r[1], r[0], MIME[f]))
            ref = f'{dados.MESES[r[2] - 1]}/{r[3]}'
    envio.enviar(destinos, f'Relatório de Faturamento — {ref}',
                 envio.corpo_padrao(ref, f'{agora_brasilia():%d/%m/%Y %H:%M}', True), anexos)
    return dict(ok=True, enviado_para=destinos)


def _precisa_enviar(app, agora: datetime) -> bool:
    cfg = configuracao()
    if not cfg['ativo'] or agora.weekday() not in cfg['dias']:
        return False
    alvo = agora.replace(hour=cfg['hora'][0], minute=cfg['hora'][1], second=0, microsecond=0)
    if agora < alvo or agora > agora.replace(hour=cfg['ate'][0], minute=cfg['ate'][1], second=0, microsecond=0):
        return False
    est = _ler(app)
    if est.get('enviado_em') == agora.date().isoformat():
        return False
    ult = est.get('tentativa')
    if ult and est.get('erro') and (agora - datetime.fromisoformat(ult)) < timedelta(minutes=30):
        return False
    return True


def _tick(app) -> None:
    agora = agora_brasilia()
    if not _precisa_enviar(app, agora):
        return
    if not _trava.acquire(blocking=False):
        return
    try:
        est = _ler(app)
        try:
            r = enviar_agora(app)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            r = dict(ok=False, erro=str(e)[:300])
        est['tentativa'] = agora.isoformat(timespec='seconds')
        if r['ok']:
            est.update(enviado_em=agora.date().isoformat(), erro=None, para=r['enviado_para'])
            print(f'[relatorio-faturamento] enviado automaticamente para {", ".join(r["enviado_para"])}', flush=True)
        else:
            est['erro'] = r['erro']
            print(f'[relatorio-faturamento] envio automatico falhou: {r["erro"]}', flush=True)
        ESTADO.update(ultimo_envio=est.get('enviado_em'), ultima_tentativa=est['tentativa'], erro=est.get('erro'))
        _gravar(app, est)
    finally:
        _trava.release()


def iniciar_agendador(app) -> None:
    def loop():
        time.sleep(45)
        while True:
            try:
                _tick(app)
            except Exception:  # noqa: BLE001
                traceback.print_exc()
            time.sleep(60)
    threading.Thread(target=loop, daemon=True, name='relatorio-fat-envio').start()
