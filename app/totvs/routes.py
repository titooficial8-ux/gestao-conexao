"""BI COMERCIAL: dados do faturamento TOTVS (dashboard, geracao sob demanda e downloads)."""
from datetime import date, timedelta
from functools import lru_cache

from flask import Blueprint, abort, jsonify, request, send_from_directory
from flask_login import current_user, login_required

from app.comercial_me.routes import idioma_atual
from app.models import Tab

from . import base, painel, servico, slide

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
        padrao=servico.meta('padrao'), custom=servico.meta('custom'),
        proxima=servico.proxima_atualizacao(), meses=servico.meses_armazenados(), antigos=servico.meses_antigos(),
    )


def _json(obj):
    r = jsonify(obj)
    r.headers['Cache-Control'] = 'no-store'
    return r


@bi_bp.route('/status')
@login_required
def status():
    _autorizar()
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
        iniciou = servico.iniciar(date(date.today().year - 1, 1, 1), date.today(), 'base', 'historico')
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


@lru_cache(maxsize=1)
def _derivados(chave):
    cols, linhas, _ = servico.linhas_armazem()
    return [base.derivar(dict(zip(cols, r))) for r in linhas] if cols else []


@bi_bp.route('/slide')
@login_required
def slide_mensal():
    _autorizar()
    regs = _derivados(servico._chave_blocos())
    if not regs:
        return _json(dict(vazio=True))
    anos = sorted({x['ano'] for x in regs if x['ano']})
    ano = request.args.get('ano', type=int) or anos[-1]
    meses = sorted({x['mes'] for x in regs if x['ano'] == ano and x['mes']})
    mes = request.args.get('mes', type=int) or (meses[-1] if meses else 1)
    out = slide.montar(regs, mes, ano)
    out.update(vazio=False, anos=anos, meses=meses, gerado_em=servico.linhas_armazem()[2], plano_origem=base._ler('origem'))
    return _json(out)
