"""Programacao ao vivo: le a tabela machine_production do PCP Hub (Supabase) e atualiza as Sequencias do Gestao, sem importar arquivo.

Precisa de um usuario do PCP Hub (e-mail e senha) no .env — a leitura respeita as regras de seguranca do proprio PCP Hub (somente leitura):
    PCPHUB_EMAIL=usuario@conexaomalhas.com.br
    PCPHUB_PASSWORD=senha
Opcionais: PCPHUB_URL, PCPHUB_ANON_KEY (a chave publica do PCP Hub; ja vem preenchida), PCPHUB_REGIAO=BR, PCPHUB_INTERVALO_MIN=5.
O Gestao nao apaga as sequencias: atualiza as que ja existem (mantendo a situacao e as pecas pesadas vindas do TOTVS), cria as novas e remove as que sairam do PCP Hub.
"""
import os
import threading
import time
import traceback
from datetime import datetime, date

import requests

URL = os.getenv('PCPHUB_URL', 'https://zygrpemxeyweeuowlhgx.supabase.co').rstrip('/')
CHAVE = os.getenv('PCPHUB_ANON_KEY', 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Inp5Z3JwZW14ZXl3ZWV1b3dsaGd4Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzU5NjcyOTQsImV4cCI6MjA5MTU0MzI5NH0.qAUo4isjHYZA8ons3770ETBH89FoTM5nssqpAn9L9bk')
INTERVALO_MIN = max(1, int(os.getenv('PCPHUB_INTERVALO_MIN', '5')))
REGIAO = os.getenv('PCPHUB_REGIAO', 'BR')
ESTADO = dict(rodando=False, ultima=None, erro=None, n=0, msg='')
_trava = threading.Lock()


def configurado() -> bool:
    return bool(os.getenv('PCPHUB_EMAIL') and os.getenv('PCPHUB_PASSWORD'))


def _login() -> str:
    r = requests.post(f'{URL}/auth/v1/token?grant_type=password', json=dict(email=os.environ['PCPHUB_EMAIL'], password=os.environ['PCPHUB_PASSWORD']), headers=dict(apikey=CHAVE), timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f'Login no PCP Hub recusado (HTTP {r.status_code}): {r.text[:160]}')
    return r.json()['access_token']


def buscar_linhas() -> list:
    """Todas as linhas de machine_production da regiao (paginado de 1000 em 1000)."""
    token = _login()
    cab = dict(apikey=CHAVE, Authorization=f'Bearer {token}')
    linhas, ini = [], 0
    while True:
        r = requests.get(f'{URL}/rest/v1/machine_production', params=dict(select='*', region=f'eq.{REGIAO}', order='machine.asc,sequence_order.asc'),
                         headers=dict(cab, Range=f'{ini}-{ini + 999}', **{'Range-Unit': 'items'}), timeout=60)
        if r.status_code not in (200, 206):
            raise RuntimeError(f'Leitura da programacao falhou (HTTP {r.status_code}): {r.text[:160]}')
        bloco = r.json()
        linhas += bloco
        if len(bloco) < 1000:
            return linhas
        ini += 1000


def _data(v):
    if not v:
        return None
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def _int(v):
    try:
        return int(float(v)) if v not in (None, '') else 0
    except (TypeError, ValueError):
        return 0


def aplicar(linhas: list, agora=None) -> dict:
    """Atualiza as Sequencias do banco com as linhas (mesmo formato do PCP Hub). Precisa estar dentro do app_context."""
    from app import db
    from app.main.routes import resolve_estrutura_from_article
    from app.models import Machine, Sequencia
    agora = agora or datetime.utcnow()
    maquinas = {m.number: m for m in Machine.query.all()}
    atuais = {}
    for s in Sequencia.query.all():
        atuais[(s.machine_id, s.op or f'#{s.sequence_order}')] = s
    from app.models import PesagemResumo
    no_totvs = {r.op for r in PesagemResumo.query.filter(PesagemResumo.encontrada.is_(True), PesagemResumo.quantidade_op > 0)}   # OPs cuja quantidade o TOTVS ja informa
    vistos, criadas, atualizadas = set(), 0, 0
    for r in linhas:
        try:
            numero = int(str(r.get('machine')).strip())
        except (TypeError, ValueError):
            continue
        m = maquinas.get(numero)
        if not m:
            m = Machine(number=numero)
            db.session.add(m)
            db.session.flush()
            maquinas[numero] = m
        chave = (m.id, str(r.get('op') or '') or f'#{r.get("sequence_order")}')
        vistos.add(chave)
        campos = dict(sequence_order=r.get('sequence_order'), artigo=r.get('article'), cliente=r.get('client'), op=r.get('op'), status=r.get('status'), total_pcs=_int(r.get('total_pcs')),
                      qt_solicitada=_int(r.get('requested_quantity')), qt_avulsa=_int(r.get('qt_avulsa')), cod_segmento=r.get('cod_segmento'), dt_cliente=_data(r.get('client_date')),
                      production_month=r.get('production_month'), termino_malharia=_data(r.get('estimated_end_date')), estrutura=resolve_estrutura_from_article(r.get('article')),
                      region=r.get('region') or 'BR', synced_at=agora)
        s = atuais.get(chave)
        if s:
            if str(s.op) in no_totvs:                    # a quantidade (pecas e metros) da OP vem direto do TOTVS, nao do PCP Hub
                campos.pop('total_pcs', None)
                campos.pop('qt_solicitada', None)
            for k, v in campos.items():
                setattr(s, k, v)                      # situacao e pecas_produzidas (calculadas pelo TOTVS) ficam como estao
            atualizadas += 1
        else:
            db.session.add(Sequencia(machine_id=m.id, pecas_produzidas=_int(r.get('quantity_pieces')), **campos))
            criadas += 1
    removidas = 0
    if vistos:                                          # nunca apaga tudo se a leitura veio vazia
        for chave, s in atuais.items():
            if chave not in vistos:
                db.session.delete(s)
                removidas += 1
    db.session.commit()
    return dict(criadas=criadas, atualizadas=atualizadas, removidas=removidas)


def atualizar(app) -> bool:
    if not _trava.acquire(blocking=False):
        return False
    ESTADO.update(rodando=True, erro=None, msg='Lendo a programação do PCP Hub…')
    try:
        linhas = buscar_linhas()
        with app.app_context():
            res = aplicar(linhas)
        ESTADO.update(ultima=datetime.now().isoformat(timespec='seconds'), n=len(linhas), msg=f'{len(linhas)} sequências · {res["criadas"]} novas, {res["removidas"]} removidas')
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        ESTADO.update(erro=str(e)[:300], msg='Falha ao ler o PCP Hub — mantive a programação anterior')
    finally:
        ESTADO['rodando'] = False
        _trava.release()
    return ESTADO['erro'] is None


def iniciar_agendador(app) -> None:
    if not configurado():
        ESTADO['msg'] = 'PCP Hub não configurado (PCPHUB_EMAIL e PCPHUB_PASSWORD no .env)'
        return

    def loop():
        time.sleep(20)
        while True:
            try:
                atualizar(app)
            except Exception:  # noqa: BLE001
                traceback.print_exc()
            time.sleep(INTERVALO_MIN * 60)
    threading.Thread(target=loop, daemon=True, name='pcp-hub-programacao').start()


# ------------------------------------------------------------------ rotas (status e atualizar agora)
from flask import Blueprint, abort, current_app, jsonify  # noqa: E402
from flask_login import current_user, login_required  # noqa: E402

pcphub_bp = Blueprint('pcphub', __name__, url_prefix='/pcp-hub')


def _autorizar():
    from app.models import Tab
    tab = Tab.query.filter_by(slug='programacao').first_or_404()
    if not tab.visible_to(current_user):
        abort(403)


@pcphub_bp.route('/status')
@login_required
def status():
    _autorizar()
    return jsonify(configurado=configurado(), **ESTADO)


@pcphub_bp.route('/atualizar', methods=['POST'])
@login_required
def atualizar_agora():
    _autorizar()
    if not configurado():
        return jsonify(ok=False, erro='PCP Hub não configurado no .env (PCPHUB_EMAIL e PCPHUB_PASSWORD).'), 400
    threading.Thread(target=atualizar, args=(current_app._get_current_object(),), daemon=True, name='pcp-hub-agora').start()
    return jsonify(ok=True)
