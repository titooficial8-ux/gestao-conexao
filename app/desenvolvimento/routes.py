"""Gestao Desenvolvimento: Cronograma, Cotacoes, Cronograma de Amostras (TOTVS) e Cronograma de Projetos (+ Colecao de Campanhas / Desenhos).

Regras de acesso (a visibilidade das abas continua pelo Setor, em Tab.visible_to):
  - Desenvolvimento e administrador: editam. Cada pessoa do Desenvolvimento edita as proprias tarefas.
  - Comercial: so visualiza o Cronograma/Projetos; nas Cotacoes pede, aprova, reprova e pede alteracao.
"""
from __future__ import annotations

from datetime import date, datetime

import mimetypes
import os
import uuid

from flask import Blueprint, abort, current_app, jsonify, request, send_file
from flask_login import current_user, login_required

from app import db
from app.desenvolvimento.models import CampanhaDesenho, Cotacao, CotacaoEvento, DevArquivo, DevProjeto, DevTarefa
from app.models import Tab, User

dev_bp = Blueprint('desenvolvimento', __name__, url_prefix='/dev')

ABAS = {  # slug -> (nome pt, nome es, icone, cor, setores que veem)
    'cronograma-dev': ('Cronograma', 'Cronograma', '\U0001F4C5', '#2f7fe0', ('Desenvolvimento', 'Comercial', 'Diretoria')),
    'cotacoes': ('Cotações', 'Cotizaciones', '\U0001F4B2', '#1f9d55', ('Desenvolvimento', 'Comercial', 'Diretoria')),
    'cronograma-amostras': ('Cronograma de Amostras', 'Cronograma de Muestras', '\U0001F9F5', '#e0972f', ('Desenvolvimento', 'Comercial', 'Diretoria')),
    'cronograma-projetos': ('Cronograma de Projetos', 'Cronograma de Proyectos', '\U0001F5C2', '#8a5fe0', ('Desenvolvimento', 'Comercial', 'Diretoria')),
    'laudos-desenvolvimento': ('Laudos Desenvolvimento', 'Informes de Desarrollo', '\U0001F4CB', '#3fa34d', ('Desenvolvimento', 'Comercial', 'Diretoria')),
    'produtos-tecnologicos': ('Produtos Tecnológicos', 'Productos Tecnológicos', '\U0001F52C', '#2f7fe0', ('Desenvolvimento', 'Comercial', 'Diretoria')),
    'material-institucional': ('Material Institucional', 'Material Institucional', '\U0001F3A8', '#e0972f', ('Desenvolvimento', 'Comercial', 'Diretoria')),
}


def eh_dev(user=None) -> bool:
    u = user or current_user
    return bool(getattr(u, 'is_authenticated', False)) and (u.is_admin or u.has_sector('Desenvolvimento'))


def eh_comercial(user=None) -> bool:
    u = user or current_user
    return bool(getattr(u, 'is_authenticated', False)) and (u.is_admin or u.has_sector('Comercial'))


def _autorizar(slug: str):
    tab = Tab.query.filter_by(slug=slug).first_or_404()
    if not tab.visible_to(current_user):
        abort(403)


def _exigir_dev():
    if not eh_dev():
        abort(403)


def garantir_abas() -> None:
    """Cria as abas do Desenvolvimento que ainda nao existem (bancos novos e antigos) e liga os setores certos a cada uma."""
    from app.models import Category, Sector
    cat = Category.query.filter_by(slug='desenvolvimento').first()
    if not cat:
        return
    setores = {s.name: s for s in Sector.query.all()}
    ordem = max([t.order or 0 for t in cat.tabs] or [0])
    for slug, (pt, es, icone, cor, quem) in ABAS.items():
        tab = Tab.query.filter_by(slug=slug).first()
        if not tab:
            ordem += 1
            tab = Tab(category_id=cat.id, slug=slug, name_pt=pt, name_es=es, icon=icone, icon_color=cor, is_new=True, order=ordem)
            db.session.add(tab)
            tab.sectors = [setores[n] for n in quem if n in setores]
    db.session.commit()


# ------------------------------------------------------------------ utilitarios
def _data(v):
    try:
        return date.fromisoformat(str(v)[:10]) if v else None
    except ValueError:
        return None


def _iso(d):
    return d.isoformat() if d else None


def _nome(u):
    return u.name.split(' - ')[0].split(' (')[0] if u else None


@dev_bp.route('/usuarios')
@login_required
def usuarios():
    """Pessoas do Desenvolvimento (para escolher o responsavel)."""
    lista = [u for u in User.query.filter_by(status='ativo').order_by(User.name).all() if u.has_sector('Desenvolvimento')]
    return jsonify([dict(id=u.id, nome=_nome(u), email=u.email) for u in lista])


@dev_bp.route('/eu')
@login_required
def eu():
    return jsonify(id=current_user.id, nome=_nome(current_user), dev=eh_dev(), comercial=eh_comercial(), admin=bool(current_user.is_admin))


# ------------------------------------------------------------------ cronograma (tarefas)
def _tarefa(t: DevTarefa) -> dict:
    hoje = date.today()
    return dict(id=t.id, titulo=t.titulo, descricao=t.descricao, projeto=t.projeto, responsavel_id=t.responsavel_id, responsavel=_nome(t.responsavel), inicio=_iso(t.inicio), prazo=_iso(t.prazo),
                status=t.status, prioridade=t.prioridade, progresso=t.progresso or 0, atrasada=bool(t.prazo and t.status != 'CONCLUIDO' and t.prazo < hoje),
                concluido_em=_iso(t.concluido_em), pode_editar=bool(current_user.is_admin or (eh_dev() and t.responsavel_id == current_user.id)))


def _aplicar_tarefa(t: DevTarefa, j: dict):
    for k in ('titulo', 'descricao', 'projeto'):
        if k in j:
            setattr(t, k, (j[k] or '').strip() or None)
    for k in ('inicio', 'prazo'):
        if k in j:
            setattr(t, k, _data(j[k]))
    if 'status' in j and j['status'] in DevTarefa.STATUS:
        t.status = j['status']
        t.concluido_em = datetime.utcnow() if t.status == 'CONCLUIDO' else None
        if t.status == 'CONCLUIDO':
            t.progresso = 100
    if 'prioridade' in j and j['prioridade'] in ('BAIXA', 'MEDIA', 'ALTA'):
        t.prioridade = j['prioridade']
    if 'progresso' in j:
        try:
            t.progresso = max(0, min(100, int(j['progresso'])))
        except (TypeError, ValueError):
            pass
    if 'responsavel_id' in j and current_user.is_admin:        # so o administrador passa a tarefa para outra pessoa
        t.responsavel_id = int(j['responsavel_id']) if j['responsavel_id'] else None


@dev_bp.route('/tarefas')
@login_required
def tarefas():
    _autorizar('cronograma-dev')
    return jsonify([_tarefa(t) for t in DevTarefa.query.order_by(DevTarefa.prazo.is_(None), DevTarefa.prazo, DevTarefa.id).all()])


@dev_bp.route('/tarefas', methods=['POST'])
@login_required
def tarefa_criar():
    _autorizar('cronograma-dev')
    _exigir_dev()
    j = request.get_json(silent=True) or {}
    if not (j.get('titulo') or '').strip():
        return jsonify(erro='Informe o título da tarefa.'), 400
    t = DevTarefa(criado_por_id=current_user.id, responsavel_id=current_user.id)
    if current_user.is_admin and j.get('responsavel_id'):
        t.responsavel_id = int(j['responsavel_id'])
    _aplicar_tarefa(t, j)
    db.session.add(t)
    db.session.commit()
    return jsonify(_tarefa(t))


@dev_bp.route('/tarefas/<int:tid>', methods=['POST', 'PUT'])
@login_required
def tarefa_editar(tid):
    _autorizar('cronograma-dev')
    t = db.session.get(DevTarefa, tid) or abort(404)
    if not (current_user.is_admin or (eh_dev() and t.responsavel_id == current_user.id)):
        abort(403)
    _aplicar_tarefa(t, request.get_json(silent=True) or {})
    db.session.commit()
    return jsonify(_tarefa(t))


@dev_bp.route('/tarefas/<int:tid>', methods=['DELETE'])
@login_required
def tarefa_excluir(tid):
    _autorizar('cronograma-dev')
    t = db.session.get(DevTarefa, tid) or abort(404)
    if not (current_user.is_admin or (eh_dev() and t.responsavel_id == current_user.id)):
        abort(403)
    db.session.delete(t)
    db.session.commit()
    return jsonify(ok=True)


# ------------------------------------------------------------------ cotacoes
ACOES = {  # acao -> (status novo, quem pode: 'dev' | 'com' | 'ambos', status de origem permitidos)
    'assumir': ('EM_ANALISE', 'dev', ('SOLICITADA', 'ALTERACAO')),
    'cotar': ('COTADA', 'dev', ('SOLICITADA', 'EM_ANALISE', 'ALTERACAO')),
    'aprovar': ('APROVADA', 'com', ('COTADA',)),
    'reprovar': ('REPROVADA', 'com', ('COTADA',)),
    'pedir_alteracao': ('ALTERACAO', 'com', ('COTADA', 'APROVADA')),
    'cancelar': ('CANCELADA', 'ambos', ('SOLICITADA', 'EM_ANALISE', 'COTADA', 'ALTERACAO')),
}


def _cotacao(c: Cotacao, completo=False) -> dict:
    d = dict(id=c.id, codigo=f'COT-{c.id:04d}', cliente=c.cliente, artigo=c.artigo, quantidade=c.quantidade, unidade=c.unidade, prazo_desejado=_iso(c.prazo_desejado), observacoes=c.observacoes,
             status=c.status, solicitante=_nome(c.solicitante), responsavel=_nome(c.responsavel), valor_cotado=c.valor_cotado, prazo_entrega=_iso(c.prazo_entrega), resposta=c.resposta,
             criado_em=c.criado_em.isoformat(timespec='minutes') if c.criado_em else None, cotado_em=c.cotado_em.isoformat(timespec='minutes') if c.cotado_em else None,
             decidido_em=c.decidido_em.isoformat(timespec='minutes') if c.decidido_em else None,
             atrasada=bool(c.prazo_desejado and c.status in ('SOLICITADA', 'EM_ANALISE', 'ALTERACAO') and c.prazo_desejado < date.today()),
             horas_resposta=round((c.cotado_em - c.criado_em).total_seconds() / 3600, 1) if c.cotado_em and c.criado_em else None)
    d['acoes'] = [a for a, (_, quem, de) in ACOES.items() if c.status in de and ((quem == 'dev' and eh_dev()) or (quem == 'com' and eh_comercial()) or (quem == 'ambos' and (eh_dev() or eh_comercial())))]
    if completo:
        d['eventos'] = [dict(acao=e.acao, texto=e.texto, status=e.status_para, usuario=_nome(e.user), em=e.criado_em.isoformat(timespec='minutes')) for e in c.eventos]
    return d


def _evento(c, acao, texto=None, status=None):
    db.session.add(CotacaoEvento(cotacao_id=c.id, user_id=current_user.id, acao=acao, texto=(texto or None), status_para=status or c.status))


@dev_bp.route('/cotacoes')
@login_required
def cotacoes():
    _autorizar('cotacoes')
    return jsonify([_cotacao(c) for c in Cotacao.query.order_by(Cotacao.criado_em.desc()).all()])


@dev_bp.route('/cotacoes/<int:cid>')
@login_required
def cotacao_detalhe(cid):
    _autorizar('cotacoes')
    return jsonify(_cotacao(db.session.get(Cotacao, cid) or abort(404), True))


@dev_bp.route('/cotacoes', methods=['POST'])
@login_required
def cotacao_criar():
    _autorizar('cotacoes')
    if not (eh_comercial() or eh_dev()):
        abort(403)
    j = request.get_json(silent=True) or {}
    if not (j.get('cliente') or '').strip() or not (j.get('artigo') or '').strip():
        return jsonify(erro='Informe o cliente e o artigo/descrição.'), 400
    c = Cotacao(cliente=j['cliente'].strip(), artigo=j['artigo'].strip(), unidade=(j.get('unidade') or 'mts'), prazo_desejado=_data(j.get('prazo_desejado')),
                observacoes=(j.get('observacoes') or '').strip() or None, solicitante_id=current_user.id, status='SOLICITADA')
    try:
        c.quantidade = float(str(j.get('quantidade')).replace(',', '.')) if j.get('quantidade') not in (None, '') else None
    except ValueError:
        return jsonify(erro='Quantidade inválida.'), 400
    db.session.add(c)
    db.session.flush()
    _evento(c, 'solicitou', c.observacoes, 'SOLICITADA')
    db.session.commit()
    return jsonify(_cotacao(c, True))


@dev_bp.route('/cotacoes/<int:cid>/acao', methods=['POST'])
@login_required
def cotacao_acao(cid):
    _autorizar('cotacoes')
    c = db.session.get(Cotacao, cid) or abort(404)
    j = request.get_json(silent=True) or {}
    acao = j.get('acao')
    if acao not in ACOES or acao not in _cotacao(c)['acoes']:
        return jsonify(erro='Ação não permitida para esta cotação.'), 403
    novo = ACOES[acao][0]
    texto = (j.get('texto') or '').strip()
    if acao in ('reprovar', 'pedir_alteracao', 'cancelar') and not texto:
        return jsonify(erro='Escreva o motivo.'), 400
    if acao == 'cotar':
        try:
            c.valor_cotado = float(str(j.get('valor')).replace('.', '').replace(',', '.')) if j.get('valor') not in (None, '') else None
        except ValueError:
            return jsonify(erro='Valor inválido.'), 400
        c.prazo_entrega = _data(j.get('prazo_entrega'))
        c.resposta = texto or None
        c.cotado_em = datetime.utcnow()
    if acao in ('assumir', 'cotar') and not c.responsavel_id:
        c.responsavel_id = current_user.id
    if acao in ('aprovar', 'reprovar'):
        c.decidido_em = datetime.utcnow()
    c.status = novo
    _evento(c, acao, texto, novo)
    db.session.commit()
    return jsonify(_cotacao(c, True))


# ------------------------------------------------------------------ projetos e desenhos
def _projeto(p: DevProjeto) -> dict:
    return dict(id=p.id, nome=p.nome, descricao=p.descricao, responsavel_id=p.responsavel_id, responsavel=_nome(p.responsavel), inicio=_iso(p.inicio), prazo=_iso(p.prazo), status=p.status,
                prioridade=p.prioridade, progresso=p.progresso or 0, atrasado=bool(p.prazo and p.status != 'CONCLUIDO' and p.prazo < date.today()))


def _desenho(d: CampanhaDesenho) -> dict:
    return dict(id=d.id, campanha=d.campanha, desenho=d.desenho, cliente=d.cliente, responsavel_id=d.responsavel_id, responsavel=_nome(d.responsavel), inicio=_iso(d.inicio), prazo=_iso(d.prazo),
                status=d.status, observacao=d.observacao, atrasado=bool(d.prazo and d.status not in ('APROVADO', 'SALVO') and d.prazo < date.today()))


def _crud(modelo, serializar, campos, obrigatorio, slug):
    """Rotas de lista/criar/editar/excluir de um cadastro simples (so Desenvolvimento escreve)."""
    base = f'/{modelo.__tablename__.replace("dev_", "")}'

    def listar():
        _autorizar(slug)
        return jsonify([serializar(x) for x in modelo.query.order_by(modelo.prazo.is_(None), modelo.prazo, modelo.id).all()])

    def salvar(rid=None):
        _autorizar(slug)
        _exigir_dev()
        j = request.get_json(silent=True) or {}
        obj = (db.session.get(modelo, rid) or abort(404)) if rid else modelo()
        for k in campos:
            if k in j:
                v = j[k]
                if k in ('inicio', 'prazo'):
                    v = _data(v)
                elif k == 'responsavel_id':
                    v = int(v) if v else None
                elif k == 'progresso':
                    v = max(0, min(100, int(v or 0)))
                elif isinstance(v, str):
                    v = v.strip() or None
                setattr(obj, k, v)
        if not getattr(obj, obrigatorio):
            return jsonify(erro='Preencha o nome.'), 400
        if not rid:
            db.session.add(obj)
        db.session.commit()
        return jsonify(serializar(obj))

    def excluir(rid):
        _autorizar(slug)
        _exigir_dev()
        db.session.delete(db.session.get(modelo, rid) or abort(404))
        db.session.commit()
        return jsonify(ok=True)

    nome = modelo.__tablename__
    dev_bp.add_url_rule(base, f'{nome}_listar', login_required(listar))
    dev_bp.add_url_rule(base, f'{nome}_criar', login_required(salvar), methods=['POST'])
    dev_bp.add_url_rule(base + '/<int:rid>', f'{nome}_editar', login_required(salvar), methods=['POST', 'PUT'])
    dev_bp.add_url_rule(base + '/<int:rid>', f'{nome}_excluir', login_required(excluir), methods=['DELETE'])


_crud(DevProjeto, _projeto, ('nome', 'descricao', 'responsavel_id', 'inicio', 'prazo', 'status', 'prioridade', 'progresso'), 'nome', 'cronograma-projetos')
_crud(CampanhaDesenho, _desenho, ('campanha', 'desenho', 'cliente', 'responsavel_id', 'inicio', 'prazo', 'status', 'observacao'), 'desenho', 'cronograma-projetos')


# ------------------------------------------------------------------ amostras (TOTVS)
@dev_bp.route('/amostras/dados')
@login_required
def amostras_dados():
    _autorizar('cronograma-amostras')
    from app.totvs import amostras, entrada
    if not entrada.configurado():
        return jsonify(ok=False, erro='TOTVS não configurado no .env.'), 400
    try:
        return jsonify(ok=True, **amostras.dados(forcar=request.args.get('atualizar') == '1'))
    except Exception as e:  # noqa: BLE001
        return jsonify(ok=False, erro=str(e)[:300]), 502


# ------------------------------------------------------------------ biblioteca de arquivos (laudos, produtos tecnologicos, material institucional)
GB = 1024 ** 3
MB = 1024 ** 2
VIDEO = {'mp4', 'mov', 'webm', 'm4v', 'avi', 'mkv'}
IMAGEM = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
DOCS = {'pdf', 'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx', 'txt', 'csv'}
SECOES = {  # secao -> (aba, extensoes permitidas, tamanho maximo por arquivo)
    'laudos': ('laudos-desenvolvimento', DOCS | IMAGEM | {'zip'}, 300 * MB),
    'produtos_pdf': ('produtos-tecnologicos', {'pdf'}, 500 * MB),
    'produtos_video': ('produtos-tecnologicos', VIDEO, 2 * GB),
    'institucional': ('material-institucional', DOCS | IMAGEM | VIDEO | {'zip', 'rar', '7z', 'psd', 'ai', 'indd', 'eps', 'svgz'}, 2 * GB),
}
INLINE = {'pdf'} | VIDEO | IMAGEM            # abrem no navegador; o resto so baixa (evita executar html/svg enviados)


def _pasta() -> str:
    p = os.path.join(current_app.instance_path, 'dev_arquivos')
    os.makedirs(p, exist_ok=True)
    return p


def _ext(nome: str) -> str:
    return nome.rsplit('.', 1)[-1].lower() if '.' in nome else ''


def _arquivo(a: DevArquivo) -> dict:
    return dict(id=a.id, secao=a.secao, titulo=a.titulo, descricao=a.descricao, categoria=a.categoria, referencia=a.referencia, nome=a.nome_original, ext=_ext(a.nome_original), tamanho=a.tamanho or 0,
                enviado_por=_nome(a.enviado_por), enviado_em=a.enviado_em.isoformat(timespec='minutes') if a.enviado_em else None, inline=_ext(a.nome_original) in INLINE,
                pode_excluir=bool(current_user.is_admin or (eh_dev() and a.enviado_por_id == current_user.id)))


@dev_bp.route('/arquivos')
@login_required
def arquivos():
    secao = request.args.get('secao', '')
    if secao not in SECOES:
        abort(400)
    _autorizar(SECOES[secao][0])
    return jsonify([_arquivo(a) for a in DevArquivo.query.filter_by(secao=secao).order_by(DevArquivo.enviado_em.desc()).all()])


@dev_bp.route('/arquivos', methods=['POST'])
@login_required
def arquivo_enviar():
    secao = request.form.get('secao', '')
    if secao not in SECOES:
        return jsonify(erro='Seção inválida.'), 400
    aba, permitidas, maximo = SECOES[secao]
    _autorizar(aba)
    _exigir_dev()
    f = request.files.get('arquivo')
    titulo = (request.form.get('titulo') or '').strip()
    if not f or not f.filename:
        return jsonify(erro='Escolha um arquivo.'), 400
    ext = _ext(f.filename)
    if ext not in permitidas:
        return jsonify(erro=f'Tipo de arquivo não permitido (.{ext}). Permitidos: ' + ', '.join(sorted(permitidas))), 400
    if (request.content_length or 0) > maximo + 5 * MB:
        return jsonify(erro=f'Arquivo maior que o limite de {maximo // MB if maximo < GB else maximo // GB} {"MB" if maximo < GB else "GB"}.'), 413
    nome_seguro = ''.join(c if c.isalnum() or c in '._- ' else '_' for c in os.path.basename(f.filename))[:150]
    rel = os.path.join(secao, f'{uuid.uuid4().hex}_{nome_seguro}')
    destino = os.path.join(_pasta(), rel)
    os.makedirs(os.path.dirname(destino), exist_ok=True)
    f.save(destino)
    tam = os.path.getsize(destino)
    if tam > maximo:
        os.remove(destino)
        return jsonify(erro='Arquivo maior que o limite permitido.'), 413
    a = DevArquivo(secao=secao, titulo=titulo or os.path.splitext(nome_seguro)[0], descricao=(request.form.get('descricao') or '').strip() or None, categoria=(request.form.get('categoria') or '').strip() or None,
                   referencia=(request.form.get('referencia') or '').strip() or None, nome_original=nome_seguro, caminho=rel, tamanho=tam, mimetype=mimetypes.guess_type(nome_seguro)[0], enviado_por_id=current_user.id)
    db.session.add(a)
    db.session.commit()
    return jsonify(_arquivo(a))


def _enviar(aid: int, baixar: bool):
    a = db.session.get(DevArquivo, aid) or abort(404)
    _autorizar(SECOES[a.secao][0])
    caminho = os.path.join(_pasta(), a.caminho)
    if not os.path.isfile(caminho):
        abort(404)
    inline = _ext(a.nome_original) in INLINE and not baixar
    r = send_file(caminho, mimetype=a.mimetype or 'application/octet-stream', as_attachment=not inline, download_name=a.nome_original, conditional=True, max_age=0)   # conditional = suporta Range (video)
    r.headers['X-Content-Type-Options'] = 'nosniff'
    return r


@dev_bp.route('/arquivos/<int:aid>/ver')
@login_required
def arquivo_ver(aid):
    return _enviar(aid, False)


@dev_bp.route('/arquivos/<int:aid>/baixar')
@login_required
def arquivo_baixar(aid):
    return _enviar(aid, True)


@dev_bp.route('/arquivos/<int:aid>', methods=['DELETE'])
@login_required
def arquivo_excluir(aid):
    a = db.session.get(DevArquivo, aid) or abort(404)
    _autorizar(SECOES[a.secao][0])
    if not (current_user.is_admin or (eh_dev() and a.enviado_por_id == current_user.id)):
        abort(403)
    try:
        os.remove(os.path.join(_pasta(), a.caminho))
    except OSError:
        pass
    db.session.delete(a)
    db.session.commit()
    return jsonify(ok=True)
