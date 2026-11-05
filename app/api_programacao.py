"""API somente-leitura da PROGRAMACAO (tudo que esta programado, por maquina) para outros sistemas.

Autenticacao: cabecalho  Authorization: Bearer <token>  (ou  X-API-Key: <token>), com o token definido no .env da VPS:
    GESTAO_API_TOKEN=<texto longo e secreto>
Sem o token no .env a API fica desligada (503). Um administrador logado no navegador tambem pode abrir (para testar).

GET /api/v1/programacao                 -> TODAS as maquinas (inclusive as sem programacao) e suas sequencias (nao finalizadas/canceladas)
    ?maquina=48                         -> so uma maquina
    ?op=3135                            -> so uma OP
    ?status=PRODUZINDO|EM_SEQUENCIA|PCP -> filtra pelo status
    ?incluir_finalizadas=1              -> inclui FINALIZADO/CANCELADO
GET /api/v1/programacao/<op>            -> uma OP
GET /api/v1/pesagens                    -> pecas que passaram do local 23 para o 24 (a mesma fonte do Controle de Producao), por dia de producao (06h20 -> 06h19)
    ?dia=AAAA-MM-DD                     -> um dia de producao (padrao: o dia de producao de agora)
    ?de=AAAA-MM-DD&ate=AAAA-MM-DD       -> intervalo de dias de producao
    ?maquina=48  ?op=3135               -> filtros opcionais
"""
from __future__ import annotations

import hmac
import os

from flask import Blueprint, jsonify, request
from flask_login import current_user

from app import db  # noqa: F401
from app.models import Machine, Sequencia

api_prog_bp = Blueprint('api_prog', __name__, url_prefix='/api/v1')


def _autorizado() -> bool:
    token = (os.getenv('GESTAO_API_TOKEN') or '').strip()
    enviado = (request.headers.get('X-API-Key') or '').strip()
    auth = request.headers.get('Authorization') or ''
    if auth.lower().startswith('bearer '):
        enviado = auth[7:].strip()
    if token and enviado and hmac.compare_digest(token.encode(), enviado.encode()):
        return True
    return bool(current_user.is_authenticated and current_user.is_admin)


def _d(v):
    return v.isoformat() if v else None


def _linha(q: Sequencia) -> dict:
    try:
        from app.main.routes import resolve_metros_from_article
        ds_metros = float(str(resolve_metros_from_article(q.artigo) or '').replace(',', '.') or 0) or None
    except Exception:  # noqa: BLE001
        ds_metros = None
    return dict(
        id=q.id, ordem=q.sequence_order, op=q.op, artigo=q.artigo, cliente=q.cliente, estrutura=q.estrutura, ds_metros=ds_metros,
        status=q.status, situacao=q.situacao, total_pcs=(q.total_pcs or 0) + (q.pecas_extra or 0), pecas_produzidas=q.pecas_produzidas or 0, pc_produzir=q.pc_produzir,
        qt_solicitada=q.qt_solicitada, qt_avulsa=q.qt_avulsa, mes_producao=q.production_month, dt_cliente=_d(q.dt_cliente), termino_malharia=_d(q.termino_malharia),
        previsao_termino=_d(q.prev_termino_em), pecas_dia=q.pecas_dia, observacao_pcp=q.obs_pcp, origem=q.origem_reg or 'totvs', atualizado_em=_d(q.synced_at))


@api_prog_bp.route('/programacao')
@api_prog_bp.route('/programacao/<op>')
def programacao(op=None):
    if not (os.getenv('GESTAO_API_TOKEN') or '').strip() and not (current_user.is_authenticated and current_user.is_admin):
        return jsonify(erro='API desligada: defina GESTAO_API_TOKEN no .env do servidor.'), 503
    if not _autorizado():
        return jsonify(erro='Não autorizado: envie Authorization: Bearer <token>.'), 401
    op = op or request.args.get('op')
    q = Sequencia.query.join(Machine, Sequencia.machine_id == Machine.id)
    if request.args.get('maquina', '').isdigit():
        q = q.filter(Machine.number == int(request.args['maquina']))
    if op:
        q = q.filter(Sequencia.op == str(op))
    if request.args.get('status'):
        q = q.filter(Sequencia.status == request.args['status'].upper())
    if request.args.get('incluir_finalizadas') != '1' and not op:
        q = q.filter(db.or_(Sequencia.status.is_(None), Sequencia.status.notin_(['FINALIZADO', 'CANCELADO'])))
    linhas = q.order_by(Machine.number, Sequencia.sequence_order, Sequencia.id).all()
    maquinas = {}
    if not op and not request.args.get('status'):                   # TODAS as maquinas, mesmo as sem nada programado (sequencias vazias)
        qm = Machine.query.order_by(Machine.number)
        if request.args.get('maquina', '').isdigit():
            qm = qm.filter(Machine.number == int(request.args['maquina']))
        for m in qm.all():
            maquinas[m.number] = dict(maquina=m.number, status_maquina=m.status, sequencias=[])
    for s in linhas:
        m = s.machine
        g = maquinas.setdefault(m.number, dict(maquina=m.number, status_maquina=m.status, sequencias=[]))
        g['sequencias'].append(_linha(s))
    lista = [maquinas[k] for k in sorted(maquinas)]
    return jsonify(total_maquinas=len(lista), total_sequencias=len(linhas), maquinas=lista)


def _janela_producao(de, ate):
    """[inicio, fim) em UTC dos dias de producao `de`..`ate` (cada dia vai das 06:20 as 06:19 de Brasilia)."""
    from datetime import datetime, timedelta
    ini = datetime.combine(de, datetime.min.time()) + timedelta(hours=6, minutes=20) + timedelta(hours=3)
    fim = datetime.combine(ate + timedelta(days=1), datetime.min.time()) + timedelta(hours=6, minutes=20) + timedelta(hours=3)
    return ini, fim


@api_prog_bp.route('/pesagens')
def pesagens():
    """Pecas pesadas (movimento 23 -> 24): uma linha por peca, com maquina, OP, artigo, cliente, metros (DS.METROS), kg estimado e hora."""
    import json
    from datetime import date, datetime, timedelta, timezone
    if not (os.getenv('GESTAO_API_TOKEN') or '').strip() and not (current_user.is_authenticated and current_user.is_admin):
        return jsonify(erro='API desligada: defina GESTAO_API_TOKEN no .env do servidor.'), 503
    if not _autorizado():
        return jsonify(erro='Não autorizado: envie Authorization: Bearer <token>.'), 401
    brt = timezone(timedelta(hours=-3))
    agora = datetime.now(brt)
    try:
        if request.args.get('de') or request.args.get('ate'):
            de = date.fromisoformat(request.args.get('de') or request.args['ate'])
            ate = date.fromisoformat(request.args.get('ate') or request.args['de'])
        else:
            base = date.fromisoformat(request.args['dia']) if request.args.get('dia') else (agora - timedelta(hours=6, minutes=20)).date()
            de = ate = base
    except ValueError:
        return jsonify(erro='Datas no formato AAAA-MM-DD.'), 400
    if (ate - de).days > 31:
        return jsonify(erro='Intervalo máximo de 31 dias.'), 400
    ini, fim = _janela_producao(de, ate)
    from app.main.routes import _movimentos_atual, _preparar_resposta
    hub, api_txt = _movimentos_atual()
    _etag, resp = _preparar_resposta(hub, api_txt)
    maq, opf = (request.args.get('maquina') or '').strip().lstrip('0'), (request.args.get('op') or '').strip()
    saida = []
    for x in json.loads(resp['plano']):
        try:
            d = datetime.fromisoformat(str(x.get('dt_real')).replace('Z', '+00:00'))
        except ValueError:
            continue
        d = (d.astimezone(timezone.utc) if d.tzinfo else d.replace(tzinfo=timezone.utc)).replace(tzinfo=None)
        if not (ini <= d < fim):
            continue
        if maq and str(x.get('cod_maquina') or '').lstrip('0') != maq or opf and str(x.get('op')) != opf:
            continue
        local = d + timedelta(hours=-3)
        prod = (local - timedelta(hours=6, minutes=20)).date()
        minutos = local.hour * 60 + local.minute
        turno = 1 if 6 * 60 + 20 <= minutos < 14 * 60 + 20 else 2 if 14 * 60 + 20 <= minutos < 22 * 60 + 20 else 3
        saida.append(dict(maquina=x.get('cod_maquina'), op=x.get('op'), item=x.get('nr_item'), artigo=x.get('cod_artigo'), cliente=x.get('cliente'), metros=x.get('qt_movimento'),
                          metro_padrao=x.get('metro_padrao'), kg=x.get('qt_pesos'), local_origem=x.get('local_code') or '23', local_destino='24',
                          hora=local.strftime('%Y-%m-%dT%H:%M:%S'), dia_producao=prod.isoformat(), turno=turno))
    saida.sort(key=lambda r: (r['hora'], str(r['maquina']), str(r['op'])))
    return jsonify(de=de.isoformat(), ate=ate.isoformat(), total=len(saida), metros=round(sum(r['metros'] or 0 for r in saida), 2), kg=round(sum(r['kg'] or 0 for r in saida), 2), pesagens=saida)
