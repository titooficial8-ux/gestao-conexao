"""Ponte provisoria: traz do PCP Hub as pesagens do Relatorio 32 (o agente do Christian exporta o relatorio do TOTVS para a tabela report_32_movement) para o banco do Gestao.

So para a producao (Controle de Producao, Gestao da Malharia e Tempo Real). Leitura apenas. Quando a BHAN liberar uma rota da API do TOTVS para esse relatorio, e so trocar a fonte.
.env (opcional): RELATORIO32_INTERVALO_SEG=60 (padrao), RELATORIO32_DIAS=3 (janela re-lida a cada rodada), RELATORIO32_DIAS_INICIAL=60 (primeira carga).
"""
import os
import threading
import time
import traceback
from datetime import datetime, timedelta, timezone

import requests

from app import db, pcphub
from app.models import Report32Movement

ESTADO = dict(rodando=False, ultima=None, erro=None, lidas=0, novas=0, removidas=0, duracao=None)
_trava = threading.Lock()
CAMPOS = 'ciclo,op,nr_item,nr_lote,cod_artigo,metro_padrao,gramatura,largura,local_code,qt_movimento,qt_pesos,cod_maquina,dt_real'


def _utc(v):
    """ISO com fuso do Hub -> datetime ingenuo em UTC (como o Gestao guarda dt_real)."""
    d = datetime.fromisoformat(str(v).replace('Z', '+00:00'))
    return d.astimezone(timezone.utc).replace(tzinfo=None) if d.tzinfo else d


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _ler_hub(desde: datetime) -> list:
    token = pcphub._login()
    cab = dict(apikey=pcphub.CHAVE, Authorization=f'Bearer {token}')
    linhas, ini = [], 0
    while True:
        r = requests.get(f'{pcphub.URL}/rest/v1/report_32_movement', params=dict(select=CAMPOS, dt_real=f'gte.{desde.replace(tzinfo=timezone.utc).isoformat()}', order='dt_real.asc,id.asc'),
                         headers=dict(cab, Range=f'{ini}-{ini + 999}', **{'Range-Unit': 'items'}), timeout=90)
        if r.status_code not in (200, 206):
            raise RuntimeError(f'Relatorio 32 no Hub: HTTP {r.status_code} {r.text[:160]}')
        bloco = r.json()
        linhas += bloco
        if len(bloco) < 1000:
            return linhas
        ini += 1000


def _chave(op, item, lote, dt):
    return (str(op or ''), str(item or ''), str(lote or ''), dt)


def sincronizar(dias: int = None) -> dict:
    """Le do Hub as pesagens dos ultimos `dias` e acerta o banco do Gestao (insere as novas, apaga as que o Hub tirou). Precisa de app_context."""
    agora = datetime.utcnow()
    ult = db.session.query(db.func.max(Report32Movement.dt_real)).scalar()
    if dias is None:
        dias = int(os.getenv('RELATORIO32_DIAS', '3'))
        if not ult or ult < agora - timedelta(days=7):               # banco vazio ou muito atrasado: primeira carga maior
            dias = int(os.getenv('RELATORIO32_DIAS_INICIAL', '60'))
    desde = (agora - timedelta(days=dias)).replace(hour=0, minute=0, second=0, microsecond=0)
    linhas = _ler_hub(desde)
    existentes = {_chave(r.op, r.nr_item, r.nr_lote, r.dt_real): r for r in Report32Movement.query.filter(Report32Movement.dt_real >= desde).all()}
    novas, vistas = 0, set()
    for x in linhas:
        dt = _utc(x['dt_real'])
        k = _chave(x.get('op'), x.get('nr_item'), x.get('nr_lote'), dt)
        vistas.add(k)
        if k in existentes:
            continue
        db.session.add(Report32Movement(ciclo=x.get('ciclo'), op=str(x.get('op') or ''), nr_lote=str(x.get('nr_lote') or ''), nr_item=str(x.get('nr_item') or ''), cod_artigo=x.get('cod_artigo'),
                                        metro_padrao=_num(x.get('metro_padrao')), gramatura=_num(x.get('gramatura')), largura=_num(x.get('largura')), local_code=str(x.get('local_code') or '23'),
                                        qt_movimento=_num(x.get('qt_movimento')), qt_pesos=_num(x.get('qt_pesos')), cod_maquina=str(x.get('cod_maquina') or '') or None, dt_real=dt))
        novas += 1
    removidas = 0
    if linhas:                                                          # nunca apaga tudo se a leitura veio vazia
        for k, r in existentes.items():
            if k not in vistas:
                db.session.delete(r)
                removidas += 1
    db.session.commit()
    return dict(lidas=len(linhas), novas=novas, removidas=removidas, desde=desde.isoformat())


def atualizar(app) -> bool:
    if not _trava.acquire(blocking=False):
        return False
    t0 = time.time()
    ESTADO.update(rodando=True, erro=None)
    try:
        with app.app_context():
            r = sincronizar()
        ESTADO.update(ultima=datetime.now().isoformat(timespec='seconds'), lidas=r['lidas'], novas=r['novas'], removidas=r['removidas'])
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        ESTADO['erro'] = str(e)[:300]
        try:
            db.session.rollback()
        except Exception:  # noqa: BLE001
            pass
    finally:
        ESTADO.update(rodando=False, duracao=round(time.time() - t0, 1))
        _trava.release()
    return ESTADO['erro'] is None


def iniciar_agendador(app) -> None:
    if not pcphub.configurado():
        ESTADO['erro'] = 'PCP Hub nao configurado (PCPHUB_EMAIL / PCPHUB_PASSWORD no .env)'
        return
    intervalo = max(20, int(os.getenv('RELATORIO32_INTERVALO_SEG', '60')))

    def loop():
        time.sleep(10)
        while True:
            try:
                atualizar(app)
            except Exception:  # noqa: BLE001
                traceback.print_exc()
            time.sleep(intervalo)
    threading.Thread(target=loop, daemon=True, name='relatorio32-hub').start()


def ultima_pesagem():
    """dt_real (UTC) da pesagem mais recente que a ponte trouxe do Hub."""
    return db.session.query(db.func.max(Report32Movement.dt_real)).scalar()


def em_dia(max_atraso_horas: float = None) -> bool:
    """A ponte esta alimentando o banco? Tem pesagem recente (padrao 8 h: a fabrica produz dia e noite). Se o agente do Hub parou, o Controle usa as baixas de fio em vez de mostrar zero."""
    limite = float(os.getenv('RELATORIO32_MAX_ATRASO_H', '8')) if max_atraso_horas is None else max_atraso_horas
    ult = ultima_pesagem()
    return bool(ult and ult >= datetime.utcnow() - timedelta(hours=limite))
