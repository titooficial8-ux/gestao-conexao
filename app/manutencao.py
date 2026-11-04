"""Amostras e Manutencoes das maquinas (antes no PCP Hub).

- Historico: tudo que o PCP Hub tinha (tabelas maintenance_notices e sample_notices, ja arquivadas em hub_arquivo) e importado para avisos_manut (idempotente).
- Ao vivo: a cada minuto le as ocorrencias em andamento do Acompanhamento de Producao. Quando o lider abre uma MANUTENCAO/AMOSTRA numa maquina, o aviso entra em andamento
  (se havia um aviso PROGRAMADO para a maquina, ele e quem comeca) e a maquina pisca como 'em manutencao' na Programacao; quando a ocorrencia fecha, o aviso e finalizado e vai para o Historico.
"""
from __future__ import annotations

import json
import re
import threading
import time
from datetime import date, datetime, timedelta, timezone

from flask import Blueprint, abort, current_app, jsonify, request
from flask_login import current_user, login_required

from app import db
from app.models import AvisoManut, HubArquivo, Tab

manut_bp = Blueprint('manut', __name__, url_prefix='/manutencoes')
TIPOS = ('MANUTENCAO', 'AMOSTRA')
BRT = timezone(timedelta(hours=-3))


def _autorizar():
    tab = Tab.query.filter_by(slug='amostras-manutencoes').first_or_404()
    if not tab.visible_to(current_user):
        abort(403)


def _edita() -> bool:
    if current_user.is_admin:
        return True
    nomes = {x.name for x in (current_user.sectors or [])}
    return bool(nomes & {'PCP', 'Produção', 'Desenvolvimento'})


# ---------------------------------------------------------------- historico do Hub -> Gestao
def _pega(d: dict, *trechos):
    """Primeiro valor nao vazio cuja chave contem um dos trechos (sem diferenciar maiusculas)."""
    for t in trechos:
        for k, v in d.items():
            if t in str(k).lower() and v not in (None, '', []):
                return v
    return None


def _dt(v):
    if not v:
        return None
    try:
        x = datetime.fromisoformat(str(v).replace('Z', '+00:00'))
    except ValueError:
        return None
    return x.astimezone(timezone.utc).replace(tzinfo=None) if x.tzinfo else x


def _num(v):
    try:
        return float(str(v).replace(',', '.'))
    except (TypeError, ValueError):
        return None


def _mapear(tabela: str, d: dict) -> dict:
    """Linha do Hub (JSON) -> campos do aviso. Reconhece as colunas pelo nome (machine, mechanic, estimated..., start..., end...)."""
    tipo = 'AMOSTRA' if 'sample' in tabela else 'MANUTENCAO'
    maq = _pega(d, 'machine', 'maquina')
    ini = _dt(_pega(d, 'started', 'start', 'inicio', 'begin'))
    fim = _dt(_pega(d, 'ended', 'end', 'finish', 'encerr', 'closed', 'fim'))
    dia = _dt(_pega(d, 'scheduled', 'date', 'data'))
    ativo = _pega(d, 'is_active', 'active', 'ativo')
    em_andamento = (str(ativo).lower() in ('true', '1', 'sim') and not fim) if ativo is not None else (ini is not None and not fim)
    return dict(tipo=tipo, maquina=int(re.sub(r'\D', '', str(maq)) or 0) or None, mecanico=_pega(d, 'mechanic', 'mecanic'), data=(dia or ini or fim).date() if (dia or ini or fim) else None,
                horas_estimadas=_num(_pega(d, 'estimated', 'hours', 'horas')), inicio=ini, fim=fim, obs=_pega(d, 'observation', 'notes', 'note', 'description', 'obs', 'reason', 'motivo'),
                status='EM_ANDAMENTO' if em_andamento else ('FINALIZADA' if fim or ini else 'PROGRAMADA'))


def importar_do_hub() -> dict:
    """Copia maintenance_notices e sample_notices (de hub_arquivo) para avisos_manut sem duplicar. Devolve contagens."""
    res = {}
    ja = {h for (h,) in db.session.query(AvisoManut.hub_id).filter(AvisoManut.origem == 'hub')}
    for tabela in ('maintenance_notices', 'sample_notices'):
        novos, total = 0, 0
        for h in HubArquivo.query.filter_by(tabela=tabela).all():
            total += 1
            chave = f'{tabela}:{h.hub_id}'
            if chave in ja:
                continue
            try:
                campos = _mapear(tabela, json.loads(h.dados))
            except Exception:  # noqa: BLE001
                continue
            if not campos['maquina']:
                continue
            db.session.add(AvisoManut(origem='hub', hub_id=chave, criado_por='PCP Hub', **campos))
            novos += 1
        db.session.commit()
        res[tabela] = dict(no_arquivo=total, importados=novos)
    return res


# ---------------------------------------------------------------- ao vivo: Acompanhamento de Producao
def _tipo_da_ocorrencia(x: dict):
    t = ((x.get('tipo') or '') + ' ' + (x.get('motivo') or '')).upper()      # o lider pode sinalizar como tipo 'PARADA' com motivo 'MANUTENÇÃO PREVENTIVA'
    if 'MANUT' in t:
        return 'MANUTENCAO'
    if 'AMOSTR' in t:
        return 'AMOSTRA'
    return None


def _abertas_setups() -> tuple:
    """Eventos abertos em /setups (SETUP, MANUTENCAO, AMOSTRA...): o card do Acompanhamento 'MANUTENÇÃO PREVENTIVA - 0 pç - desde 23:41' vem daqui. Devolve (itens, bruto, erro)."""
    from app import acompanhamento as ac
    hoje = datetime.now(BRT).date()
    itens, bruto, erro = [], [], None
    for dia in (hoje, hoje - timedelta(days=1)):
        try:
            lista = ac._lista(ac.chamar('/setups', {'de': dia.isoformat(), 'ate': dia.isoformat()}))
        except Exception as e:  # noqa: BLE001
            erro = str(e)[:160]
            continue
        for x in lista:
            if not isinstance(x, dict):
                continue
            bruto.append(x)
            texto = ' '.join(str(x.get(k) or '') for k in ('tipo', 'motivo', 'descricao', 'tipoSetup', 'status')).upper()
            st = str(x.get('status') or '').upper()
            fechado = bool(x.get('fim') or x.get('termino') or x.get('fimEm') or x.get('encerradoEm')) or any(k in st for k in ('FINALIZ', 'ENCERR', 'CONCLU', 'FECHAD'))
            maq = str(x.get('maquina') or x.get('machine') or x.get('maquina_id') or '')
            if fechado or not maq.isdigit() or ('MANUT' not in texto and 'AMOSTR' not in texto):
                continue
            ini = ac._local(x.get('inicio') or x.get('inicioEm') or x.get('criadoEm'))
            mins = int(max((datetime.now() - ini).total_seconds() // 60, 0)) if ini else 0
            itens.append(dict(id='s' + str(x.get('id')), maquina=str(int(maq)), tipo=str(x.get('tipo') or '').upper(), subtipo=str(x.get('subtipo') or '').upper(), motivo=x.get('motivo') or x.get('descricao') or '', minutos=mins,
                              aberta_por=x.get('abertaPor') or x.get('responsavel') or '', revisor=False))
    return itens, bruto, erro


def sincronizar() -> dict:
    """Uma rodada: ocorrencias abertas no Acompanhamento -> avisos em andamento; as que fecharam -> finalizadas."""
    from app import acompanhamento
    if not acompanhamento.configurado():
        return dict(ok=False, msg='Acompanhamento não configurado (ACOMP_TOKEN)')
    r = acompanhamento.abertas()
    if r.get('erro') and not r.get('itens'):
        return dict(ok=False, msg=r['erro'])
    agora = datetime.utcnow()
    abertas = {}
    extras, erro_setups = [], None
    try:
        extras, _b, erro_setups = _abertas_setups()
    except Exception as e:  # noqa: BLE001
        erro_setups = str(e)[:160]
    if erro_setups:
        r = dict(r, erro=r.get('erro') or erro_setups)           # leitura incompleta: nao finaliza nada nesta rodada
    for x in list(r.get('itens') or []) + extras:
        tipo = _tipo_da_ocorrencia(x)
        if not tipo or x.get('revisor') or not str(x.get('maquina') or '').isdigit():
            continue
        abertas[str(x['id'])] = (tipo, int(x['maquina']), x)
    criados = iniciados = 0
    for aid, (tipo, maq, x) in abertas.items():
        ja = AvisoManut.query.filter_by(acomp_id=aid).first()
        if ja:
            if ja.status == 'FINALIZADA' and ja.origem == 'acomp':     # o Acompanhamento ainda mostra a ocorrencia aberta: uma leitura ruim do passado a encerrou por engano -> reabre
                ja.status, ja.fim = 'EM_ANDAMENTO', None
                iniciados += 1
            continue
        ini = agora - timedelta(minutes=int(x.get('minutos') or 0))
        prog = AvisoManut.query.filter_by(tipo=tipo, maquina=maq, status='PROGRAMADA').order_by(AvisoManut.data).first()
        if prog:                                                    # havia manutencao/amostra programada: ela comeca quando o lider sinaliza
            prog.status, prog.inicio, prog.acomp_id = 'EM_ANDAMENTO', ini, aid
            prog.subtipo = x.get('subtipo') or prog.subtipo
            iniciados += 1
        else:
            db.session.add(AvisoManut(tipo=tipo, maquina=maq, data=ini.replace(tzinfo=timezone.utc).astimezone(BRT).date(), inicio=ini, status='EM_ANDAMENTO', origem='acomp', acomp_id=aid,
                                      obs=x.get('motivo') or None, mecanico=None, subtipo=(x.get('subtipo') or None), criado_por=x.get('aberta_por') or 'Acompanhamento'))
            criados += 1
    finalizados = 0
    if not r.get('erro'):                                           # so finaliza com leitura boa: uma falha da API nao pode encerrar manutencao
        for av in AvisoManut.query.filter(AvisoManut.status == 'EM_ANDAMENTO', AvisoManut.acomp_id.isnot(None)).all():
            if av.acomp_id not in abertas:
                av.status, av.fim = 'FINALIZADA', agora
                av.data_termino = av.data_termino or agora.replace(tzinfo=timezone.utc).astimezone(BRT).date()      # o lider finalizou: a data de termino e a de hoje
                finalizados += 1
    db.session.commit()
    if criados or iniciados or finalizados:
        for m in {mq for (_t, mq, _x) in abertas.values()} | {av.maquina for av in AvisoManut.query.filter_by(status='FINALIZADA').order_by(AvisoManut.fim.desc()).limit(5).all()}:
            _recalcular(m)
    return dict(ok=True, criados=criados, iniciados=iniciados, finalizados=finalizados, abertas=len(abertas))


def iniciar_agendador(app) -> None:
    def loop():
        time.sleep(40)
        try:
            with app.app_context():
                if AvisoManut.query.filter_by(origem='hub').count() == 0 and HubArquivo.query.filter(HubArquivo.tabela.in_(['maintenance_notices', 'sample_notices'])).count():
                    importar_do_hub()                                   # 1a vez: traz o historico que o PCP Hub tinha
        except Exception as e:  # noqa: BLE001
            print(f'[manutencao] importacao inicial: {e}', flush=True)
        while True:
            try:
                with app.app_context():
                    sincronizar()
            except Exception as e:  # noqa: BLE001
                print(f'[manutencao] erro: {e}', flush=True)
                try:
                    with app.app_context():
                        db.session.rollback()
                except Exception:  # noqa: BLE001
                    pass
            time.sleep(60)
    if not any(t.name == 'manutencao-sync' for t in threading.enumerate()):
        threading.Thread(target=loop, daemon=True, name='manutencao-sync').start()


def em_manutencao() -> dict:
    """{numero da maquina: aviso} das maquinas com MANUTENCAO em andamento (a tela da Programacao pisca)."""
    return {a.maquina: a for a in AvisoManut.query.filter_by(tipo='MANUTENCAO', status='EM_ANDAMENTO').all() if a.maquina}


# ---------------------------------------------------------------- rotas
def _recalcular(maquina) -> None:
    """Refaz as previsoes de entrega da maquina (a parada de amostra/manutencao preventiva muda o termino das OPs). Roda em segundo plano."""
    if not maquina:
        return
    app = current_app._get_current_object()

    def tarefa():
        try:
            with app.app_context():
                from app import previsao
                previsao.calcular(maquina=int(maquina), esperar=True)
        except Exception as e:  # noqa: BLE001
            print(f'[manutencao] recalculo da maquina {maquina}: {e}', flush=True)
    threading.Thread(target=tarefa, daemon=True, name='recalculo-parada').start()


def amostras_ativas() -> dict:
    """{numero da maquina: 'desde dd/mm HH:MM'} das maquinas com AMOSTRA em andamento (faixa roxa na Programacao)."""
    out = {}
    try:
        for a in AvisoManut.query.filter_by(tipo='AMOSTRA', status='EM_ANDAMENTO').all():
            if a.maquina:
                ini = a.inicio.replace(tzinfo=timezone.utc).astimezone(BRT) if a.inicio else None
                out[a.maquina] = ('desde ' + ini.strftime('%d/%m %H:%M')) if ini else 'em andamento'
    except Exception:  # noqa: BLE001
        pass
    return out


<<<<<<< HEAD
def _impacto(a: AvisoManut) -> str:
    """Texto do que a parada tira da producao (usado no alerta da maquina)."""
    try:
        from app import previsao
        j = previsao.janelas_parada(a.maquina, datetime.now() - timedelta(days=400))
        mine = [x for x in j if x[0].date() == (a.data if a.tipo == 'AMOSTRA' else (a.data or a.data_termino))]
        if not mine:
            return 'Manutenção corretiva ou sem data de término: não entra no cálculo de entregas.' if a.tipo == 'MANUTENCAO' else ''
        ini, fim = mine[0]
        if a.tipo == 'AMOSTRA':
            h = (fim - ini).total_seconds() / 3600
            return f'Máquina sem produzir {int(h)}h{int(round((h - int(h)) * 60)):02d} (até {previsao.AMOSTRA_FIM_HORA}h) — entregas recalculadas.'
        dias = int(round((fim - ini).total_seconds() / 86400))
        return f'{dias} dia(s) sem produzir — entregas da máquina recalculadas.'
    except Exception:  # noqa: BLE001
        return ''


=======
>>>>>>> origin/main
def avisos_programados() -> dict:
    """{maquina: [dict(tipo, data, termino, mecanico, horas)]} dos avisos PROGRAMADOS (ainda nao comecaram), do mais proximo ao mais distante."""
    out = {}
    try:
        for a in AvisoManut.query.filter_by(status='PROGRAMADA').order_by(AvisoManut.data.asc()).all():
            if a.maquina:
                out.setdefault(a.maquina, []).append(dict(
                    tipo=a.tipo, rotulo='Manutenção' if a.tipo == 'MANUTENCAO' else 'Amostra', icone='🔧' if a.tipo == 'MANUTENCAO' else '🧪',
                    data=a.data.strftime('%d/%m') if a.data else '', data_completa=a.data.strftime('%d/%m/%Y') if a.data else '',
<<<<<<< HEAD
                    termino=a.data_termino.strftime('%d/%m/%Y') if a.data_termino else '', mecanico=a.mecanico or '', horas=a.horas_estimadas, subtipo=a.subtipo or '', impacto=_impacto(a)))
=======
                    termino=a.data_termino.strftime('%d/%m/%Y') if a.data_termino else '', mecanico=a.mecanico or '', horas=a.horas_estimadas))
>>>>>>> origin/main
    except Exception:  # noqa: BLE001
        pass
    return out


def _json(a: AvisoManut) -> dict:
    br = lambda d: d.replace(tzinfo=timezone.utc).astimezone(BRT) if d else None
    ini, fim = br(a.inicio), br(a.fim)
    dur = None
    ref_fim = fim or (datetime.now(BRT) if a.status == 'EM_ANDAMENTO' else None)
    if ini and ref_fim and ref_fim >= ini:
        mins = int((ref_fim - ini).total_seconds() // 60)
        dur = f'{mins // 60}h{mins % 60:02d}'
    return dict(id=a.id, tipo=a.tipo, maquina=a.maquina, mecanico=a.mecanico or '', data=a.data.strftime('%d/%m/%Y') if a.data else '', data_iso=a.data.isoformat() if a.data else '', subtipo=a.subtipo or '', termino=a.data_termino.strftime('%d/%m/%Y') if a.data_termino else '', termino_iso=a.data_termino.isoformat() if a.data_termino else '', horas=a.horas_estimadas,
                inicio=ini.strftime('%d/%m/%Y %H:%M') if ini else '', inicio_iso=ini.strftime('%Y-%m-%dT%H:%M') if ini else '', fim=fim.strftime('%d/%m/%Y %H:%M') if fim else '', fim_iso=fim.strftime('%Y-%m-%dT%H:%M') if fim else '',
                status=a.status, duracao=dur, obs=a.obs or '', origem=a.origem, anomalia=bool(ini and fim and fim < ini))


@manut_bp.route('/dados')
@login_required
def dados():
    _autorizar()
    tipo = (request.args.get('tipo') or 'MANUTENCAO').upper()
    historico = request.args.get('historico') == '1'
    q = AvisoManut.query.filter_by(tipo=tipo)
    q = q.filter(AvisoManut.status == 'FINALIZADA') if historico else q.filter(AvisoManut.status != 'FINALIZADA')
    ordem = AvisoManut.fim.desc() if historico else AvisoManut.data.asc()
    itens = q.order_by(ordem, AvisoManut.id.desc()).limit(1500 if historico else 300).all()
    return jsonify(itens=[_json(a) for a in itens], pode_editar=_edita(), total_hist=AvisoManut.query.filter_by(tipo=tipo, status='FINALIZADA').count(), em_manutencao=sorted(em_manutencao()))


def _aplicar(a: AvisoManut, d: dict):
    if 'maquina' in d:
        m = re.sub(r'\D', '', str(d.get('maquina') or ''))
        if not m:
            raise ValueError('Informe a máquina.')
        a.maquina = int(m)
    for campo in ('mecanico', 'obs'):
        if campo in d:
            setattr(a, campo, (str(d.get(campo) or '').strip() or None))
    if 'horas' in d:
        a.horas_estimadas = _num(d.get('horas'))
    if 'data' in d and d.get('data'):
        a.data = date.fromisoformat(str(d['data'])[:10])
    if 'subtipo' in d:
        v = str(d.get('subtipo') or '').strip().upper()
        a.subtipo = v if v in ('PREVENTIVA', 'CORRETIVA') else None
    if 'termino' in d:
        a.data_termino = date.fromisoformat(str(d['termino'])[:10]) if d.get('termino') else None
    for campo, k in (('inicio', 'inicio'), ('fim', 'fim')):
        if k in d:
            v = str(d.get(k) or '').strip()
            setattr(a, campo, datetime.fromisoformat(v).replace(tzinfo=BRT).astimezone(timezone.utc).replace(tzinfo=None) if v else None)
    if a.fim and a.status != 'FINALIZADA':
        a.status = 'FINALIZADA'
    elif a.inicio and not a.fim and a.status == 'PROGRAMADA':
        a.status = 'EM_ANDAMENTO'


@manut_bp.route('/avisos', methods=['POST'])
@login_required
def criar():
    _autorizar()
    if not _edita():
        return jsonify(erro='Sem permissão para criar avisos.'), 403
    d = request.get_json(silent=True) or {}
    tipo = str(d.get('tipo') or 'MANUTENCAO').upper()
    if tipo not in TIPOS:
        return jsonify(erro='Tipo inválido.'), 400
    a = AvisoManut(tipo=tipo, status='PROGRAMADA', origem='gestao', criado_por=getattr(current_user, 'name', None) or 'Usuário')
    try:
        _aplicar(a, d)
    except ValueError as e:
        return jsonify(erro=str(e)), 400
    if not a.data:
        a.data = date.today()
    if a.tipo == 'MANUTENCAO' and not a.subtipo and 'subtipo' not in d:
        a.subtipo = 'PREVENTIVA'
    db.session.add(a)
    db.session.commit()
    _recalcular(a.maquina)
    return jsonify(ok=True, id=a.id)


@manut_bp.route('/avisos/<int:aid>', methods=['POST'])
@login_required
def editar(aid):
    _autorizar()
    if not _edita():
        return jsonify(erro='Sem permissão para editar.'), 403
    a = db.session.get(AvisoManut, aid) or abort(404)
    try:
        _aplicar(a, request.get_json(silent=True) or {})
    except ValueError as e:
        return jsonify(erro=str(e)), 400
    db.session.commit()
    _recalcular(a.maquina)
    return jsonify(ok=True)


@manut_bp.route('/avisos/<int:aid>/excluir', methods=['POST'])
@login_required
def excluir(aid):
    _autorizar()
    if not _edita():
        return jsonify(erro='Sem permissão para excluir.'), 403
    a = db.session.get(AvisoManut, aid) or abort(404)
    maq = a.maquina
    db.session.delete(a)
    db.session.commit()
    _recalcular(maq)
    return jsonify(ok=True)


@manut_bp.route('/importar-hub', methods=['POST', 'GET'])
@login_required
def importar_rota():
    """So administrador: importa o historico de manutencoes/amostras que o PCP Hub tinha (de hub_arquivo; antes roda a migracao do Hub se a tabela estiver vazia)."""
    if not current_user.is_admin:
        abort(403)
    out = {}
    if HubArquivo.query.filter(HubArquivo.tabela.in_(['maintenance_notices', 'sample_notices'])).count() == 0:
        try:
            from app import hubhistorico
            out['migracao_hub'] = {k: v for k, v in hubhistorico.importar().items() if k in ('maintenance_notices', 'sample_notices')}
        except Exception as e:  # noqa: BLE001
            out['migracao_hub'] = f'falhou: {str(e)[:200]}'
    out.update(importar_do_hub())
    return jsonify(out)


@manut_bp.route('/diagnostico')
@login_required
def diagnostico():
    """So administrador: colunas das tabelas do Hub (para conferir o mapeamento) e uma rodada da leitura do Acompanhamento."""
    if not current_user.is_admin:
        abort(403)
    amostra = {}
    for t in ('maintenance_notices', 'sample_notices'):
        h = HubArquivo.query.filter_by(tabela=t).first()
        amostra[t] = dict(linhas=HubArquivo.query.filter_by(tabela=t).count(), exemplo=json.loads(h.dados) if h else None, mapeado=_mapear(t, json.loads(h.dados)) if h else None)
    try:
        sinc = sincronizar()
    except Exception as e:  # noqa: BLE001
        sinc = dict(erro=str(e)[:200])
    try:
        _i, bruto, erro = _abertas_setups()
        setups = dict(erro=erro, lidos=len(bruto), abertos_manut_amostra=_i, exemplo=bruto[:3])
    except Exception as e:  # noqa: BLE001
        setups = dict(erro=str(e)[:200])
    return jsonify(hub=amostra, sincronizacao=sinc, setups=setups, avisos=AvisoManut.query.count())
