"""Gestao da Malharia > PREVIA PCP: previsao de metros que as maquinas vao tirar nos proximos dias.

Conta por maquina, em cima da fila de OPs da Programacao: pecas por dia da OP (voltas/RPM ou raport x RPM, ja com a eficiencia; calculado em app/previsao.py)
x METRAGEM PADRAO da OP (DS.METROS), hora a hora, descontando: troca de artigo entre OPs (TROCA_HORAS), domingos e dias sem producao, amostras (ate 18h) e manutencao
preventiva programada. Maquina em manutencao/inoperante nao entra. E uma estimativa para o PCP planejar, nao e o realizado."""
from __future__ import annotations

from datetime import datetime, timedelta

from flask import Blueprint, abort, jsonify, render_template, request
from flask_login import current_user, login_required

from app import db
from app.models import Machine, NonWorkingDay, Sequencia, Tab

previa_bp = Blueprint('previa_pcp', __name__, url_prefix='/malharia/previa-pcp')
MAX_DIAS = 31


def _autorizar():
    tabs = Tab.query.filter(Tab.slug.in_(['previa-pcp', 'gestao-malharia'])).all()
    if not any(t.visible_to(current_user) for t in tabs):
        abort(403)


def _ds_metros(s) -> float:
    from app.totvs.movimentos import _metro_padrao
    try:
        v = _metro_padrao(s)
    except Exception:  # noqa: BLE001
        v = None
    if not v:
        try:
            from app.main.routes import resolve_metros_from_article
            v = float(str(resolve_metros_from_article(s.artigo) or '').replace(',', '.') or 0)
        except (ValueError, TypeError):
            v = 0
    return float(v or 0)


def projetar(dias: int = 14, agora: datetime | None = None, so_maquina: int | None = None) -> dict:
    from app import previsao
    from app.dias_producao import domingos
    agora = (agora or datetime.now()).replace(minute=0, second=0, microsecond=0)
    dias = max(1, min(int(dias), MAX_DIAS))
    desloc = timedelta(hours=6)                                  # o dia de producao comeca as 6h (mesma regra do calculo de entregas)
    dia0 = (agora - desloc).date()
    datas = [dia0 + timedelta(days=i) for i in range(dias + 1)]
    parados_g = {d.date for d in NonWorkingDay.query.filter(NonWorkingDay.date >= dia0).all() if d.machine_id is None} | domingos(dia0, dia0 + timedelta(days=dias + 2))
    maquinas, tot_dia = [], {d: 0.0 for d in datas}
    for m in Machine.query.order_by(Machine.number).all():
        if so_maquina and m.number != so_maquina:
            continue
        info = dict(maquina=m.number, status=m.status, metros_dia={}, total=0.0, pecas=0.0, fila=[], aviso='')
        if m.status in Machine.STATUS_FORA_DE_OPERACAO:
            info['aviso'] = 'Máquina em manutenção/inoperante: fora da previsão'
            maquinas.append(info)
            continue
        seqs = [s for s in Sequencia.query.filter_by(machine_id=m.id).all() if (s.status or '') in previsao.ATIVAS]
        seqs.sort(key=lambda s: (s.sequence_order if s.sequence_order is not None else 10 ** 6, s.id))
        if not seqs:
            info['aviso'] = 'Sem OP programada'
            maquinas.append(info)
            continue
        parados = parados_g | {d.date for d in NonWorkingDay.query.filter(NonWorkingDay.machine_id == m.id, NonWorkingDay.date >= dia0).all()}
        janelas = previsao.janelas_parada(m.number, agora)
        fila = []
        for s in seqs:
            restam = max((s.total_pcs or 0) + (s.pecas_extra or 0) - (s.pecas_produzidas or 0), 0)
            fila.append(dict(s=s, restam=float(restam), pd=float(s.pecas_dia or 0), metro=_ds_metros(s)))
        sem_base = [f for f in fila if f['pd'] <= 0 or f['metro'] <= 0]
        i, troca = 0, (0.0 if (seqs[0].status == 'PRODUZINDO') else previsao.TROCA_HORAS)
        por_dia = {d: 0.0 for d in datas}
        pecas = 0.0
        for h in range(dias * 24):
            t = agora + timedelta(hours=h)
            if i >= len(fila):
                break
            d = (t - desloc).date()
            if (t - timedelta(hours=6)).date() in parados or any(a <= t < b for a, b in janelas):
                continue
            livre = 1.0
            if troca > 0:
                uso = min(livre, troca)
                troca -= uso
                livre -= uso
            f = fila[i]
            if f['pd'] <= 0 or f['metro'] <= 0:                       # sem base de calculo: nao projeta e segue para a proxima
                i += 1
                troca = previsao.TROCA_HORAS
                continue
            if livre > 0:
                prod = min(livre * f['pd'] / 24, f['restam'])
                f['restam'] -= prod
                pecas += prod
                por_dia[d] = por_dia.get(d, 0.0) + prod * f['metro']
                if f['restam'] <= 1e-6:
                    i += 1
                    troca = previsao.TROCA_HORAS
        info['metros_dia'] = {k.isoformat(): round(v, 1) for k, v in por_dia.items() if k in tot_dia}
        info['total'] = round(sum(por_dia.values()), 1)
        info['pecas'] = round(pecas, 1)
        for d, v in por_dia.items():
            if d in tot_dia:
                tot_dia[d] += v
        f0 = fila[0]
        info['fila'] = [dict(op=x['s'].op, artigo=x['s'].artigo, ds_metros=x['metro'] or None, pecas_dia=round(x['pd'], 1) or None, restam=round(max((x['s'].total_pcs or 0) + (x['s'].pecas_extra or 0) - (x['s'].pecas_produzidas or 0), 0)))
                        for x in fila[:3]]
        if sem_base:
            info['aviso'] = f'{len(sem_base)} OP(s) sem base de cálculo (voltas/RPM ou DS.METROS): ficam de fora'
        maquinas.append(info)
    total = round(sum(tot_dia.values()), 1)
    from app.main.routes import _get_or_create_quality_settings
    try:
        meta = float(_get_or_create_quality_settings().meta_dia or 0)
    except Exception:  # noqa: BLE001
        meta = 0.0
    ativos = [d for d in datas[:-1] if d not in parados_g]
    return dict(gerado_em=agora.isoformat(timespec='minutes'), dias=dias, datas=[d.isoformat() for d in datas], meta_dia=meta, total=total, total_por_dia={d.isoformat(): round(v, 1) for d, v in tot_dia.items()},
                media_dia_util=round(total / len(ativos), 1) if ativos else 0.0, maquinas=maquinas, maquinas_projetadas=sum(1 for m in maquinas if m['total'] > 0))


@previa_bp.route('/')
@login_required
def pagina():
    _autorizar()
    return render_template('main/previa_pcp.html')


@previa_bp.route('/dados')
@login_required
def dados():
    _autorizar()
    try:
        dias = int(request.args.get('dias', 14))
    except ValueError:
        dias = 14
    maq = request.args.get('maquina', type=int)
    return jsonify(ok=True, **projetar(dias, so_maquina=maq))
