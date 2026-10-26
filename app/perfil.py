"""Meu perfil: foto, capa, setor/cargo e status online do proprio usuario.

As imagens sao reenviadas pelo Pillow (so entram JPG/PNG/WEBP validos, recortadas e reduzidas)
e ficam em instance/perfis/, fora do GitHub."""
import io
from pathlib import Path

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, send_file, url_for
from flask_login import current_user, login_required
from PIL import Image, ImageOps, UnidentifiedImageError

from app import db
from app.presenca import HORAS_PARA_SELO

perfil_bp = Blueprint('perfil', __name__)

MAX_UPLOAD = 8 * 1024 * 1024
TAMANHOS = {'foto': (400, 400), 'capa': (1600, 500)}


def _pasta() -> Path:
    p = Path(current_app.instance_path) / 'perfis'
    p.mkdir(parents=True, exist_ok=True)
    return p


def _arquivo(user_id: int, tipo: str) -> Path:
    return _pasta() / f'{user_id}_{tipo}.jpg'


@perfil_bp.route('/perfil')
@login_required
def meu_perfil():
    horas = (current_user.segundos_acesso or 0) / 3600
    return render_template('perfil/meu_perfil.html', horas=horas, meta=HORAS_PARA_SELO,
                           pct=min(100, round(horas / HORAS_PARA_SELO * 100)))


@perfil_bp.route('/perfil/imagem/<int:user_id>/<tipo>')
@login_required
def imagem(user_id, tipo):
    if tipo not in TAMANHOS:
        abort(404)
    f = _arquivo(user_id, tipo)
    if not f.exists():
        abort(404)
    resp = send_file(f, mimetype='image/jpeg')
    resp.headers['Cache-Control'] = 'private, max-age=86400'
    return resp


@perfil_bp.route('/perfil/enviar/<tipo>', methods=['POST'])
@login_required
def enviar(tipo):
    if tipo not in TAMANHOS:
        abort(404)
    arq = request.files.get('arquivo')
    if not arq or not arq.filename:
        flash('Escolha uma imagem.', 'error')
        return redirect(url_for('perfil.meu_perfil'))
    dados = arq.read(MAX_UPLOAD + 1)
    if len(dados) > MAX_UPLOAD:
        flash('Imagem muito grande (máximo 8 MB).', 'error')
        return redirect(url_for('perfil.meu_perfil'))
    try:
        img = Image.open(io.BytesIO(dados))
        img = ImageOps.exif_transpose(img)
        img = ImageOps.fit(img.convert('RGB'), TAMANHOS[tipo], Image.LANCZOS)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        flash('Arquivo inválido. Envie uma imagem JPG, PNG ou WEBP.', 'error')
        return redirect(url_for('perfil.meu_perfil'))
    img.save(_arquivo(current_user.id, tipo), 'JPEG', quality=88, optimize=True)
    setattr(current_user, f'{tipo}_versao', (getattr(current_user, f'{tipo}_versao') or 0) + 1)
    db.session.commit()
    flash('Foto atualizada.' if tipo == 'foto' else 'Capa atualizada.', 'success')
    return redirect(url_for('perfil.meu_perfil'))


@perfil_bp.route('/perfil/remover/<tipo>', methods=['POST'])
@login_required
def remover(tipo):
    if tipo not in TAMANHOS:
        abort(404)
    f = _arquivo(current_user.id, tipo)
    if f.exists():
        f.unlink()
    setattr(current_user, f'{tipo}_versao', 0)
    db.session.commit()
    return redirect(url_for('perfil.meu_perfil'))
