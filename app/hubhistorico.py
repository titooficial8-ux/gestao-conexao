"""Migracao 100% dos historicos do PCP Hub para o banco do Gestao (leitura no Hub, gravacao so aqui; idempotente).

1) TODA linha de TODA tabela de historico/registro do Hub vai, completa (JSON), para `hub_arquivo`.
2) As que o Gestao ja usa em telas proprias (Historico de producao, ciclos diarios, Saved Drawing) tambem entram nas tabelas tipadas, sem duplicar.
Conferencia: `conferir()` compara a contagem do Hub com a do Gestao tabela a tabela.
"""
import json
from datetime import datetime, timezone

import requests

from app import db
from app.models import ArticleDailyCycle, HistoricoProducao, HubArquivo, SavedDrawingHistory, SavedDrawingSaveLog
from app import pcphub

# tabela do Hub -> coluna de data usada como referencia
TABELAS = {
    'production_history': 'changed_at',
    'machine_client_date_history': 'changed_at',
    'article_daily_cycles': 'created_at',
    'article_daily_avg': 'created_at',
    'machine_stops': 'started_at',
    'maintenance_notices': 'created_at',
    'sample_notices': 'created_at',
    'sample_notices_history': 'changed_at',
    'weekend_machine_runs': 'created_at',
    'saved_drawings_history': 'created_at',
    'saved_drawings_save_log': 'saved_at',
    'article_alerts_history': 'created_at',
    'machine_comments': 'created_at',
    'op_comments': 'created_at',
    'article_comments': 'created_at',
    'priority_queue': 'created_at',
    'shift_reports': 'created_at',
}


def _dt(v):
    """ISO do Hub (com fuso) -> datetime ingenuo em UTC (o Gestao guarda UTC e converte na tela)."""
    if not v:
        return None
    try:
        d = datetime.fromisoformat(str(v).replace('Z', '+00:00'))
    except ValueError:
        return None
    return d.astimezone(timezone.utc).replace(tzinfo=None) if d.tzinfo else d


def _d(v):
    x = _dt(v)
    return x.date() if x else None


def _ler(tabela: str, cab: dict) -> list:
    linhas, ini = [], 0
    while True:
        r = requests.get(f'{pcphub.URL}/rest/v1/{tabela}', params=dict(select='*', order='id.asc'),
                         headers=dict(cab, Range=f'{ini}-{ini + 999}', **{'Range-Unit': 'items'}), timeout=120)
        if r.status_code not in (200, 206):
            raise RuntimeError(f'{tabela}: HTTP {r.status_code} {r.text[:160]}')
        bloco = r.json()
        linhas += bloco
        if len(bloco) < 1000:
            return linhas
        ini += 1000


def _arquivar(tabela: str, linhas: list) -> int:
    ja = {h for (h,) in db.session.query(HubArquivo.hub_id).filter_by(tabela=tabela)}
    col = TABELAS[tabela]
    novas = 0
    for x in linhas:
        hid = str(x.get('id'))
        if hid in ja:
            continue
        db.session.add(HubArquivo(tabela=tabela, hub_id=hid, data_ref=_dt(x.get(col)), dados=json.dumps(x, ensure_ascii=False, default=str)))
        novas += 1
    db.session.commit()
    return novas


def _tipadas(tabela: str, linhas: list) -> int:
    """Copia para as tabelas que as telas do Gestao leem, pulando o que ja existe (mesma chave natural)."""
    n = 0
    if tabela == 'production_history':
        ja = {(h.machine, h.op, h.old_status, h.new_status, h.changed_at) for h in HistoricoProducao.query.all()}
        for x in linhas:
            ch = _dt(x.get('changed_at'))
            k = (x.get('machine'), x.get('op'), x.get('old_status'), x.get('new_status'), ch)
            if k in ja:
                continue
            ja.add(k)
            db.session.add(HistoricoProducao(
                machine=x.get('machine'), date=_d(x.get('date')), artigo=x.get('article'), cliente=x.get('client'), op=x.get('op'),
                quantity_pieces=x.get('quantity_pieces') or 0, requested_quantity=x.get('requested_quantity') or 0, pieces_per_day=x.get('pieces_per_day') or 0,
                old_status=x.get('old_status'), new_status=x.get('new_status'), changed_at=ch, reason=(x.get('reason') or None) and str(x['reason'])[:255],
                estimated_end_date=_d(x.get('estimated_end_date')), client_date=_d(x.get('client_date')), total_pcs=x.get('total_pcs')))
            n += 1
    elif tabela == 'article_daily_cycles':
        ja = {(c.article, c.machine, c.op, c.cycle_date) for c in ArticleDailyCycle.query.all()}
        for x in linhas:
            k = (x.get('article'), x.get('machine'), x.get('op'), _d(x.get('cycle_date')))
            if k in ja:
                continue
            ja.add(k)
            db.session.add(ArticleDailyCycle(article=x.get('article'), machine=x.get('machine'), op=x.get('op'), cycle_date=k[3], pieces=x.get('pieces') or 0, meters=float(x.get('meters') or 0)))
            n += 1
    elif tabela == 'saved_drawings_history':
        ja = {(h.machine, h.article) for h in SavedDrawingHistory.query.all()}
        for x in linhas:
            k = (x.get('machine'), x.get('article'))
            if k in ja:
                continue
            ja.add(k)
            db.session.add(SavedDrawingHistory(machine=k[0], article=k[1], source=x.get('source'), created_at=_dt(x.get('created_at')) or datetime.utcnow()))
            n += 1
    elif tabela == 'saved_drawings_save_log':
        ja = {(h.machine, h.op, h.article, h.saved_at, h.action) for h in SavedDrawingSaveLog.query.all()}
        for x in linhas:
            sa = _dt(x.get('saved_at'))
            k = (x.get('machine'), x.get('op'), x.get('article'), sa, x.get('action'))
            if k in ja:
                continue
            ja.add(k)
            db.session.add(SavedDrawingSaveLog(machine=k[0], op=k[1], article=k[2], client=x.get('client'), observation=x.get('observation'),
                                               saved_by_name=x.get('saved_by_name'), saved_by_email=x.get('saved_by_email'), saved_at=sa, action=x.get('action')))
            n += 1
    db.session.commit()
    return n


def importar() -> dict:
    """Le tudo do Hub e grava no Gestao. Devolve, por tabela, quantas linhas o Hub tem, quantas entraram agora e quantas o Gestao tem no total."""
    token = pcphub._login()
    cab = dict(apikey=pcphub.CHAVE, Authorization=f'Bearer {token}')
    res = {}
    for tabela in TABELAS:
        try:
            linhas = _ler(tabela, cab)
            novas = _arquivar(tabela, linhas)
            tip = _tipadas(tabela, linhas)
            res[tabela] = dict(hub=len(linhas), novas=novas, tabela_propria=tip, gestao=HubArquivo.query.filter_by(tabela=tabela).count())
        except Exception as e:  # noqa: BLE001
            db.session.rollback()
            res[tabela] = dict(erro=str(e)[:200])
    return res


def conferir(res: dict) -> bool:
    return all('erro' not in r and r['hub'] == r['gestao'] for r in res.values())
