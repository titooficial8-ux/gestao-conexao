"""BI COMERCIAL: dados do faturamento TOTVS (dashboard, geracao sob demanda e downloads)."""
from datetime import date, timedelta
from functools import lru_cache

import threading
import time

from flask import Blueprint, Response, abort, current_app, jsonify, request, send_from_directory
from flask_login import current_user, login_required

from app.comercial_me.routes import idioma_atual
from app.models import Tab

from . import almoxarifado, base, entrada, movimentos, painel, pedidos, planilha_pedidos, servico, slide

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
    if not (servico.DIR / nome).is_file():
        return Response('O arquivo deste relatório não está mais no computador (foi apagado ou a geração não terminou). '
                        'Volte à tela e clique em "Gerar relatório" de novo.', status=404, mimetype='text/plain; charset=utf-8')
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


_slide_cache = {}


def _montar_slide(ano, mes):
    """slide.montar com cache: so refaz quando os blocos de notas mudam (a conta leva segundos)."""
    chave = (servico._chave_blocos(), ano, mes)
    if chave not in _slide_cache:
        if len(_slide_cache) > 12:
            _slide_cache.clear()
        _slide_cache[chave] = slide.montar(_derivados(chave[0], (ano, ano - 1)), mes, ano)
    return dict(_slide_cache[chave])


def dados_do_slide(ano=None, mes=None):
    """Dados do slide mensal (o mesmo da tela) para um mes/ano; None se ainda nao ha notas carregadas."""
    idx = servico._indice()
    if not idx:
        return None
    anos = sorted({int(ym[:4]) for ym in idx})
    ano = ano or anos[-1]
    meses = sorted(int(ym[5:]) for ym in idx if ym.startswith(f'{ano}-'))
    if not meses:
        ano = anos[-1]
        meses = sorted(int(ym[5:]) for ym in idx if ym.startswith(f'{ano}-'))
    mes = mes or meses[-1]
    return _montar_slide(ano, mes)


@bi_bp.route('/entrada-pedidos')
@login_required
def entrada_pedidos():
    """Entrada de pedidos por dia (2024-2026), do TOTVS. ?mes=1..12"""
    _autorizar()
    if entrada.configurado() and not entrada.ESTADO['rodando'] and entrada._ler().get('versao') != entrada.VERSAO:
        threading.Thread(target=entrada.atualizar, daemon=True, name='totvs-entrada-1a').start()      # 1a vez: busca o historico em segundo plano
    return _json(entrada.resumo(request.args.get('mes', type=int)))


@bi_bp.route('/pedidos-alterados')
@login_required
def pedidos_alterados():
    """Pedidos alterados do 1o dia do mes ate ontem (secao separada da Entrada de pedidos)."""
    _autorizar()
    if not entrada.configurado():
        return _json(dict(ok=False, erro='TOTVS não configurado no .env.')), 400
    try:
        return _json(dict(ok=True, **entrada.alterados()))
    except Exception as e:  # noqa: BLE001
        return _json(dict(ok=False, erro=str(e)[:300])), 502


@bi_bp.route('/ultimo-pedido')
@login_required
def ultimo_pedido():
    """Ultimo pedido que entrou (faixa fixa das abas BI COMERCIAL, COMERCIAL MI e COMERCIAL ME)."""
    tabs = Tab.query.filter(Tab.slug.in_(['dashboard-comercial', 'pedidos-comercial-mi', 'pedidos-comercial-me'])).all()
    if not any(t.visible_to(current_user) for t in tabs):
        abort(403)
    return _json(dict(ultimo=entrada.ultimo(), estado=dict(ultima=entrada.ESTADO.get('ultima'), erro=entrada.ESTADO.get('erro'), rodando=entrada.ESTADO.get('rodando'), msg=entrada.ESTADO.get('msg'))))


@bi_bp.route('/pedidos-planilha', methods=['POST'])
@login_required
def pedidos_planilha():
    """Envia a planilha "Pedido de venda": dela saem os pedidos de 2023 e 2024 (2025 e 2026 vem do TOTVS)."""
    import io
    _autorizar()
    from app.relatorio_fat.routes import pode_gerar
    if not pode_gerar(current_user):
        abort(403)
    f = request.files.get('arquivo')
    if not f or not f.filename.lower().endswith('.xlsx'):
        return _json(dict(ok=False, erro='Escolha o arquivo .xlsx da planilha "Pedido de venda".')), 400
    try:
        r = planilha_pedidos.importar(io.BytesIO(f.read()))
    except Exception as e:  # noqa: BLE001
        return _json(dict(ok=False, erro=f'Não consegui ler a planilha: {e}')), 400
    return _json(dict(ok=True, **r))


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
    t0 = time.time()
    out = _montar_slide(ano, mes)
    if time.time() - t0 > 2:
        print(f'[bi-comercial] slide mensal {mes}/{ano} levou {time.time() - t0:.1f}s', flush=True)
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
    from app.models import BaixaFio, PesagemResumo
    amostra = BaixaFio.query.filter(BaixaFio.casada.is_(True)).count(), BaixaFio.query.filter(BaixaFio.gramatura.isnot(None)).count(), PesagemResumo.query.filter(PesagemResumo.gramatura.isnot(None)).count()
    return _json(dict(versao='gramatura-v22', baixas_casadas=amostra[0], baixas_com_gramatura=amostra[1], ops_com_gramatura=amostra[2], estado=dict(movimentos.ESTADO), pecas_no_banco=PesagemMalharia.query.count(), ops_no_banco=len(ops_db),
                      ops_fora_da_programacao=len(ops_db - prog)))


@mov_bp.route('/resumo')
@login_required
def movimentos_resumo():
    """Texto curto (cabe numa tela): pecas do Gestao por data do movimento, ultimos 8 dias, e de onde veio a hora de cada uma."""
    from datetime import date as _d, timedelta as _t
    from flask import Response
    from app.models import PesagemMalharia
    ini = _d.today() - _t(days=8)
    from app.models import BaixaFio
    from app.totvs import movimentos as _mv
    linhas = ['versao: baixas-v16', f'baixas de fio no banco: {BaixaFio.query.count()} | estado: ' + str({k: v for k, v in _mv.ESTADO.items() if k.startswith(('baixas', 'erro', 'ultima'))}), 'data | pecas | metros | hora da baixa | sem hora | tardia | hora em outro dia']
    dist = {}
    for q in PesagemMalharia.query.filter(PesagemMalharia.data >= ini).all():
        d = dist.setdefault(q.data, [0, 0.0, 0, 0, 0, 0])
        d[0] += 1
        d[1] += q.metros or 0
        d[2] += q.hora_origem == 'trx'
        d[3] += q.hora_origem not in ('trx', 'tardia')
        d[4] += q.hora_origem == 'tardia'
        d[5] += bool(q.hora_real and q.hora_origem in ('trx', 'tardia') and q.hora_real.date() != q.data)
    for dia, d in sorted(dist.items()):
        linhas.append(f'{dia} | {d[0]} | {d[1]:.1f} | {d[2]} | {d[3]} | {d[4]} | {d[5]}')
    return Response('\n'.join(linhas), mimetype='text/plain')


@mov_bp.route('/baixas')
@login_required
def movimentos_baixas():
    """Texto curto: quantas baixas de fio (operacao 2000, uma por peca pesada) o TOTVS tem no dia de producao (?dia=2026-10-09,
    de 06:20 ate 06:19 do dia seguinte), para comparar com as 242 pecas da planilha."""
    from datetime import date as _d, datetime as _dt, timedelta as _t
    from flask import Response
    from app.totvs import movimentos
    dia = _d.fromisoformat(request.args.get('dia') or _d.today().isoformat())
    ini, fim = _dt.combine(dia, _dt.min.time()) + _t(hours=6, minutes=20), _dt.combine(dia, _dt.min.time()) + _t(days=1, hours=6, minutes=20)
    linhas, itens, pag = [f'dia de producao {dia}: {ini:%d/%m %H:%M} ate {fim:%d/%m %H:%M} (exclusive)'], [], 1
    with movimentos._trava:
        cli = movimentos._cliente()
        while True:
            r = cli.post(movimentos.TRX_URL, dict(branchCodeList=[movimentos.EMPRESA], operationCodeList=[movimentos.BAIXA_FIO],
                                                  startChangeDate=f'{dia.isoformat()}T00:00:00', endChangeDate=f'{(dia + _t(days=2)).isoformat()}T00:00:00',
                                                  page=pag, pageSize=500))
            if r.status_code != 200:
                return Response('\n'.join(linhas + [f'erro HTTP {r.status_code}: {r.text[:300]}']), mimetype='text/plain')
            j = r.json()
            itens += j.get('items') or []
            if not j.get('hasNext') or pag >= 60:
                break
            pag += 1
    dentro, kg = [], 0.0
    for t in itens:
        h = movimentos._dt(t.get('lastchangeDate'))
        if h and ini <= h < fim:
            dentro.append(t)
            kg += sum(movimentos._num(i.get('quantity')) for i in t.get('items') or [] if str(i.get('measuredUnit') or '').strip().upper() == 'KG')
    clientes = {}
    for t in dentro:
        clientes[t.get('customerCode')] = clientes.get(t.get('customerCode'), 0) + 1
    linhas += [f'baixas de fio buscadas (2 dias): {len(itens)}', f'baixas dentro do dia de producao: {len(dentro)}  (kg: {kg:.1f})',
               'por cliente: ' + ', '.join(f'{c}={n}' for c, n in sorted(clientes.items(), key=lambda x: -x[1])[:15])]
    return Response('\n'.join(linhas), mimetype='text/plain')


@mov_bp.route('/baixa-amostra')
@login_required
def movimentos_baixa_amostra():
    """Mostra 3 baixas de fio cruas (todos os campos que a API devolve) para ver o que liga a baixa a OP/maquina/artigo."""
    from datetime import date as _d, timedelta as _t
    from app.totvs import movimentos
    dia = _d.fromisoformat(request.args.get('dia') or _d.today().isoformat())
    with movimentos._trava:
        cli = movimentos._cliente()
        r = cli.post(movimentos.TRX_URL, dict(branchCodeList=[movimentos.EMPRESA], operationCodeList=[movimentos.BAIXA_FIO],
                                              startChangeDate=f'{dia.isoformat()}T00:00:00', endChangeDate=f'{(dia + _t(days=1)).isoformat()}T00:00:00',
                                              expand='items', page=1, pageSize=3))
    return _json(dict(status=r.status_code, amostra=(r.json().get('items') if r.status_code == 200 else r.text[:400])))


@mov_bp.route('/baixas-sem-op')
@login_required
def movimentos_baixas_sem_op():
    """Texto: baixas de fio do dia de producao (?dia=2026-10-09) que nao ligaram a nenhuma peca 23->24 (sem OP): hora, cliente e kg,
    para achar a mesma linha na planilha do TOTVS e ver a OP dela."""
    from datetime import date as _d, datetime as _dt, timedelta as _t
    from flask import Response
    from app.models import BaixaFio
    dia = _d.fromisoformat(request.args.get('dia') or _d.today().isoformat())
    ini = _dt.combine(dia, _dt.min.time()) + _t(hours=6, minutes=20)
    bx = BaixaFio.query.filter(BaixaFio.mudou >= ini, BaixaFio.mudou < ini + _t(days=1)).order_by(BaixaFio.mudou).all()
    com, sem = [b for b in bx if b.casada], [b for b in bx if not b.casada]
    linhas = [f'dia de producao {dia}: {len(bx)} baixas | com OP: {len(com)} | sem OP: {len(sem)}', '', 'hora | cliente | kg | fios']
    linhas += [f'{b.mudou:%d/%m %H:%M} | {b.cliente_cod} | {b.kg:.3f} | {b.composicao}' for b in sem[:80]]
    return Response('\n'.join(linhas), mimetype='text/plain')


@mov_bp.route('/produto')
@login_required
def movimentos_produto():
    """Mostra o produto (artigo) cru do TOTVS de uma OP (?op=2687) para ver se traz gramatura/largura/peso."""
    from app.totvs import movimentos
    op = request.args.get('op', '')
    with movimentos._trava:
        cli = movimentos._cliente()
        r = cli.post(movimentos.OPS_URL, dict(filter=dict(branchCodeList=[movimentos.EMPRESA], cycleCodeList=[13], orderCodeList=[int(op)]), expand='items', pageSize=1, page=1))
        itens = (r.json().get('items') or [{}])[0].get('items') or [] if r.status_code == 200 else []
        cod = itens[0].get('productCode') if itens else None
        out = dict(op=op, item_da_op=itens[:1])
        if cod:
            for nome, exp in (('sem_expand', None), ('classifications', 'classifications')):
                corpo = dict(filter=dict(productCodeList=[cod]), option=dict(branchInfoCode=movimentos.EMPRESA), page=1, pageSize=1)
                if exp:
                    corpo['expand'] = exp
                rp = cli.post(movimentos.BASE_URL + '/api/totvsmoda/product/v2/products/search', corpo)
                out[nome] = (rp.json().get('items') or [None])[0] if rp.status_code == 200 else f'HTTP {rp.status_code} {rp.text[:200]}'
    return _json(out)


@mov_bp.route('/dia')
@login_required
def movimentos_dia():
    """Texto curto: totais de um dia de producao (?dia=2026-10-08) por turno, com a mesma conta do Controle de Producao
    (pecas = baixas de fio, metros = metro padrao da OP, kg = peso da peca), para comparar com a planilha do TOTVS."""
    import json as _j
    from datetime import date as _d, datetime as _dt, timedelta as _t
    from flask import Response
    from app.main.routes import _api_baixas, _hub_movimentos_json
    dia = _d.fromisoformat(request.args.get('dia') or _d.today().isoformat())
    ini = _dt.combine(dia, _dt.min.time()) + _t(hours=6, minutes=20)
    linhas = _j.loads('[' + _api_baixas(dict(_hub_movimentos_json())) + ']')
    turnos = {'1o turno (06:20-14:19)': [0, 0.0, 0.0, 0], '2o turno (14:20-22:19)': [0, 0.0, 0.0, 0], '3o turno (22:20-06:19)': [0, 0.0, 0.0, 0]}
    tot = [0, 0.0, 0.0, 0]
    por_op = {}
    for r in linhas:
        h = _dt.fromisoformat(r['dt_real'].replace('Z', '')) - _t(hours=3)
        if not (ini <= h < ini + _t(days=1)) or (r.get('cod_artigo') and 'amostra' in r['cod_artigo'].lower()):
            continue
        k = int((h - ini).total_seconds() // 28800)
        po = por_op.setdefault(r.get('op') or 'SEM-OP', [0, 0.0, 0.0])
        po[0] += 1
        po[1] += r.get('metro_padrao') or 0
        po[2] += r.get('qt_pesos') or 0
        nome = list(turnos)[min(k, 2)]
        for alvo in (turnos[nome], tot):
            alvo[0] += 1
            alvo[1] += r.get('metro_padrao') or 0
            alvo[2] += r.get('qt_pesos') or 0
            alvo[3] += 0 if r.get('op') else 1
    out = [f'dia de producao {dia} ({ini:%d/%m %H:%M} ate {(ini + _t(days=1)):%d/%m %H:%M})', 'turno | pecas | metros (padrao) | kg | pecas sem OP']
    out += [f'{n} | {v[0]} | {v[1]:.0f} | {v[2]:.1f} | {v[3]}' for n, v in turnos.items()]
    out.append(f'TOTAL | {tot[0]} | {tot[1]:.0f} | {tot[2]:.1f} | {tot[3]}')
    out.append('')
    out.append('op | pecas | metros (padrao) | kg')
    out += [f'{o} | {v[0]} | {v[1]:.0f} | {v[2]:.1f}' for o, v in sorted(por_op.items(), key=lambda x: (not x[0].isdigit(), int(x[0]) if x[0].isdigit() else 0))]
    from app.models import PesagemMalharia
    sem = PesagemMalharia.query.filter(PesagemMalharia.trx_codigo.is_(None), PesagemMalharia.data >= dia - _t(days=1), PesagemMalharia.data <= dia + _t(days=1)).count()
    out.append(f'pecas 23->24 (datas {dia - _t(days=1):%d/%m} a {dia + _t(days=1):%d/%m}) ainda sem baixa ligada: {sem}')
    return Response('\n'.join(out), mimetype='text/plain')


@mov_bp.route('/lote')
@login_required
def movimentos_lote():
    """Lotes do TOTVS de um produto (?produto=1082&dia=2026-10-08): mostra os campos crus de 2 lotes (3 itens de cada)
    para ver se o lote traz peso, data/hora e OP de cada peca pesada."""
    from datetime import date as _d, timedelta as _t
    from app.totvs import movimentos
    dia = _d.fromisoformat(request.args.get('dia') or _d.today().isoformat())
    produto = int(request.args.get('produto', '1082'))
    with movimentos._trava:
        cli = movimentos._cliente()
        r = cli.post(movimentos.BATCH_URL, dict(filter=dict(branchCode=movimentos.EMPRESA, productCodeList=[produto],
                                                            updatedDateStart=f'{(dia - _t(days=10)).isoformat()}T00:00:00', updatedDateEnd=f'{(dia + _t(days=2)).isoformat()}T23:59:59'),
                                                page=1, pageSize=50))
    if r.status_code != 200:
        return _json(dict(status=r.status_code, erro=r.text[:400]))
    lotes = r.json().get('items') or []
    amostra = []
    for b in lotes[-2:]:
        b = dict(b)
        its = b.get('batchItem') or []
        b['batchItem'] = its[:3]
        b['total_itens_do_lote'] = len(its)
        amostra.append(b)
    return _json(dict(total_lotes=len(lotes), amostra=amostra))


@mov_bp.route('/endpoints')
@login_required
def movimentos_endpoints():
    """Lista os endpoints da API do TOTVS de um servico (?servico=production | production-order | general | product | ...),
    lendo o swagger dele: metodo, caminho e descricao. Serve para achar uma consulta que traga cada peca pesada com OP e hora."""
    from flask import Response
    from app.totvs import movimentos
    servico = request.args.get('servico', 'production')
    versao = request.args.get('versao', 'v2')
    url = f'{movimentos.BASE_URL}/api/totvsmoda/{servico}/{versao}/swagger/v1/swagger.json'
    with movimentos._trava:
        cli = movimentos._cliente()
        r = cli.session.get(url, timeout=120)
    if r.status_code != 200:
        return Response(f'{url}\nHTTP {r.status_code} {r.text[:300]}', mimetype='text/plain')
    doc = r.json()
    if request.args.get('caminho'):                            # detalhe de um endpoint: campos do pedido e da resposta
        def resolver(sch, prof=0):
            if not isinstance(sch, dict) or prof > 4:
                return str(sch)[:60]
            if '$ref' in sch:
                nome = sch['$ref'].split('/')[-1]
                alvo = doc
                for parte in sch['$ref'].lstrip('#/').split('/'):
                    alvo = alvo.get(parte, {}) if isinstance(alvo, dict) else {}
                return {nome: resolver(alvo, prof + 1)}
            if sch.get('type') == 'array':
                return [resolver(sch.get('items', {}), prof + 1)]
            if 'properties' in sch:
                return {k: resolver(v, prof + 1) if (isinstance(v, dict) and ('$ref' in v or v.get('type') in ('array', 'object'))) else (v.get('type', '') + (' - ' + v['description'][:80] if v.get('description') else '')) for k, v in sch['properties'].items()}
            return sch.get('type', '')
        det = (doc.get('paths') or {}).get(request.args['caminho'], {})
        saida = {}
        for metodo, d in det.items():
            if not isinstance(d, dict):
                continue
            corpo = ((d.get('requestBody') or {}).get('content') or {}).get('application/json', {}).get('schema', {})
            resp = (((d.get('responses') or {}).get('200') or {}).get('content') or {}).get('application/json', {}).get('schema', {})
            saida[metodo] = dict(descricao=d.get('description') or d.get('summary'), parametros=[x.get('name') for x in d.get('parameters') or []], pedido=resolver(corpo), resposta=resolver(resp))
        return _json(saida)
    linhas = [url]
    for caminho, ops in sorted((doc.get('paths') or {}).items()):
        for metodo, d in ops.items():
            if isinstance(d, dict):
                linhas.append(f"{metodo.upper()} {caminho} - {(d.get('summary') or d.get('description') or '')[:140]}")
    return Response('\n'.join(linhas), mimetype='text/plain')


@mov_bp.route('/tipos')
@login_required
def movimentos_tipos():
    """Texto: testa os filtros de tipo da consulta de movimentos (tipo de OP e tipo de movimento) para o dia (?dia=2026-10-09)
    e conta as pecas 23->24 de cada combinacao, para achar as que a consulta padrao deixa de fora."""
    from datetime import date as _d, timedelta as _t
    from flask import Response
    from app.totvs import movimentos
    dia = _d.fromisoformat(request.args.get('dia') or _d.today().isoformat())
    base = dict(branchCodeList=[movimentos.EMPRESA], movementStartDate=dia.isoformat(), movementEndDate=(dia + _t(days=1)).isoformat(),
                listNormalMovement=True, listFinalizationMovement=True, reversalType=3, originsLocalCodeList=[23], destinationLocalCodeList=[24], pageSize=500)
    testes = [('padrao (ciclo 13)', dict(cycleCodeList=[13])), ('sem ciclo', {}),
              ('tipoOP 1..6', dict(productionOrderTypeList=[1, 2, 3, 4, 5, 6])), ('tipoOP 2', dict(productionOrderTypeList=[2])),
              ('tipoOP 3', dict(productionOrderTypeList=[3])), ('movementTypes 1..6', dict(movementTypes=[1, 2, 3, 4, 5, 6])),
              ('finalizationTypes 1..6', dict(finalizationTypes=[1, 2, 3, 4, 5, 6]))]
    linhas = [f'dia {dia}: pecas 23->24 por filtro (movimentos do dia {dia:%d/%m} e {dia + _t(days=1):%d/%m})']
    with movimentos._trava:
        cli = movimentos._cliente()
        for nome, extra in testes:
            itens, pag, erro = [], 1, None
            while True:
                r = cli.post(movimentos.MOV_URL, dict(base, **extra, page=pag))
                if r.status_code != 200:
                    erro = f'HTTP {r.status_code} {r.text[:120]}'
                    break
                j = r.json()
                itens += j.get('items') or []
                if not j.get('hasNext') or pag >= 40:
                    break
                pag += 1
            if erro:
                linhas.append(f'{nome}: {erro}')
                continue
            p24 = [m for m in itens if m.get('originLocationCode') == 23 and m.get('destinationLocationCode') == 24]
            g = {}
            for m in p24:
                k = f"tipoOP={m.get('productionOrderType')} ciclo={m.get('cycle')} {m.get('movementType')}/{m.get('movementHistory')}"
                g[k] = g.get(k, 0) + 1
            linhas.append(f'{nome}: total {len(p24)} | ' + '; '.join(f'{k}={v}' for k, v in sorted(g.items())))
    return Response('\n'.join(linhas), mimetype='text/plain')


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
            corpo_base = dict(branchCodeList=[movimentos.EMPRESA], cycleCodeList=[13], movementStartDate=dia.isoformat(), movementEndDate=(dia + __import__('datetime').timedelta(days=2)).isoformat(),
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
                               metros_23_para_24=round(sum(m.get('totalQuantity') or 0 for m in p24), 1),
                               metros_23_sem_destino=round(sum(m.get('totalQuantity') or 0 for m in de23 if m.get('destinationLocationCode') is None), 1),
                               amostra_23_sem_destino=[dict(op=m.get('productionOrder'), qtd=m.get('totalQuantity'), historico=m.get('movementHistory')) for m in de23 if m.get('destinationLocationCode') is None][:6],
                               pecas_23_para_24_por_data={d: sum(1 for m in p24 if str(m.get('movementDate'))[:10] == d) for d in sorted({str(m.get('movementDate'))[:10] for m in p24})})
    # variacoes de filtro: o que mais a API tem saindo do local 23 para o 24 neste dia (outras filiais, ciclos ou tipos de OP)
    variantes = {}
    base = dict(movementStartDate=dia.isoformat(), movementEndDate=(dia + __import__('datetime').timedelta(days=1)).isoformat(),
                listNormalMovement=True, listFinalizationMovement=True, reversalType=3, pageSize=500)
    with movimentos._trava:
        for nome, extra in (('so_filial_1_ciclo_13', dict(branchCodeList=[1], cycleCodeList=[13])), ('filial_1_todos_ciclos', dict(branchCodeList=[1])),
                            ('todas_filiais_ciclo_13', dict(cycleCodeList=[13])), ('filiais_1_a_6_todos_ciclos', dict(branchCodeList=[1, 2, 3, 4, 5, 6]))):
            itens, pag, erro = [], 1, None
            while True:
                r = cli.post(movimentos.MOV_URL, dict(base, **extra, page=pag))
                if r.status_code != 200:
                    erro = f'HTTP {r.status_code} {r.text[:200]}'
                    break
                j = r.json()
                itens += j.get('items') or []
                if not j.get('hasNext') or pag >= 60:
                    break
                pag += 1
            if erro:
                variantes[nome] = erro
                continue
            p24 = [m for m in itens if m.get('originLocationCode') == 23 and m.get('destinationLocationCode') == 24]
            cont = {}
            for m in p24:
                k = f"filial={m.get('branchCode')} ciclo={m.get('cycle')} tipoOP={m.get('productionOrderType')} historico={m.get('movementHistory')}"
                cont[k] = cont.get(k, 0) + 1
            variantes[nome] = dict(pecas_23_para_24=len(p24), metros=round(sum(m.get('totalQuantity') or 0 for m in p24), 1), por_grupo=cont)
    saida['variantes'] = variantes
    from app.models import PesagemMalharia
    dist = {}
    for q in PesagemMalharia.query.filter(PesagemMalharia.data >= dia - __import__('datetime').timedelta(days=2)).all():
        d = dist.setdefault(str(q.data), dict(pecas=0, hora_baixa=0, tardia=0, sem_hora=0, hora_em_outro_dia=0, metros=0.0))
        d['pecas'] += 1
        d['metros'] = round(d['metros'] + (q.metros or 0), 1)
        if q.hora_origem == 'trx':
            d['hora_baixa'] += 1
        elif q.hora_origem == 'tardia':
            d['tardia'] += 1
        else:
            d['sem_hora'] += 1
        if q.hora_real and q.hora_origem in ('trx', 'tardia') and q.hora_real.date() != q.data:
            d['hora_em_outro_dia'] += 1
    saida['gestao_por_data_do_movimento'] = dict(sorted(dist.items()))
    saida['gestao_no_banco'] = {str(d): PesagemMalharia.query.filter_by(data=d).count() for d in (dia, dia + __import__('datetime').timedelta(days=1))}
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


# ---------------------------------------------------------------------------
# Controle do Estoque (Almoxarifado): materia-prima do local 34, direto do TOTVS
# ---------------------------------------------------------------------------
almox_bp = Blueprint('almoxarifado_totvs', __name__, url_prefix='/estoque-almox')


def _autorizar_almox():
    tab = Tab.query.filter_by(slug='controle-estoque').first_or_404()
    if not tab.visible_to(current_user):
        abort(403)


@almox_bp.route('/dados')
@login_required
def almox_dados():
    _autorizar_almox()
    d = almoxarifado.ler()
    return _json({**{k: v for k, v in d.items() if k != 'versao'}, 'configurado': almoxarifado.configurado(), 'estado': dict(almoxarifado.ESTADO), 'estoque': d.get('estoque') or almoxarifado.estoque_atual(), 'estoques_no_totvs': almoxarifado._RES.get('estoques')})


@almox_bp.route('/atualizar', methods=['POST'])
@login_required
def almox_atualizar():
    _autorizar_almox()
    if not almoxarifado.configurado():
        return _json(dict(ok=False, erro='TOTVS não configurado no .env.')), 400
    threading.Thread(target=almoxarifado.atualizar, daemon=True, name='almox-agora').start()
    return _json(dict(ok=True))


@almox_bp.route('/diagnostico')
@login_required
def almox_diagnostico():
    """So para o administrador: mostra exemplos crus do que o TOTVS devolve (para conferir os campos)."""
    if not current_user.is_admin:
        abort(403)
    try:
        return _json(almoxarifado.buscar(amostra=True))
    except Exception as e:  # noqa: BLE001
        return _json(dict(erro=str(e)[:400])), 502


@almox_bp.route('/indicadores')
@login_required
def almox_indicadores():
    _autorizar_almox()
    return _json({**almoxarifado.indicadores(), 'configurado': almoxarifado.configurado(), 'estado': dict(almoxarifado.ESTADO_K)})


@almox_bp.route('/diagnostico-kardex')
@login_required
def almox_diag_kardex():
    """So administrador: testa o kardex de 1 produto (?produto=CODIGO) de varias formas e mostra o que o TOTVS responde em cada uma."""
    if not current_user.is_admin:
        abort(403)
    from datetime import date, timedelta
    cod = request.args.get('produto', type=int) or ((almoxarifado.ler().get('itens') or [{}])[0].get('codigo'))
    if not cod:
        return _json(dict(erro='A lista de materias-primas esta vazia: nao ha produto para testar. Use ?produto=CODIGO.'))
    try:
        cli = almoxarifado.api.TotvsModaClient()
        cli.autenticar()
    except Exception as e:  # noqa: BLE001
        return _json(dict(erro=str(e)[:400])), 502
    hoje = date.today()
    ts = almoxarifado.tipo_saldo()

    def tenta(nome, **p):
        base = dict(ProductCode=cod, BranchCode=almoxarifado.EMPRESA)
        base.update(p)
        base = {k: v for k, v in base.items() if v is not None}
        try:
            r = cli.get(almoxarifado.KARDEX_URL, base)
            try:
                j = r.json()
            except ValueError:
                j = None
            item = j[0] if isinstance(j, list) and j else (j if isinstance(j, dict) else {})
            movs = item.get('movements') or [] if isinstance(item, dict) else []
            return dict(variante=nome, parametros={k: str(v) for k, v in base.items()}, http=r.status_code, n_movimentos=len(movs), erro=(r.text[:160] if r.status_code != 200 else None),
                        exemplo=[dict(data=str(m.get('movementDate'))[:10], operacao=m.get('operationCode'), historico=m.get('historyName'), entrada=m.get('inQuantity'), saida=m.get('outQuantity')) for m in movs[:3]])
        except Exception as e:  # noqa: BLE001
            return dict(variante=nome, erro=str(e)[:160])

    def dt(d, fim=False):
        return f'{d.isoformat()}T{"23:59:59" if fim else "00:00:00"}'

    v = []
    for dias in (179, 90, 30):
        ini = hoje - timedelta(days=dias)
        v.append(tenta(f'tipo saldo {ts}, {dias} dias, com hora', StartDate=dt(ini), EndDate=dt(hoje, True), BalanceType=ts))
    v.append(tenta('so datas (sem hora), 90 dias', StartDate=(hoje - timedelta(days=90)).isoformat(), EndDate=hoje.isoformat(), BalanceType=ts))
    v.append(tenta('sem BalanceType, 90 dias', StartDate=dt(hoje - timedelta(days=90)), EndDate=dt(hoje, True)))
    for outro in (0, 1, 2):
        if outro != ts:
            v.append(tenta(f'BalanceType={outro}, 90 dias', StartDate=dt(hoje - timedelta(days=90)), EndDate=dt(hoje, True), BalanceType=outro))
    return _json(dict(produto=cod, tipo_saldo_configurado=ts, operacoes_de_saida_contadas=sorted(almoxarifado.OPERACOES_SAIDA), tentativas=v))
