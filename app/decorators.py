from functools import wraps

from flask import abort
from flask_login import current_user


def admin_required(f):
    """Libera apenas para o Desenvolvedor (is_admin) — acesso total do sistema."""

    @wraps(f)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated:
            abort(403)
        if not current_user.is_admin:
            abort(403)
        return f(*args, **kwargs)

    return wrapper
