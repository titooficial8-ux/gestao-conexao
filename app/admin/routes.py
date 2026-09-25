from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user

from app import db
from app.models import User, Sector, Category, Tab, Role
from app.decorators import admin_required

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')


@admin_bp.route('/usuarios')
@login_required
@admin_required
def users():
    all_users = User.query.order_by(User.status.asc(), User.created_at.desc()).all()
    sectors = Sector.query.order_by(Sector.name).all()
    roles = Role.query.order_by(Role.name).all()
    return render_template('admin/users.html', users=all_users, sectors=sectors, roles=roles)


@admin_bp.route('/usuarios/<int:user_id>/atualizar', methods=['POST'])
@login_required
@admin_required
def update_user(user_id):
    user = User.query.get_or_404(user_id)
    user.status = request.form.get('status', user.status)
    user.is_admin = request.form.get('is_admin') == 'on'
    sector_ids = request.form.getlist('sectors')
    user.sectors = Sector.query.filter(Sector.id.in_(sector_ids)).all() if sector_ids else []

    role_id = request.form.get('role_id', '')
    user.role_id = int(role_id) if role_id else None

    nova_senha = request.form.get('nova_senha', '').strip()
    if nova_senha:
        user.set_password(nova_senha)
        user.reset_password_requested = False
        user.reset_password_requested_at = None

    db.session.commit()
    flash('usuario_atualizado', 'success')
    return redirect(url_for('admin.users'))


@admin_bp.route('/usuarios/<int:user_id>/excluir', methods=['POST'])
@login_required
@admin_required
def delete_user(user_id):
    user = User.query.get_or_404(user_id)
    if user.id == current_user.id:
        flash('nao_pode_excluir_a_si_mesmo', 'error')
        return redirect(url_for('admin.users'))
    db.session.delete(user)
    db.session.commit()
    flash('usuario_excluido', 'success')
    return redirect(url_for('admin.users'))


@admin_bp.route('/setores', methods=['GET', 'POST'])
@login_required
@admin_required
def sectors():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        if name and not Sector.query.filter_by(name=name).first():
            db.session.add(Sector(name=name))
            db.session.commit()
            flash('setor_criado', 'success')
        return redirect(url_for('admin.sectors'))

    all_sectors = Sector.query.order_by(Sector.name).all()
    return render_template('admin/sectors.html', sectors=all_sectors)


@admin_bp.route('/setores/<int:sector_id>/excluir', methods=['POST'])
@login_required
@admin_required
def delete_sector(sector_id):
    sector = Sector.query.get_or_404(sector_id)
    db.session.delete(sector)
    db.session.commit()
    flash('setor_excluido', 'success')
    return redirect(url_for('admin.sectors'))


@admin_bp.route('/permissoes', methods=['GET', 'POST'])
@login_required
@admin_required
def permissions():
    categories = Category.query.order_by(Category.order).all()
    sectors = Sector.query.order_by(Sector.name).all()

    if request.method == 'POST':
        for tab in Tab.query.all():
            sector_ids = request.form.getlist(f'tab_{tab.id}')
            tab.sectors = Sector.query.filter(Sector.id.in_(sector_ids)).all() if sector_ids else []
        db.session.commit()
        flash('permissoes_atualizadas', 'success')
        return redirect(url_for('admin.permissions'))

    return render_template('admin/permissions.html', categories=categories, sectors=sectors)
