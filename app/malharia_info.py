"""Botao INFO da maquina e alerta 'Peca apresentando defeito' da Programacao.

Fontes (todas lidas, nada e gravado fora do Gestao):
- Etapas de liberacao do SKU (E1 mecanico, E2 revisao cru, E3 acabamento, E4 liberacao): lista SharePoint 2026_liberacao_sku_BR_GT.
- Peca com defeito / ocorrencias de qualidade: lista SharePoint ncform (a mesma do Reprova Cru).
- Acompanhamento: sequencia da maquina (TOTVS), pesagens do Relatorio 32 e historico de producao do Gestao.
No .env: as mesmas SHAREPOINT_TENANT_ID/CLIENT_ID/CLIENT_SECRET dos indicadores; opcionais LIBERACAO_SKU_LISTA, LIDER_MALHARIA_EMAILS.
"""
import os
import re
import threading
import time
from datetime import datetime, timedelta

from flask import Blueprint, abort, jsonify, request
from flask_login import current_user, login_required

from app import db
from app.models import Machine, NcEstado, PesagemMalharia, Sequencia, Tab

info_bp = Blueprint('info_maquina', __name__, url_prefix='/info')

LISTA_LIBERACAO = os.getenv('LIBERACAO_SKU_LISTA', 'dc941722-1fd0-453a-a9ce-3592cf6e8443')
ALERTA_HORAS = int(os.getenv('ALERTA_DEFEITO_HORAS', '12'))
_cache, _trava = {}, threading.Lock()
ETAPAS = (('E1', 'Mecânico (liberação na máquina)'), ('E2', 'Revisão do cru'), ('E3', 'Acabamento'), ('E4', 'Liberação final'))


def _cacheado(chave, ttl, fn):
    """Mantem o ultimo valor bom: se o SharePoint falhar, devolve o antigo (ou lista vazia) em vez de quebrar a tela."""
    agora = time.time()
    with _trava:
        v = _cache.get(chave)
        if v and agora - v[0] < ttl:
            return v[1]
    try:
        novo = fn()
    except Exception as e:  # noqa: BLE001
        with _trava:
            _cache[chave] = (agora - ttl + 15, v[1] if v else [], str(e)[:200])
        return v[1] if v else []
    with _trava:
        _cache[chave] = (agora, novo, None)
    return novo


def _erro(chave):
    v = _cache.get(chave)
    return v[2] if v and len(v) > 2 else None


def _maq2(v):
    v = re.sub(r'\D', '', str(v or ''))
    return v.zfill(2) if v else ''


def _utc_local(iso):
    try:
        d = datetime.fromisoformat(str(iso).replace('Z', '+00:00'))
        return (d.replace(tzinfo=None) - timedelta(hours=3)) if d.tzinfo else d
    except ValueError:
        return None


def _liberacoes() -> list:
    from app.indicadores import sharepoint as sp

    def ler():
        saida, url, params = [], f'{sp.GRAPH}/sites/{sp._site()}/lists/{LISTA_LIBERACAO}/items', {'$expand': 'fields', '$top': 999}
        while url:
            j = sp._get(url, params)
            for it in j.get('value') or []:
                saida.append(dict(it.get('fields') or {}, _criado=it.get('createdDateTime'), _alterado=it.get('lastModifiedDateTime')))
            url, params = j.get('@odata.nextLink'), None
        return saida
    return _cacheado('liberacoes', 60, ler)


def _ncs() -> list:
    from app.indicadores import sharepoint as sp
    from app.indicadores.servico import DEFEITOS
    desde = (datetime.utcnow() - timedelta(days=60)).strftime('%Y-%m-%dT00:00:00Z')

    def ler():
        saida = []
        for x in sp.itens_recentes(desde):
            cods = sorted('R' + k[-2:] for k, v in x.items() if re.fullmatch(r'_x0052_\d\d', k) and v)
            cont = re.findall(r'R\d\d', str(x.get('contagemR') or ''))
            cods = cods or cont
            saida.append(dict(
                id=str(x.get('id') or x.get('_id')), maquina=_maq2(x.get('Maquina')), artigo=str(x.get('field_7') or '').strip(), op=str(x.get('Ordemdeproducao') or '').strip(),
                peca=str(x.get('field_9') or ''), emitente=x.get('field_2') or '', origem=x.get('field_4') or '', setor=x.get('field_13') or '', turno=x.get('TURNO') or '',
                metros=float(x.get('Metros2') or 0), quilos=float(x.get('Quilos2') or 0), cliente=x.get('field_8') or '', obs=x.get('field_49') or '',
                defeitos=[f'{c} {DEFEITOS.get(c, "")}'.strip() for c in cods], criado=_utc_local(x.get('_criado') or x.get('Created'))))
        try:                                                  # artigo digitado errado pelo revisor: vale o artigo da OP
            real = info_das_ops([(n['op'], n['artigo'], n['cliente']) for n in saida if n['op']])
            for n in saida:
                v = real.get(n['op'])
                if v and v['artigo'] and v['artigo'] != n['artigo']:
                    n['artigo_digitado'], n['artigo'] = n['artigo'], v['artigo']
        except Exception:  # noqa: BLE001
            pass
        return saida
    return _cacheado('ncs', 20, ler)


def _hub_cientes() -> set:
    """NCs que o pessoal ja deu 'ciente' no PCP Hub (enquanto o Hub ainda e usado): contam como resolvidas aqui tambem. Falha = conjunto vazio."""
    def ler():
        from app import pcphub
        if not pcphub.configurado():
            return set()
        import requests
        token = pcphub._login()
        r = requests.get(f'{pcphub.URL}/rest/v1/nc_alertas', params=dict(select='nc_id', ciente_em='not.is.null', order='criado_em.desc', limit='1500'),
                         headers=dict(apikey=pcphub.CHAVE, Authorization=f'Bearer {token}'), timeout=30)
        r.raise_for_status()
        return {str(x['nc_id']) for x in r.json()}
    return _cacheado('hub_cientes', 30, ler) or set()


def resolvidas() -> set:
    return {e.nc_id for e in NcEstado.query.filter_by(estado='LIBERADO').all()} | set(_hub_cientes())


_INFO_OP = {}                                        # op -> (quando, cliente, artigo): cliente e artigo de uma OP nao mudam; guarda por 12 h


def info_das_ops(pares) -> dict:
    """{op: {'cliente', 'artigo'}} REAIS. O cliente e o artigo digitados na NC pelo revisor podem estar errados (ex.: 35400018 em vez de 3540018): aqui vem da OP -
    Programacao/Pre-Programacao do Gestao, pedido ligado a OP e, se a OP ja saiu da programacao, direto do TOTVS (cliente e produto da OP).
    OP avulsa da Conexao: o cliente e o do pedido em aberto do artigo. `pares` = [(op, artigo_digitado, cliente_digitado)]."""
    from app.main.routes import _cliente_pelo_artigo, normalize_client_name
    from app.models import FilaPreProgramacao, PedidoRelatorio
    agora = time.time()
    out, faltam, nomes, arts = {}, [], {}, {}
    for op, artigo, informado in pares:
        op = str(op or '')
        if not op or op in out:
            continue
        v = _INFO_OP.get(op)
        if v and agora - v[0] < 43200:
            out[op] = dict(cliente=v[1], artigo=v[2])
            continue
        cli_op = art_op = None
        sq = Sequencia.query.filter_by(op=op).first()
        if sq:
            cli_op, art_op = sq.cliente, (sq.artigo or '').split(' ')[0] or None
        if not cli_op or not art_op:
            p = PedidoRelatorio.query.filter_by(nr_op=op).first()
            if p:
                cli_op, art_op = cli_op or p.nome_cliente, art_op or p.cod_produto
        if not cli_op or not art_op:
            f = FilaPreProgramacao.query.filter_by(op=op).first()
            if f:
                cli_op, art_op = cli_op or f.cliente, art_op or (f.artigo or '').split(' ')[0]
        nomes[op], arts[op] = cli_op, art_op
        if not (cli_op and art_op):
            faltam.append(op)
        out[op] = None
    if faltam:
        try:
            from app.totvs import api, artigos
            cli = api.TotvsModaClient()
            cli.autenticar()
            achadas = []
            for lote in [faltam[i:i + 50] for i in range(0, len(faltam), 50)]:
                achadas += artigos._ops(cli, [int(x) for x in lote if x.isdigit()])
            cods = artigos.codigos_de_artigo(cli, [(o.get('items') or [{}])[0].get('productCode') for o in achadas])
            for o in achadas:
                op = str(o.get('orderCode'))
                it = (o.get('items') or [{}])[0]
                nomes[op] = nomes.get(op) or o.get('customerName')
                arts[op] = arts.get(op) or cods.get(str(it.get('productCode')))
        except Exception:  # noqa: BLE001
            pass
    dig = {str(op): (a, inf) for op, a, inf in pares if op}
    for op, (a, inf) in dig.items():
        if out.get(op):
            continue
        nome, art = nomes.get(op), arts.get(op)
        if nome and 'CONEXAO MALHAS' in nome.upper():             # OP avulsa: o cliente de verdade vem do artigo
            nome = _cliente_pelo_artigo(art or a) or nome
        nome = normalize_client_name(nome.strip()) if nome else None
        if nome and art:
            _INFO_OP[op] = (agora, nome, art)
        out[op] = dict(cliente=nome or inf or '', artigo=art or a or '')
    return out


def clientes_das_ops(pares) -> dict:
    return {op: v['cliente'] for op, v in info_das_ops(pares).items() if v}


def pode_liberar(u=None) -> bool:
    """Lider da malharia (ou administrador): usuario do setor Malharia com cargo de lider, ou e-mail em LIDER_MALHARIA_EMAILS."""
    u = u or current_user
    if not u.is_authenticated:
        return False
    if u.is_admin or (u.email or '').lower() in {e.strip().lower() for e in os.getenv('LIDER_MALHARIA_EMAILS', '').split(',') if e.strip()}:
        return True
    setor = (u.setor.name if u.setor else '').lower()
    cargo = (u.role.name if u.role else '').lower()
    return 'malharia' in setor and ('lider' in cargo or 'líder' in cargo)


def _tab_ok():
    tab = Tab.query.filter_by(slug='programacao').first_or_404()
    if not tab.visible_to(current_user):
        abort(403)


# ------------------------------------------------------------------ alerta: peca apresentando defeito
@info_bp.route('/alertas')
@login_required
def alertas():
    _tab_ok()
    from app import acompanhamento
    if acompanhamento.configurado():            # o lider marca ANALISANDO/LIBERADO no sistema do Acompanhamento; aqui o PCP so acompanha
        pend = acompanhamento.liberacoes('pendente')
        if True:
            hoje = (datetime.now() - timedelta(hours=6, minutes=20)).strftime('%Y-%m-%d')
            lib = acompanhamento.liberacoes('liberado', hoje)

            tot = _total_pecas([x['op'] for x in pend['itens'] + lib['itens']])

            def j(x):
                t = tot.get(x['op'])
                return dict(invalida=peca_invalida(x['peca'], t), total_pecas=t, id=x['id'], maquina=x['maquina'], op=x['op'] or '–', peca=x['peca'], artigo=x['artigo'], metros=x['metros_reprovados'], metros_cru=x['metros_reais'], metros_passou=x['metros_passou'], parcial=x['status'] == 'PARCIAL',
                            defeitos=x['defeitos'], origem='REVISÃO CRU', emitente=x['revisor'], criado=x['pedido_em'] or x['quando'], turno=x['turno'], obs='',
                            estado=('ANALISANDO' if x['lib_status'] == 'ANALISANDO' else ('LIBERADO' if x['lib_status'] in ('LIBERADO', 'APROVADA') else 'AGUARDANDO')),
                            por=x['analisando_por'] or x['liberado_por'], em=x['analisando_em'] or x['liberado_em'])
            itens = [j(x) for x in pend['itens']]
            _avisar_invalidas(itens)
            return jsonify(itens=itens, liberados=[j(x) for x in lib['itens']], pode=False, fonte='acompanhamento', erro=pend['erro'])
    limite = datetime.utcnow() - timedelta(hours=ALERTA_HORAS + 3)
    estados = {e.nc_id: e for e in NcEstado.query.all()}
    resolv = resolvidas()
    itens = []
    for n in _ncs():
        if not n['defeitos'] or (n['setor'] and 'produ' not in n['setor'].lower()) or not n['criado'] or n['criado'] + timedelta(hours=3) < limite:
            continue
        e = estados.get(n['id'])
        if n['id'] in resolv:
            continue
        itens.append(dict(n, origem=_origem(n['origem']), criado=n['criado'].strftime('%d/%m %H:%M'), estado=e.estado if e else 'AGUARDANDO', por=e.por_nome if e else None, em=(e.em - timedelta(hours=3)).strftime('%d/%m, %H:%M') if e and e.em else None,
                          ordem=n['criado'].isoformat()))
    itens.sort(key=lambda x: x['ordem'], reverse=True)
    return jsonify(itens=itens, pode=pode_liberar(), erro=_erro('ncs'))


@info_bp.route('/alertas/<nc_id>/estado', methods=['POST'])
@login_required
def alerta_estado(nc_id):
    _tab_ok()
    if not pode_liberar():
        return jsonify(erro='Somente o líder da malharia pode marcar ANALISANDO ou LIBERADO.'), 403
    est = str((request.get_json(silent=True) or {}).get('estado') or '').upper()
    if est not in ('ANALISANDO', 'LIBERADO'):
        return jsonify(erro='Estado inválido.'), 400
    e = NcEstado.query.filter_by(nc_id=str(nc_id)).first() or NcEstado(nc_id=str(nc_id))
    e.estado, e.por_nome, e.em = est, current_user.name, datetime.utcnow()
    db.session.add(e)
    db.session.commit()
    return jsonify(ok=True)


# ------------------------------------------------------------------ INFO da maquina / OP
def _turno_idx(dt):
    m = dt.hour * 60 + dt.minute
    return 0 if 380 <= m < 860 else (1 if 860 <= m < 1340 else 2)


def _estimativa(seq, maq):
    """Media de pecas que o artigo entrega nesta maquina (DS.METROS x raport/volta x RPM instalado x eficiencia)."""
    try:
        from app import previsao
        return previsao.estimativa_artigo(maq.number, seq)
    except Exception:  # noqa: BLE001
        return None


def _acompanhamento(seq, maq):
    hoje = datetime.now()
    ini = hoje.replace(hour=6, minute=20, second=0, microsecond=0)
    if hoje < ini:
        ini -= timedelta(days=1)
    pes, ontem = [0, 0, 0], [0, 0, 0]
    ini_ont = ini - timedelta(days=1)
    for r in PesagemMalharia.query.filter_by(op=str(seq.op)).all():          # pecas pesadas (local 23) desta OP, com a hora real da baixa de fio
        h = r.hora_real or r.visto_em
        if not h or h < ini_ont or h >= ini + timedelta(days=1):
            continue
        (pes if h >= ini else ontem)[_turno_idx(h)] += 1
    meta_dia = seq.pecas_dia or 0
    return dict(op=seq.op, artigo=seq.artigo, cliente=seq.cliente, status=seq.status, situacao=seq.situacao, produzidas=seq.pecas_produzidas or 0, total=seq.total_pcs or 0,
                meta_dia=round(meta_dia, 1) if meta_dia else None, meta_turno=round(meta_dia / 3, 1) if meta_dia else None, pesadas_turno=pes, pesadas_ontem=ontem, inicio_ontem=ini_ont.strftime('%d/%m %H:%M'), inicio_dia=ini.strftime('%d/%m %H:%M'),
                termino=seq.prev_termino_em.strftime('%d/%m %H:%M') if seq.prev_termino_em else (seq.termino_malharia.strftime('%d/%m/%Y') if seq.termino_malharia else None),
                rpm=seq.prev_rpm, estrutura=seq.estrutura, previsao_artigo=_estimativa(seq, maq))


def _etapas(artigo, op, maquina):
    """Linha mais recente da liberacao do SKU para artigo + maquina (prefere a da mesma OP)."""
    cod = (artigo or '').split(' ')[0]
    cand = [x for x in _liberacoes() if str(x.get('field_2') or '').strip() == cod and _maq2(x.get('field_3')) == maquina]
    if not cand:
        return None, []
    cand.sort(key=lambda x: (str(x.get('field_4')) == str(op), x.get('_alterado') or ''), reverse=True)
    x = cand[0]
    g = lambda k: x.get(k) if x.get(k) not in (None, '') else None
    etapas = [
        dict(cod='E1', titulo=ETAPAS[0][1], feito=True, itens=[('Status gm', g('field_20')), ('Status largura', g('field_21')), ('Status volta', g('field_22')), ('Usuário', g('field_25')), ('Gm cru', g('field_12')), ('Largura crua', g('field_13')),
                                                         ('Tamanho da volta', g('field_14')), ('Observação', g('field_24'))]),
        dict(cod='E2', titulo=ETAPAS[1][1], feito=bool(g('field_37') or g('field_26')), itens=[('Revisão', g('field_37')), ('Responsável', g('field_26')), ('Status gm', g('field_35')), ('Status largura', g('field_36')), ('Gm cru', g('field_29')),
                                                                                           ('Largura crua', g('field_28')), ('Voltas sugeridas', g('field_34')), ('Observação', g('field_38'))]),
        dict(cod='E3', titulo=ETAPAS[2][1], feito=bool(g('field_53') or g('field_40')), itens=[('Acabamento', g('field_53')), ('Responsável', g('field_40')), ('Status gm', g('field_49')), ('Status largura', g('field_50')), ('Visual', g('field_51')),
                                                                                           ('Motivo reprovação', g('field_52')), ('Observação', g('field_54'))]),
        dict(cod='E4', titulo=ETAPAS[3][1], feito=bool(g('field_57')), itens=[('Liberação', g('field_57')), ('Usuário', g('field_59')), ('Observação', g('field_58'))]),
    ]
    atual = str(g('field_7') or 'E1')
    for e in etapas:
        e['itens'] = [dict(k=k, v=v) for k, v in e['itens'] if v is not None]
        e['atual'] = e['cod'] == atual
    resumo = dict(status=g('field_5'), etapa=atual, op=g('field_4'), atualizado=(_utc_local(x.get('_alterado')) or datetime.now()).strftime('%d/%m %H:%M'),
                  regulagem={k: g(c) for k, c in (('Ponto disco', 'field_61'), ('Ponto cilindro', 'field_62'), ('Puxador', 'field_63'), ('Frontura', 'field_64'), ('Altura disco', 'field_65'), ('Fita trama', 'field_66'), ('Tensão fios', 'field_67'),
                                                  ('Largura alargador', 'field_68'), ('Voltas carrinho enchimento', 'field_69'), ('RPM', 'field_70')) if g(c) is not None})
    return resumo, etapas


def _origem(o):
    """Auditoria Interna = a peca reprovou no cru."""
    return 'Auditoria Interna (Reprova Cru)' if 'auditoria interna' in str(o or '').lower() else (o or '')


def _total_pecas(ops) -> dict:
    """{op: ate que numero de peca existe}. Planejado = pecas totais da sequencia (quantidade/metro arredondada pra cima); se a OP ja terminou a pesagem (nada mais no local 23),
    vale o que foi realmente pesado: 474 m / 52 m = 9,1 vira 10 no planejado, mas se a OP fechou com 9 pecas pesadas a peca 10 nao existe."""
    from app.models import PesagemResumo
    ops = {str(o) for o in ops if o}
    if not ops:
        return {}
    plan = {str(q.op): max(q.total_pcs or 0, q.pecas_produzidas or 0) for q in Sequencia.query.filter(Sequencia.op.in_(ops)).all() if (q.total_pcs or 0) > 0}
    for r in PesagemResumo.query.filter(PesagemResumo.op.in_(ops)).all():
        if (r.pecas or 0) > 0 and (r.no_local or 0) <= 0.5:              # terminou: so existem as pecas pesadas
            plan[str(r.op)] = r.pecas
    return plan


def peca_invalida(peca, total) -> bool:
    """A peca apontada nao existe na OP: numero maior que a quantidade de pecas da OP (ou numero invalido)."""
    try:
        n = int(str(peca).strip())
    except (TypeError, ValueError):
        return False
    return bool(total) and (n < 1 or n > total)


def _avisar_invalidas(itens) -> None:
    """Peca inexistente ainda sem liberacao do lider: avisa o lider no painel dele (uma unica vez por registro) para nao dar OK."""
    from app import acompanhamento
    try:
        for x in itens:
            if not x.get('invalida') or x.get('estado') == 'LIBERADO':
                continue
            chave = f"rl:{x['id']}"
            if NcEstado.query.filter_by(nc_id=chave).first():
                continue
            ok = acompanhamento.avisar_lider(f"MQ {x['maquina']} · OP {x['op']} · peça {x['peca']}: essa peça NÃO existe, a OP tem só {x['total_pecas']} peças. Não libere sem conferir com o revisor ({x.get('emitente') or '–'}).",
                                             x['maquina'], f"peca-invalida-{x['id']}", 'Peça inexistente na OP')
            if ok:
                db.session.add(NcEstado(nc_id=chave, estado='AVISADO', por_nome='Gestão (automático)'))
                db.session.commit()
    except Exception:  # noqa: BLE001
        db.session.rollback()


def _rl_ocorrencias(cod, maquina, num):
    """RL.PRODUCAO: pecas que o revisor do cru avaliou com defeito (ou reprovou) neste artigo nesta maquina (API do Acompanhamento), ultimos 30 dias."""
    from app import acompanhamento
    if not acompanhamento.configurado():
        return []
    de = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
    out = []
    rl_itens = acompanhamento.rl_producao(num, de)['itens']
    tot = _total_pecas([x['op'] for x in rl_itens])
    for x in rl_itens:
        if x['artigo'].split(' ')[0] != cod or not x['em'] or not (x['defeitos'] or x['resultado'] == 'REPROVADA' or (x['metros_reprovados'] or 0) > 0):
            continue
        out.append(dict(quando=x['quando'], tipo='RL', resolvida=x['lib_status'] in ('LIBERADO', 'APROVADA'), aberta=x['lib_status'] in ('PENDENTE', 'ANALISANDO'), artigo=x['artigo'], op=x['op'] or '–', cliente='', avulsa=None,
                        peca=x['peca'], defeitos=', '.join(x['defeitos']) or x['resultado'], metros=x['metros_reprovados'] or 0, origem='RL.PRODUÇÃO · revisão cru', emitente=x['revisor'], obs=(f"líder: {x['lib_status'].title()}" if x['lib_status'] else ''),
                        ordem=x['em'].isoformat(), invalida=peca_invalida(x['peca'], tot.get(x['op'])), total_pecas=tot.get(x['op'])))
    return out


def _ocorrencias(artigo, maquina, num):
    """Ultimas ocorrencias SO deste artigo nesta maquina e SO as apontadas na revisao do cru (NC da lista ncform)."""
    from app.models import PedidoRelatorio
    cod = (artigo or '').split(' ')[0]
    resolv = resolvidas()
    recente = datetime.now() - timedelta(hours=72)
    lista = [n for n in _ncs() if n['artigo'] == cod and n['maquina'] == maquina and n['criado']]
    com_pedido = {str(p.nr_op) for p in PedidoRelatorio.query.filter(PedidoRelatorio.nr_op.in_([n['op'] for n in lista if n['op']])).all()} if lista else set()
    lista.sort(key=lambda n: n['criado'], reverse=True)
    real = clientes_das_ops([(n['op'], n['artigo'], n['cliente']) for n in lista[:40]])
    for n in lista:
        n['cliente'] = real.get(str(n['op']), n['cliente']) or n['cliente']
    rl = _rl_ocorrencias(cod, maquina, num)
    nc = [dict(quando=n['criado'].strftime('%d/%m %H:%M'), tipo='NC', resolvida=n['id'] in resolv, aberta=(n['id'] not in resolv and n['criado'] >= recente), artigo=n['artigo'], op=n['op'] or '–',
                 cliente=n['cliente'] or '–', avulsa=bool(n['op']) and n['op'] not in com_pedido, peca=n['peca'], defeitos=', '.join(n['defeitos']), metros=n['metros'], origem=_origem(n['origem']),
                 emitente=n['emitente'], obs=n['obs'], ordem=n['criado'].isoformat()) for n in lista[:40]]
    return sorted(nc + rl, key=lambda o: o['ordem'], reverse=True)[:50]


def _ocorrencias_abertas(m2):
    """Ocorrencias em andamento desta maquina no Acompanhamento de Producao (maquina parada, amostra...), as mesmas do painel Tempo Real Malharia."""
    from app import acompanhamento
    if not acompanhamento.configurado():
        return []
    return [dict(id=x['id'], tipo=x['tipo'], motivo=x['motivo'], inicio=x['inicio'], dia=x['dia'], minutos=x['minutos'], parada='PARAD' in x['tipo'], aguardando_peca=x['aguardando_peca'])
            for x in acompanhamento.abertas()['itens'] if x['maquina'] == m2 and not x['revisor']]


@info_bp.route('/maquina/<int:numero>/alertas')
@login_required
def info_alertas_maquina(numero):
    """Sequencias da maquina com ocorrencia de defeito/reprova (NC da revisao do cru ainda nao resolvida): pisca quando e da mesma OP; fica vermelho fixo quando e do mesmo artigo em outra OP da maquina (72 h). Liberacao nao faz piscar."""
    _tab_ok()
    maq = Machine.query.filter_by(number=numero).first_or_404()
    m2 = f'{numero:02d}'
    recente = datetime.now() - timedelta(hours=72)
    from app import acompanhamento
    api_ok = acompanhamento.configurado()
    pend_api = [x for x in acompanhamento.liberacoes('pendente')['itens'] if x['maquina'] == m2] if api_ok else []
    resolv = resolvidas()
    ncs = [n for n in _ncs() if n['maquina'] == m2 and n['criado'] and n['id'] not in resolv and not api_ok]      # NC ja resolvida (liberada aqui ou 'ciente' no Hub) nao conta mais
    parada = any(o['parada'] for o in _ocorrencias_abertas(m2))
    ids, marca = [], []
    ativas = [sq for sq in maq.sequencias if sq.status not in ('FINALIZADO', 'CANCELADO')]
    produzindo = next((sq for sq in sorted(ativas, key=lambda q: (q.status != 'PRODUZINDO', q.sequence_order or 0))), None)
    for sq in maq.sequencias:
        if sq.status in ('FINALIZADO', 'CANCELADO'):
            continue
        cod = (sq.artigo or '').split(' ')[0]
        if (parada and produzindo is not None and sq.id == produzindo.id) or any(n['artigo'] == cod and n['op'] == str(sq.op) for n in ncs) or any(x['op'] == str(sq.op) for x in pend_api):            # NC/peca com defeito desta mesma OP ainda sem liberacao do lider: i piscando
            ids.append(sq.id)
        elif any(n['artigo'] == cod and n['criado'] >= recente for n in ncs) or any(x['artigo'].split(' ')[0] == cod for x in pend_api):          # mesmo artigo reprovado em outra OP nesta maquina: i vermelho fixo (so identifica)
            marca.append(sq.id)
    return jsonify(ids=ids, marca=marca)


@info_bp.route('/maquina/<int:numero>')
@login_required
def info_maquina(numero):
    """?seq=<id> escolhe uma sequencia; sem ele, a que esta PRODUZINDO (ou a primeira da fila)."""
    _tab_ok()
    maq = Machine.query.filter_by(number=numero).first_or_404()
    seqs = [s for s in maq.sequencias if s.status not in ('FINALIZADO', 'CANCELADO')]
    sid = request.args.get('seq', type=int)
    seq = next((s for s in seqs if s.id == sid), None) or next((s for s in seqs if s.status == 'PRODUZINDO'), None) or (seqs[0] if seqs else None)
    if not seq:
        return jsonify(maquina=numero, vazio=True)
    m2 = f'{numero:02d}'
    resumo, etapas = _etapas(seq.artigo, seq.op, m2)
    if resumo is not None:                                            # o app de liberacao nao traz o raport: vem do cadastro (estrutura da OP + excecao da maquina)
        try:
            from app import previsao
            rap = previsao.raport_da_op(numero, seq)
        except Exception:  # noqa: BLE001
            rap = None
        if rap:
            resumo['regulagem'] = {'Raport': f"{str(rap).replace('.', ',')} cm", **resumo['regulagem']}
    return jsonify(maquina=numero, acompanhamento=_acompanhamento(seq, maq), liberacao=resumo, etapas=etapas, ocorrencias=_ocorrencias(seq.artigo, m2, numero), paradas=_ocorrencias_abertas(m2), erro_sharepoint=_erro('liberacoes') or _erro('ncs'))


# ------------------------------------------------------------------ aumento de quantidade da OP (PCPFP084)
def _ajuste_json(a):
    return dict(id=a.id, op=a.op, maquina=a.maquina, artigo=a.artigo, qt_antes=round(a.qt_antes or 0, 1), qt_depois=round(a.qt_depois or 0, 1), aumento=round((a.qt_depois or 0) - (a.qt_antes or 0), 1),
                pecas_antes=a.pecas_antes, pecas_depois=a.pecas_depois, quando=(a.detectado_em - timedelta(hours=3)).strftime('%d/%m %H:%M') if a.detectado_em else '',
                justificativa=a.justificativa, por=a.justificado_por)


@info_bp.route('/ajustes/<int:numero>')
@login_required
def ajustes_maquina(numero):
    """Sequencias da maquina com aumento de quantidade ainda sem justificativa (o total de pecas pisca)."""
    _tab_ok()
    from app.models import AjusteQuantidade
    seqs = {s.id for s in Machine.query.filter_by(number=numero).first_or_404().sequencias}
    todos = AjusteQuantidade.query.filter(AjusteQuantidade.sequencia_id.in_(seqs), db.or_(AjusteQuantidade.origem.is_(None), AjusteQuantidade.origem == 'totvs')).order_by(AjusteQuantidade.id).all() if seqs else []
    pend = [a for a in todos if a.justificado_em is None]
    ultimo = {a.sequencia_id: a for a in todos}                       # a ultima reposicao de cada sequencia
    com_pend = {a.sequencia_id for a in pend}
    return jsonify(itens=[dict(id=a.id, seq=a.sequencia_id) for a in pend],
                   historico=[dict(id=a.id, seq=sid) for sid, a in ultimo.items() if sid not in com_pend])


@info_bp.route('/ajuste/<int:ajuste_id>')
@login_required
def ajuste_detalhe(ajuste_id):
    _tab_ok()
    from app.models import AjusteQuantidade
    a = db.session.get(AjusteQuantidade, ajuste_id) or abort(404)
    emp, aviso = [], None
    try:
        from app.totvs import api, empenho
        cli = api.TotvsModaClient()
        cli.autenticar()
        log = []
        mats = empenho.buscar_materiais_op(cli, [int(a.op)], log).get(str(a.op)) or {}
        emp = [dict(codigo=c, nome=v[0], qtd=v[1]) for c, v in sorted(mats.items(), key=lambda kv: -kv[1][1])]
        if not emp:
            aviso = 'O TOTVS não devolveu materiais empenhados para esta OP' + (f' ({log[0].get("erro") or log[0].get("http")})' if log else '') + '.'
    except Exception as e:  # noqa: BLE001
        aviso = f'Não foi possível consultar o empenho no TOTVS agora ({str(e)[:120]}).'
    return jsonify(ajuste=_ajuste_json(a), empenho=emp, total_kg=round(sum(x['qtd'] for x in emp), 2), aviso=aviso)


@info_bp.route('/ajuste/<int:ajuste_id>/confirmar', methods=['POST'])
@login_required
def ajuste_confirmar(ajuste_id):
    _tab_ok()
    from app.main.routes import _pode_dias_sem_producao
    from app.models import AjusteQuantidade
    if not _pode_dias_sem_producao():
        return jsonify(erro='Somente o PCP pode justificar o aumento de quantidade.'), 403
    a = db.session.get(AjusteQuantidade, ajuste_id) or abort(404)
    motivo = re.sub(r'\s+', ' ', str((request.get_json(silent=True) or {}).get('motivo') or '')).strip()
    if len(motivo) < 5:
        return jsonify(erro='Justifique o motivo (mínimo 5 caracteres).'), 400
    a.justificativa, a.justificado_por, a.justificado_em = motivo[:500], current_user.name, datetime.utcnow()
    db.session.commit()
    return jsonify(ok=True)


# ------------------------------------------------------------------ ocorrencias do Acompanhamento de Producao (revisor em parada, paradas de maquina...)
@info_bp.route('/paradas')
@login_required
def paradas():
    _tab_ok()
    from app import acompanhamento
    if not acompanhamento.configurado():
        return jsonify(itens=[], erro='Token da API do Acompanhamento não configurado (ACOMP_TOKEN ou SETUPS_TOKEN no .env).')
    return jsonify(**acompanhamento.abertas())


@info_bp.route('/acompanhamento-diagnostico')
@login_required
def acompanhamento_diagnostico():
    """So administrador: lista as rotas da API do Acompanhamento e um exemplo das ocorrencias em andamento."""
    if not current_user.is_admin:
        abort(403)
    from app import acompanhamento
    out = dict(url=acompanhamento._url(), token_configurado=acompanhamento.configurado())
    for nome, caminho, params in (('rotas', '', None), ('ocorrencias_andamento', '/ocorrencias', {'status': 'andamento'})):
        try:
            j = acompanhamento.chamar(caminho, params)
            out[nome] = j if nome == 'rotas' else acompanhamento._lista(j)[:8]
        except Exception as e:  # noqa: BLE001
            out[nome] = f'erro: {e}'
    return jsonify(out)


@info_bp.route('/liberacoes-diagnostico')
@login_required
def liberacoes_diagnostico():
    """So administrador: o que a API do Acompanhamento devolve em /liberacoes (pendentes e liberadas de hoje) e como o Gestao leu."""
    if not current_user.is_admin:
        abort(403)
    from app import acompanhamento
    out = dict(token_configurado=acompanhamento.configurado(), url=acompanhamento._url())
    for nome, params in (('bruto_pendente', {'status': 'pendente'}), ('bruto_todas_hoje', {'status': 'todas', 'de': datetime.now().strftime('%Y-%m-%d')})):
        try:
            out[nome] = acompanhamento._lista(acompanhamento.chamar('/liberacoes', params))[:5]
        except Exception as e:  # noqa: BLE001
            out[nome] = f'erro: {e}'
    p = acompanhamento.liberacoes('pendente')
    out['lido_pelo_gestao'] = dict(erro=p['erro'], itens=[{k: (str(v) if k == 'em' else v) for k, v in x.items()} for x in p['itens'][:5]])
    return jsonify(out)


@info_bp.route('/op-diagnostico')
@login_required
def op_diagnostico():
    """So administrador: tudo sobre uma OP (?op=2959): o que o Gestao guarda (sequencia, aumentos, pecas pesadas) e o que o TOTVS diz agora (quantidade, pecas pesadas, planejado, materiais empenhados)."""
    if not current_user.is_admin:
        abort(403)
    from app.models import AjusteQuantidade, PesagemMalharia, PesagemResumo
    op = re.sub(r'\D', '', request.args.get('op') or '')
    if not op:
        return jsonify(erro='Informe ?op=NUMERO'), 400
    out = dict(op=op)
    out['sequencias'] = [dict(maquina=q.machine.number if q.machine else None, artigo=q.artigo, status=q.status, situacao=q.situacao, total_pcs=q.total_pcs, qt_plan=q.qt_plan, qt_solicitada=q.qt_solicitada,
                              qt_ult_vista=q.qt_ult, pecas_produzidas=q.pecas_produzidas) for q in Sequencia.query.filter_by(op=op).all()]
    out['aumentos'] = [dict(origem=a.origem, quando=str(a.detectado_em), qt_antes=a.qt_antes, qt_depois=a.qt_depois, pecas_antes=a.pecas_antes, pecas_depois=a.pecas_depois, justificativa=a.justificativa, por=a.justificado_por)
                       for a in AjusteQuantidade.query.filter_by(op=op).order_by(AjusteQuantidade.id).all()]
    pes = PesagemMalharia.query.filter_by(op=op).order_by(PesagemMalharia.item).all()
    out['pesagens_gestao'] = dict(qtd=len(pes), pecas=[dict(item=p.item, data=str(p.data), hora=str(p.hora_real or p.visto_em), metros=p.metros, acerto=p.acerto) for p in pes])
    r = PesagemResumo.query.filter_by(op=op).first()
    out['resumo_gestao'] = dict(pecas=r.pecas, quantidade_op=r.quantidade_op, no_local=r.no_local, saiu=r.saiu, metro_ideal=r.metro_ideal) if r else None
    try:
        from app.totvs import movimentos as M, empenho
        v = M.pecas(op)
        out['totvs_pecas_pesadas'] = dict(resumo=v.get('resumo'), numeros=[p.get('item') or p.get('numero') for p in v.get('pecas', [])], metros=[p.get('metros') for p in v.get('pecas', [])])
        cli = M._cliente()
        rr = cli.post(M.OPS_URL, dict(filter=dict(branchCodeList=[M.EMPRESA], cycleCodeList=[13], orderCodeList=[int(op)]), expand='items,locations', pageSize=5, page=1))
        o = (rr.json().get('items') or [{}])[0] if rr.status_code == 200 else {}
        out['totvs_op'] = {k: o.get(k) for k in ('orderCode', 'status', 'quantity', 'pendingQuantity', 'finishedQuantity', 'insertDate', 'machineCode', 'customerName') if k in o}
        out['totvs_planejado_primeiro_movimento'] = M.qt_planejada(op, o) if o else None
        mats = empenho.buscar_materiais_op(cli, [int(op)], []).get(op) or {}
        out['totvs_materiais_empenhados'] = dict(itens=[dict(codigo=c, nome=v[0], qtd=v[1]) for c, v in mats.items()], total=round(sum(v[1] for v in mats.values()), 2))
    except Exception as e:  # noqa: BLE001
        out['totvs_erro'] = f'{type(e).__name__}: {str(e)[:200]}'
    return jsonify(out)


@info_bp.route('/pecas-dia-diagnostico')
@login_required
def pecas_dia_diagnostico():
    """So administrador: de onde vem a contagem de pecas do Controle de Producao no dia de producao (?dia=AAAA-MM-DD, padrao hoje): baixas de fio do TOTVS (ligadas ou nao a uma OP) e pesagens do local 23, por maquina e por turno."""
    if not current_user.is_admin:
        abort(403)
    from collections import Counter
    from app.models import BaixaFio, PesagemMalharia
    from app.relatorio_fat.agenda import agora_brasilia
    try:
        dia = datetime.strptime(request.args.get('dia') or '', '%Y-%m-%d')
    except ValueError:
        dia = (agora_brasilia() - timedelta(hours=6, minutes=20)).replace(hour=0, minute=0, second=0, microsecond=0)
    ini = dia.replace(hour=6, minute=20)
    fim = ini + timedelta(days=1)
    bx = BaixaFio.query.filter(BaixaFio.mudou >= ini, BaixaFio.mudou < fim).all()
    out = dict(dia_producao=dia.strftime('%Y-%m-%d'), janela=f'{ini:%d/%m %H:%M} a {fim - timedelta(minutes=1):%d/%m %H:%M}',
               baixas_de_fio=dict(total=len(bx), ligadas_a_uma_op=sum(1 for b in bx if b.casada), sem_op=sum(1 for b in bx if not b.casada), kg=round(sum(b.kg or 0 for b in bx), 1),
                                  por_turno=dict(Counter(f'{1 if 380 <= b.mudou.hour * 60 + b.mudou.minute < 860 else (2 if 860 <= b.mudou.hour * 60 + b.mudou.minute < 1340 else 3)}º turno' for b in bx)),
                                  por_maquina=dict(sorted(Counter(str(b.maquina) for b in bx if b.maquina is not None).items(), key=lambda kv: int(kv[0]))), sem_maquina=sum(1 for b in bx if b.maquina is None)))
    pes = [p for p in PesagemMalharia.query.all() if (p.hora_real or p.visto_em) and ini <= (p.hora_real or p.visto_em).replace(tzinfo=None) < fim]
    out['pesagens_local_23'] = dict(total=len(pes), ops=len({p.op for p in pes}), por_turno=dict(Counter(f'{1 if 380 <= (p.hora_real or p.visto_em).hour * 60 + (p.hora_real or p.visto_em).minute < 860 else (2 if 860 <= (p.hora_real or p.visto_em).hour * 60 + (p.hora_real or p.visto_em).minute < 1340 else 3)}º turno' for p in pes)))
    # comparacao por OP: pesagens do TOTVS com a DATA do dia de producao (a data do movimento, nao a hora em que o Gestao viu) x baixas de fio ligadas a OP
    peso_op, baixa_op = Counter(p.op for p in PesagemMalharia.query.filter_by(data=dia.date()).all()), Counter(b.op for b in bx if b.op)
    dif = [dict(op=op, pesagens_totvs_hoje=peso_op.get(op, 0), baixas_de_fio_hoje=baixa_op.get(op, 0)) for op in sorted(set(peso_op) | set(baixa_op)) if peso_op.get(op, 0) != baixa_op.get(op, 0)]
    out['ops_com_diferenca'] = dict(qtd=len(dif), pesagens_totvs_hoje_total=sum(peso_op.values()), itens=dif[:40])
    out['baixas_sem_op'] = [dict(codigo=b.codigo, hora=str(b.mudou), kg=b.kg, maquina=b.maquina, fios=b.composicao) for b in bx if not b.op][:10]
    out['leitura'] = 'O Controle de Produção conta as baixas de fio (total acima). Compare com o número real do dia; se o real for maior, as peças que faltam não geraram baixa de fio (ou ainda não foram lidas do TOTVS).'
    return jsonify(out)
