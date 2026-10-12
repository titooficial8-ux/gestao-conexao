import os
import uuid
from datetime import timedelta

from flask import (Blueprint, render_template, abort, session, request, redirect,
                   url_for, flash, send_from_directory)
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename

from app import db, BASE_DIR
from app.decorators import admin_required
from app.models import TechTicket

chamados_bp = Blueprint('chamados', __name__, url_prefix='/chamados')

UPLOAD_DIR = os.path.join(BASE_DIR, 'instance', 'uploads', 'chamados')
IMAGE_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp'}
MAX_IMAGE_BYTES = 10 * 1024 * 1024

# Mesmas listas do portal de chamados do PCP Hub (TechDesk.tsx)
CATEGORIES = ['HARDWARE', 'SOFTWARE / SISTEMA', 'REDE / INTERNET', 'IMPRESSORA', 'ACESSO / SENHA',
              'TOTVS', 'PCP PLANNING', 'I.A / AUTOMAÇÃO', 'DESENVOLVIMENTO', 'OUTROS']
PRIORITIES = ['BAIXA', 'MEDIA', 'ALTA', 'CRITICA']
STATUSES = ['ABERTO', 'EM_ANDAMENTO', 'AGUARDANDO', 'RESOLVIDO', 'CANCELADO']
SECTORS = ['Produção/Malharia', 'Estoque/Almoxarifado', 'Desenvolvimento',
           'Planejamento/Comercial', 'Fiscal', 'Recursos Humanos']
DEV_TARGETS = ['Gestão Conexão (antigo PCP Planning)', 'Outros']
DEV_REQUEST_TYPES = ['Atualização', 'Melhoria Contínua', 'Integrações', 'Implementação',
                     'Liberação', 'Report de Bugs']
DEV_SERVICES = [
    {'name': 'Criação de API',
     'desc_pt': 'Uma ponte invisível que permite que diferentes sistemas e aplicativos troquem dados entre si, sem telas ou interação humana direta.',
     'desc_es': 'Un puente invisible que permite que distintos sistemas y aplicaciones intercambien datos entre sí, sin pantallas ni interacción humana directa.'},
    {'name': 'Criação de Aplicativo',
     'desc_pt': 'Um programa completo com interface visual para o usuário final realizar tarefas.',
     'desc_es': 'Un programa completo con interfaz visual para que el usuario final realice tareas.'},
    {'name': 'Desenvolver Site ou Sistema',
     'desc_pt': 'Desenvolver um site ou sistema completo.',
     'desc_es': 'Desarrollar un sitio web o sistema completo.'},
    {'name': 'Criação de Painel',
     'desc_pt': 'Desenvolver um painel de controle, com integrações.',
     'desc_es': 'Desarrollar un panel de control, con integraciones.'},
    {'name': 'Desenvolver Agentes I.A.',
     'desc_pt': 'Programas de software autônomos que usam LLMs para raciocinar, planejar e executar tarefas complexas sozinhos, além de um chatbot comum.',
     'desc_es': 'Programas de software autónomos que usan LLMs para razonar, planificar y ejecutar tareas complejas por sí solos, más allá de un chatbot común.'},
    {'name': 'Atualização de Criações',
     'desc_pt': 'Evoluir, corrigir e aprimorar produtos digitais (sites, SaaS, APIs, agentes) após o lançamento.',
     'desc_es': 'Evolucionar, corregir y mejorar productos digitales (sitios, SaaS, APIs, agentes) después del lanzamiento.'},
    {'name': 'Treinamento I.A.',
     'desc_pt': 'Capacitação prática para usar ferramentas de I.A. (ChatGPT, criar agentes, configurar LLM, fluxos) no dia a dia, mesmo sem saber programar.',
     'desc_es': 'Capacitación práctica para usar herramientas de I.A. (ChatGPT, crear agentes, configurar LLM, flujos) en el día a día, incluso sin saber programar.'},
]
DEV_SERVICE_NAMES = {s['name'] for s in DEV_SERVICES}


# horario local de cada ambiente (as datas sao gravadas em UTC)
FUSO_HORAS = {'BR': -3, 'GT': -6}


@chamados_bp.app_template_filter('hora_local')
def hora_local(dt, region='BR'):
    if not dt:
        return '—'
    return (dt + timedelta(hours=FUSO_HORAS.get(region, -3))).strftime('%d/%m/%y %H:%M')


def _ambiente_atual():
    return session.get('login_pais', 'BR')


def _salvar_imagens(files):
    """Salva as imagens anexadas e devolve os nomes gravados (ignora o que nao for imagem)."""
    nomes = []
    for f in files:
        if not f or not f.filename:
            continue
        ext = f.filename.rsplit('.', 1)[-1].lower() if '.' in f.filename else ''
        if ext not in IMAGE_EXTENSIONS:
            flash('chamado_imagem_invalida', 'error')
            continue
        f.seek(0, os.SEEK_END)
        tamanho = f.tell()
        f.seek(0)
        if tamanho > MAX_IMAGE_BYTES:
            flash('chamado_imagem_grande', 'error')
            continue
        os.makedirs(UPLOAD_DIR, exist_ok=True)
        nome = f'{uuid.uuid4().hex}_{secure_filename(f.filename)}'
        f.save(os.path.join(UPLOAD_DIR, nome))
        nomes.append(nome)
    return nomes


def _suporte_endpoint(region):
    return 'chamados.suporte_guatemala' if region == 'GT' else 'chamados.suporte_brasil'


def _lista_suporte(region):
    tickets = (TechTicket.query.filter_by(region=region)
               .order_by(TechTicket.created_at.desc()).all())
    kpis = {
        'total': len(tickets),
        'abertos': sum(1 for t in tickets if t.status == 'ABERTO'),
        'andamento': sum(1 for t in tickets if t.status in ('EM_ANDAMENTO', 'AGUARDANDO')),
        'resolvidos': sum(1 for t in tickets if t.status == 'RESOLVIDO'),
        'criticos': sum(1 for t in tickets if t.priority == 'CRITICA'
                        and t.status not in ('RESOLVIDO', 'CANCELADO')),
        'desenvolver': sum(1 for t in tickets if t.category == 'DESENVOLVIMENTO'),
    }
    titulo_key = 'chamados_suporte_gt_titulo' if region == 'GT' else 'chamados_suporte_br_titulo'
    return render_template('chamados/suporte.html', tickets=tickets, kpis=kpis, titulo_key=titulo_key)


@chamados_bp.route('/')
@login_required
def home():
    return render_template('chamados/home.html')


@chamados_bp.route('/novo-chamado', methods=['GET', 'POST'])
@login_required
def novo_chamado():
    region = _ambiente_atual()

    if request.method == 'POST':
        tipo = request.form.get('tipo', 'chamado')
        title = request.form.get('title', '').strip()
        description = request.form.get('description', '').strip() or None

        if len(title) < 3:
            flash('chamado_titulo_obrigatorio', 'error')
            return redirect(url_for('chamados.novo_chamado', tipo=tipo))

        ticket = TechTicket(
            region=region,
            user_id=current_user.id,
            requester_name=current_user.name,
            requester_email=current_user.email,
            title=title,
            description=description,
            priority='MEDIA',
            status='ABERTO',
        )

        if tipo == 'desenvolver':
            service = request.form.get('dev_service', '')
            if service not in DEV_SERVICE_NAMES:
                flash('chamado_servico_obrigatorio', 'error')
                return redirect(url_for('chamados.novo_chamado', tipo='desenvolver'))
            target = request.form.get('dev_target', '')
            if service == 'Atualização de Criações':
                if target not in DEV_TARGETS:
                    flash('chamado_criacao_obrigatoria', 'error')
                    return redirect(url_for('chamados.novo_chamado', tipo='desenvolver'))
                if target == 'Outros':
                    target = request.form.get('dev_target_other', '').strip() or 'Outros'
            else:
                target = None
            request_type = request.form.get('dev_request_type', '')
            ticket.category = 'DESENVOLVIMENTO'
            ticket.notify_email = current_user.email
            ticket.dev_service = service
            ticket.dev_request_type = request_type if request_type in DEV_REQUEST_TYPES else DEV_REQUEST_TYPES[0]
            ticket.dev_target = target
            mensagem = 'chamado_dev_enviado'
        else:
            category = request.form.get('category', 'OUTROS')
            sector = request.form.get('sector', '')
            ticket.category = category if category in CATEGORIES else 'OUTROS'
            ticket.sector = sector if sector in SECTORS else SECTORS[0]
            ticket.area = request.form.get('area', '').strip() or None
            ticket.notify_email = request.form.get('notify_email', '').strip().lower() or current_user.email
            mensagem = 'chamado_aberto'

        ticket.images = '|'.join(_salvar_imagens(request.files.getlist('images')))
        db.session.add(ticket)
        db.session.commit()
        flash(mensagem, 'success')
        return redirect(url_for(_suporte_endpoint(region)))

    return render_template(
        'chamados/novo_chamado.html',
        tipo=request.args.get('tipo', ''),
        region=region,
        categories=CATEGORIES,
        sectors=SECTORS,
        dev_services=DEV_SERVICES,
        dev_targets=DEV_TARGETS,
        dev_request_types=DEV_REQUEST_TYPES,
    )


@chamados_bp.route('/anexo/<int:ticket_id>/<path:nome>')
@login_required
def anexo(ticket_id, nome):
    ticket = TechTicket.query.get_or_404(ticket_id)
    if ticket.region != _ambiente_atual() or nome not in ticket.image_list:
        abort(404)
    return send_from_directory(UPLOAD_DIR, nome)


@chamados_bp.route('/suporte-brasil')
@login_required
def suporte_brasil():
    if _ambiente_atual() != 'BR':
        abort(403)
    return _lista_suporte('BR')


@chamados_bp.route('/suporte-guatemala')
@login_required
def suporte_guatemala():
    if _ambiente_atual() != 'GT':
        abort(403)
    return _lista_suporte('GT')


@chamados_bp.route('/dashboard')
@login_required
@admin_required
def dashboard():
    return render_template('chamados/placeholder.html', titulo_key='chamados_dashboard_titulo')


@chamados_bp.route('/auditoria')
@login_required
@admin_required
def auditoria():
    return render_template('chamados/placeholder.html', titulo_key='auditoria_sistemica_titulo')
