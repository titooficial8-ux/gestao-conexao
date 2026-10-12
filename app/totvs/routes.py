"""BI COMERCIAL: dados do faturamento TOTVS (dashboard, geracao sob demanda e downloads)."""
from datetime import date, timedelta
from functools import lru_cache

import threading

from flask import Blueprint, abort, current_app, jsonify, request, send_from_directory
from flask_login import current_user, login_required

from app.comercial_me.routes import idioma_atual
from app.models import Tab

from . import base, movimentos, painel, pedidos, servico, slide

bi_bp = Blueprint('bi_comercial', __name__, url_prefix='/bi-comercial')
SLUG = 'dashboard-comercial'


def _autorizar():
    tab = Tab.query.filter_by(slug=SLUG).first_or_404()
    if not tab.visible_to(current_user):
        abort(403)


def _data(txt):
    try:
        return date.fromisoformat(str(txt)[:10])
    except ValueError:
        return None


def _status():
    return dict(
        estado=dict(servico.ESTADO), configurado=servico.configurado(),
        padrao=servico.meta_rapido('padrao'), custom=servico.meta('custom'),
        proxima=servico.proxima_atualizacao(), meses=servico.meses_armazenados(), antigos=servico.meses_antigos(), pendentes=servico.meses_pendentes(),
    )


def _json(obj):
    r = jsonify(obj)
    r.headers['Cache-Control'] = 'no-store'
    return r


@bi_bp.route('/status')
@login_required
def status():
    _autorizar()
    servico.tentar_automatico()        # quem abre a tela ja dispara o que estiver pendente
    return _json(_status())


@bi_bp.route('/dados')
@login_required
def dados():
    _autorizar()
    tag = request.args.get('fonte', 'padrao')
    if tag not in servico.TAGS:
        abort(404)
    filtros = {k: request.args.get(k) for k in ('data_ini', 'data_fim', 'empresa', 'representante', 'segmento', 'cliente')}
    p = painel.montar(tag, idioma_atual(), filtros)
    p['meta'] = servico.meta(tag)
    return _json(p)


@bi_bp.route('/gerar', methods=['POST'])
@login_required
def gerar():
    _autorizar()
    if not servico.configurado():
        return _json(dict(ok=False, erro='Credenciais do TOTVS nao configuradas no arquivo .env (veja .env.example).')), 400
    ini, fim = _data(request.form.get('inicio')), _data(request.form.get('fim'))
    if request.form.get('modo') in ('recente', 'historico'):
        ini = fim = date.today()
    if not ini or not fim or fim < ini:
        return _json(dict(ok=False, erro='Datas invalidas.')), 400
    if fim > date.today():
        fim = date.today()
    modo = request.form.get('modo', 'custom')
    if modo == 'recente':      # "Atualizar agora": mes anterior + mes atual, direto no armazem
        ini = (date.today().replace(day=1) - timedelta(days=1)).replace(day=1)
        iniciou = servico.iniciar(ini, date.today(), 'base', 'botao')
    elif modo == 'historico':  # carga inicial: de janeiro do ano passado ate hoje (pula meses ja carregados)
        iniciou = servico.iniciar(servico.inicio_historico(), date.today(), 'base', 'historico')
    else:
        iniciou = servico.iniciar(ini, fim, 'custom', 'botao')
    return _json(dict(ok=iniciou, erro=None if iniciou else 'Ja existe uma geracao em andamento.'))


@bi_bp.route('/download/<tag>/<tipo>')
@login_required
def download(tag, tipo):
    _autorizar()
    m = servico.meta(tag) if tag in servico.TAGS else None
    nome = (m or {}).get('arquivos', {}).get(tipo)
    if not nome:
        abort(404)
    return send_from_directory(servico.DIR, nome, as_attachment=True)


@bi_bp.route('/cancelar', methods=['POST'])
@login_required
def cancelar():
    _autorizar()
    return _json(dict(ok=servico.cancelar()))


_der_cache = {}


@lru_cache(maxsize=4)
def _derivados(chave, anos):
    """Linhas derivadas dos meses dos anos pedidos; so recalcula os blocos que mudaram."""
    out, vivos = [], set()
    for b in servico._blocos():
        if int(b.stem[4:8]) not in anos:
            continue
        k = (b.name, b.stat().st_mtime_ns)
        vivos.add(k)
        if k not in _der_cache:
            d = servico._bloco_dados(b)
            _der_cache[k] = [base.derivar(dict(zip(d['colunas'], r))) for r in d['linhas']]
        out.extend(_der_cache[k])
    for k in [k for k in _der_cache if k not in vivos and int(k[0][4:8]) in anos]:
        del _der_cache[k]
    return out


@bi_bp.route('/slide')
@login_required
def slide_mensal():
    _autorizar()
    idx = servico._indice()
    if not idx:
        return _json(dict(vazio=True))
    anos = sorted({int(ym[:4]) for ym in idx})
    ano = request.args.get('ano', type=int) or anos[-1]
    meses = sorted(int(ym[5:]) for ym in idx if ym.startswith(f'{ano}-'))
    if not meses:
        ano = anos[-1]
        meses = sorted(int(ym[5:]) for ym in idx if ym.startswith(f'{ano}-'))
    mes = request.args.get('mes', type=int) or meses[-1]
    regs = _derivados(servico._chave_blocos(), (ano, ano - 1))
    out = slide.montar(regs, mes, ano)
    out.update(vazio=False, anos=anos, meses=meses, gerado_em=max(r['gerado_em'] for r in idx.values()), plano_origem=base._ler('origem'))
    return _json(out)


# ---------------------------------------------------------------------------
# Relatorio Pedidos (aba "relatorio-pedidos"): atualizado pela API do TOTVS a cada 2 minutos
# ---------------------------------------------------------------------------
pedidos_bp = Blueprint('pedidos_totvs', __name__, url_prefix='/pedidos-totvs')


def _autorizar_pedidos():
    tab = Tab.query.filter_by(slug='relatorio-pedidos').first_or_404()
    if not tab.visible_to(current_user):
        abort(403)


@pedidos_bp.route('/status')
@login_required
def pedidos_status():
    _autorizar_pedidos()
    return _json(dict(estado=dict(pedidos.ESTADO), configurado=pedidos.configurado(), intervalo_min=pedidos.INTERVALO_MIN))


@pedidos_bp.route('/atualizar', methods=['POST'])
@login_required
def pedidos_atualizar():
    _autorizar_pedidos()
    if not pedidos.configurado():
        return _json(dict(ok=False, erro='Credenciais do TOTVS nao configuradas no arquivo .env (veja .env.example).')), 400
    if pedidos.ESTADO['rodando']:
        return _json(dict(ok=False, erro='Ja existe uma atualizacao em andamento.'))
    app = current_app._get_current_object()
    threading.Thread(target=pedidos.atualizar, args=(app,), daemon=True, name='totvs-pedidos-manual').start()
    return _json(dict(ok=True))


# ---------------------------------------------------------------------------
# Programacao: ao clicar na OP, pecas pesadas no local 23 (MALHARIA PESAGEM) direto do TOTVS
# ---------------------------------------------------------------------------
mov_bp = Blueprint('movimentos_op', __name__, url_prefix='/movimentos-op')


def _quando(p):
    """Data e hora da pesagem (horario do Brasil): a baixa de fio do TOTVS quando confiavel; senao a hora em que o Gestao viu a peca."""
    if p.hora_real and p.hora_origem == 'trx':
        return p.hora_real
    return p.visto_em


@mov_bp.route('/estado')
@login_required
def movimentos_estado():
    """Diagnostico da busca automatica de movimentos (ultima rodada, erro, OPs achadas fora da Programacao)."""
    from app.models import PesagemMalharia, Sequencia
    from app.totvs import movimentos
    prog = {str(x.op) for x in Sequencia.query.all()}
    ops_db = {p.op for p in PesagemMalharia.query.all()}
    return _json(dict(estado=dict(movimentos.ESTADO), pecas_no_banco=PesagemMalharia.query.count(), ops_no_banco=len(ops_db),
                      ops_fora_da_programacao=len(ops_db - prog)))


@mov_bp.route('/diagnostico')
@login_required
def movimentos_diagnostico():
    """Conta, direto na API, os movimentos que saem do local 23 num dia (?dia=2026-10-09), por destino/tipo/estorno,
    para comparar com o Relatorio 32. Mostra tambem quantas OPs distintas e quantas fora da Programacao."""
    from datetime import date as _d
    from app.models import Sequencia
    from app.totvs import movimentos
    dia = _d.fromisoformat(request.args.get('dia') or _d.today().isoformat())
    prog = {str(x.op) for x in Sequencia.query.all()}
    saida = {}
    with movimentos._trava:
        cli = movimentos._cliente()
        for nome, kw in (('normais', dict(fin=False, rev=2)), ('com_finalizacao', dict(fin=True, rev=2)), ('com_estornados', dict(fin=True, rev=3))):
            corpo_base = dict(branchCodeList=[movimentos.EMPRESA], cycleCodeList=[13], movementStartDate=dia.isoformat(), movementEndDate=dia.isoformat(),
                              listNormalMovement=True, listFinalizationMovement=kw['fin'], reversalType=kw['rev'], pageSize=500)
            itens, pag = [], 1
            while True:
                r = cli.post(movimentos.MOV_URL, dict(corpo_base, page=pag))
                if r.status_code != 200:
                    saida[nome] = f'HTTP {r.status_code} {r.text[:200]}'
                    break
                j = r.json()
                itens += j.get('items') or []
                if not j.get('hasNext'):
                    break
                pag += 1
            else:
                continue
            if nome in saida:
                continue
            de23 = [m for m in itens if m.get('originLocationCode') == 23]
            cont = {}
            for m in de23:
                k = f"23->{m.get('destinationLocationCode')} | {m.get('movementType')} | estornado={m.get('isReversed')}"
                cont[k] = cont.get(k, 0) + 1
            p24 = [m for m in de23 if m.get('destinationLocationCode') == 24]
            saida[nome] = dict(total_movimentos_do_dia=len(itens), saindo_do_23=cont, ops_23_para_24=len({m.get('productionOrder') for m in p24}),
                               ops_23_para_24_fora_da_programacao=len({str(m.get('productionOrder')) for m in p24} - prog),
                               metros_23_para_24=round(sum(m.get('totalQuantity') or 0 for m in p24), 1))
    return _json(saida)


@mov_bp.route('/<op>')
@login_required
def movimentos_op(op):
    from app.models import PesagemMalharia, PesagemResumo, Sequencia
    tab = Tab.query.filter_by(slug='programacao').first_or_404()
    if not tab.visible_to(current_user):
        abort(403)
    if not op.isdigit():
        abort(404)
    seq = Sequencia.query.filter_by(op=op).first()
    resumo = PesagemResumo.query.filter_by(op=op).first()
    if not resumo:                                   # ainda nao entrou na atualizacao automatica: consulta na hora
        if not servico.configurado():
            return _json(dict(ok=False, erro='Credenciais do TOTVS nao configuradas no arquivo .env (veja .env.example).')), 400
        try:
            dados = movimentos.pecas(op, 13, movimentos._metro_padrao(seq) if seq else None)
        except Exception as e:  # noqa: BLE001
            return _json(dict(ok=False, erro=str(e)[:300])), 502
        if seq and seq.machine:
            dados['maquina'] = seq.machine.number
        dados.update(ok=True, cliente=seq.cliente if seq else None, artigo=seq.artigo if seq else None, automatico=False)
        return _json(dados)
    pecas = PesagemMalharia.query.filter_by(op=op).order_by(PesagemMalharia.item).all()
    return _json(dict(
        ok=True, op=op, ciclo=resumo.ciclo, encontrada=resumo.encontrada, local=movimentos.NOME_LOCAL, automatico=True,
        maquina=seq.machine.number if seq and seq.machine else resumo.maquina, metro_padrao=resumo.metro_padrao,
        cliente=seq.cliente if seq else None, artigo=seq.artigo if seq else None,
        pecas=[dict(item=p.item, data=_quando(p).date().isoformat() if _quando(p) else (p.data.isoformat() if p.data else ''), sequencia=p.sequencia, acerto=p.acerto, metros=p.metros,
                    hora=_quando(p).strftime('%H:%M') if _quando(p) else None, quando=_quando(p).isoformat() if _quando(p) else None) for p in pecas],
        resumo=dict(pecas=resumo.pecas, metros=resumo.metros, no_local=resumo.no_local, saiu=resumo.saiu, quantidade_op=resumo.quantidade_op,
                    atualizado_em=resumo.atualizado_em.isoformat(timespec='seconds') + 'Z'),
    ))
