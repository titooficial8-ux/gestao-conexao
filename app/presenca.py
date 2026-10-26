"""Quem esta online e em qual pagina (em memoria, tempo real).

Todo usuario logado manda um 'ping' a cada ~20 s; so quem tem o cargo
Desenvolvedor consegue ler a lista (tela Usuarios e Acessos)."""
import threading
import time

from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required

presenca_bp = Blueprint('presenca', __name__)

ONLINE_SEG = 60
_estado = {}
_lock = threading.Lock()


def pode_ver_presenca(u=None):
    u = u or current_user
    return bool(getattr(u, 'is_authenticated', False)) and bool(u.role) and u.role.name == 'Desenvolvedor'


@presenca_bp.route('/presenca/ping', methods=['POST'])
@login_required
def ping():
    j = request.get_json(silent=True) or {}
    with _lock:
        _estado[current_user.id] = dict(
            ts=time.time(),
            pagina=(str(j.get('titulo') or '')[:120]).strip(),
            caminho=str(j.get('caminho') or '')[:200],
            visivel=bool(j.get('visivel', True)),
        )
    return '', 204


@presenca_bp.route('/admin/presenca')
@login_required
def lista():
    if not pode_ver_presenca():
        return jsonify(erro='sem permissao'), 403
    agora = time.time()
    with _lock:
        dados = {
            str(uid): dict(online=agora - e['ts'] <= ONLINE_SEG, segundos=int(agora - e['ts']),
                           pagina=e['pagina'], caminho=e['caminho'], visivel=e['visivel'])
            for uid, e in _estado.items()
        }
    return jsonify(dados)
