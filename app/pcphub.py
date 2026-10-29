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
INTERVALO_MIN = max(1, int(os.getenv('PCPHUB_INTERVALO_MIN', '2')))
REGIAO = os.getenv('PCPHUB_REGIAO', 'BR')
ESTADO = dict(rodando=False, ultima=None, erro=None, n=0, msg='')
_trava = threading.Lock()


def configurado() -> bool:
    return bool(os.getenv('PCPHUB_EMAIL') and os.getenv('PCPHUB_PASSWORD'))


_TOKEN = dict(v=None, ate=0.0)


def _login() -> str:
    """Token do Hub reaproveitado por ~50 min (antes cada leitura fazia um login novo)."""
    if _TOKEN['v'] and time.time() < _TOKEN['ate']:
        return _TOKEN['v']
    r = requests.post(f'{URL}/auth/v1/token?grant_type=password', json=dict(email=os.environ['PCPHUB_EMAIL'], password=os.environ['PCPHUB_PASSWORD']), headers=dict(apikey=CHAVE), timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f'Login no PCP Hub recusado (HTTP {r.status_code}): {r.text[:160]}')
    j = r.json()
    _TOKEN.update(v=j['access_token'], ate=time.time() + min(int(j.get('expires_in') or 3600), 3600) - 300)
    return _TOKEN['v']


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


def buscar_impedimentos() -> list:
    token = _login()
    cab = dict(apikey=CHAVE, Authorization=f'Bearer {token}')
    r = requests.get(f'{URL}/rest/v1/artigo_maquina_impedimentos', params=dict(select='*', region=f'eq.{REGIAO}', order='movido_em.desc'), headers=cab, timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f'Leitura dos impedimentos falhou (HTTP {r.status_code}): {r.text[:160]}')
    return r.json()


def gravar_impedimentos_hub(linhas: list) -> int:
    from app.auditoria import gravar_impedimentos
    return gravar_impedimentos(linhas)


def buscar_dias_sem_producao() -> list:
    """Datas de production_non_working_days do PCP Hub (importacao unica: depois os dias sem producao sao do Gestao)."""
    token = _login()
    r = requests.get(f'{URL}/rest/v1/production_non_working_days', params=dict(select='date,note', region=f'eq.{REGIAO}', order='date.asc'), headers=dict(apikey=CHAVE, Authorization=f'Bearer {token}'), timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f'Leitura dos dias sem producao falhou (HTTP {r.status_code}): {r.text[:160]}')
    return r.json()


def buscar_paises() -> list:
    """client_country_mapping do PCP Hub: {nome_cliente, nome_normalizado, country}."""
    token = _login()
    r = requests.get(f'{URL}/rest/v1/client_country_mapping', params=dict(select='nome_cliente,nome_normalizado,country'),
                     headers=dict(apikey=CHAVE, Authorization=f'Bearer {token}'), timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f'Leitura dos paises falhou (HTTP {r.status_code}): {r.text[:160]}')
    return r.json()


def gravar_paises(linhas: list) -> int:
    from app import db
    from app.models import ClientePais
    from app.paises import normalizar
    if not linhas:
        return 0
    try:
        ClientePais.query.delete()
        vistos = {}
        for x in linhas:
            chave = normalizar(x.get('nome_normalizado') or x.get('nome_cliente'))
            if chave and x.get('country'):
                vistos[chave] = ClientePais(nome_normalizado=chave, nome=(x.get('nome_cliente') or '')[:200], pais=str(x['country'])[:40])
        db.session.bulk_save_objects(list(vistos.values()))
        db.session.commit()
        return len(vistos)
    except Exception:
        db.session.rollback()
        raise


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


_CORRECOES_CLIENTE = {'OROTOBM': 'ORTOBOM', 'ORTOBON': 'ORTOBOM'}


def limpar_cliente(nome):
    """Nome do cliente como vem digitado no PCP Hub, sem lixo de digitacao: tira ordinal no fim ('ORTOBOM 1°' -> 'ORTOBOM') e corrige erros conhecidos ('OROTOBM')."""
    import re
    t = re.sub(r'\s+[0-9]+\s*[°ºª]\s*$', '', str(nome or '')).strip()
    return _CORRECOES_CLIENTE.get(t.upper(), t) or None


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
    vistos, criadas, atualizadas, novas_seqs, prox_ordem, movidas = set(), 0, 0, [], {}, {}
    presentes = set()                                   # (maquina, op) que o Hub trouxe: uma OP que sumiu de uma maquina e apareceu em outra foi MOVIDA
    for r in linhas:
        try:
            mm = maquinas.get(int(str(r.get('machine')).strip()))
        except (TypeError, ValueError):
            mm = None
        if mm and r.get('op'):
            presentes.add((mm.id, str(r.get('op'))))
    por_op = {}
    for (mid, op), x in atuais.items():
        if x.op and (mid, op) not in presentes:
            por_op.setdefault(op, x)
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
        campos = dict(sequence_order=r.get('sequence_order'), artigo=r.get('article'), cliente=limpar_cliente(r.get('client')), op=r.get('op'), status=r.get('status'), total_pcs=_int(r.get('total_pcs')),
                      qt_solicitada=_int(r.get('requested_quantity')), qt_avulsa=_int(r.get('qt_avulsa')), cod_segmento=r.get('cod_segmento'), dt_cliente=_data(r.get('client_date')),
                      production_month=r.get('production_month'), termino_malharia=_data(r.get('estimated_end_date')), estrutura=resolve_estrutura_from_article(r.get('article')),
                      region=r.get('region') or 'BR', synced_at=agora)
        s = atuais.get(chave)
        if not s and r.get('op') and str(r.get('op')) in por_op:      # mudou de maquina no Hub: leva a mesma sequencia (mantem tudo do Gestao) em vez de apagar e recriar
            s = por_op.pop(str(r.get('op')))
            movidas[str(r.get('op'))] = (s, s.machine.number if s.machine else None)
            vistos.add((s.machine_id, str(s.op)))
            if m.id not in prox_ordem:
                prox_ordem[m.id] = max([x.sequence_order or 0 for (mid, _), x in atuais.items() if mid == m.id] or [0])
            prox_ordem[m.id] += 1
            s.machine_id, s.sequence_order, s.aud_ok, s.aud_em = m.id, prox_ordem[m.id], False, None       # entra no fim da fila da nova maquina e precisa ser verificada la
            s.termino_calculado, s.prev_termino_em = False, None
            movidas[str(r.get('op'))] = (movidas[str(r.get('op'))][0], movidas[str(r.get('op'))][1], m.number)
        if s:
            if s.termino_calculado:                      # o termino vem da previsao por voltas/RPM, nao do PCP Hub
                campos.pop('termino_malharia', None)
            if s.dt_cliente_manual:                      # DT.CLIENTE digitada pelo PCP vale mais que a do Hub
                campos.pop('dt_cliente', None)
                campos.pop('production_month', None)
            for k in ('total_pcs', 'qt_solicitada', 'qt_avulsa', 'estrutura', 'sequence_order'):     # a ordem da fila agora e do Gestao (arrastar na maquina);      # quantidade, avulsa e estrutura vem do TOTVS (quantidade da OP, cliente Conexao, classificacao 505); o PCP Hub so cria a linha
                campos.pop(k, None)
            for k, v in campos.items():
                setattr(s, k, v)                      # situacao e pecas_produzidas (calculadas pelo TOTVS) ficam como estao
            atualizadas += 1
        else:
            if m.id not in prox_ordem:
                prox_ordem[m.id] = max([x.sequence_order or 0 for (mid, _), x in atuais.items() if mid == m.id] or [0])
            prox_ordem[m.id] += 1
            campos['sequence_order'] = prox_ordem[m.id]                  # OP nova entra no fim da fila da maquina
            novo = Sequencia(machine_id=m.id, pecas_produzidas=_int(r.get('quantity_pieces')), **campos)
            db.session.add(novo)
            novas_seqs.append((novo, m.number))
            criadas += 1
    removidas = 0
    if vistos:                                          # nunca apaga tudo se a leitura veio vazia
        saiu = {}
        for chave, s in atuais.items():
            if chave not in vistos:
                saiu[str(s.op)] = (s, s.machine.number if s.machine else None)
                db.session.delete(s)
                removidas += 1
        if saiu or movidas:                             # OP que mudou de maquina durante uma auditoria vai para o log (origem -> destino)
            from app.auditoria import registrar_movimentacoes
            novas = {str(n.op): (n, num) for n, num in novas_seqs if n.op}
            for op, (sq, de, para) in movidas.items():
                saiu[op], novas[op] = (sq, de), (sq, para)
            registrar_movimentacoes(saiu, novas, {})
    db.session.commit()
    return dict(criadas=criadas, atualizadas=atualizadas, removidas=removidas, movidas=len(movidas))


def importar_base_inicial() -> None:
    """O Gestao monta a PROPRIA base: impedimentos de artigo, dias sem producao e paises dos clientes sao trazidos do PCP Hub UMA unica vez (so quando a tabela do Gestao ainda esta vazia)
    e dali em diante sao do Gestao (editados aqui). Do Hub continua vindo, sempre, so a programacao (sequencias). O vinculo pedido <-> OP vem do TOTVS."""
    from app import db
    from app.models import ArtigoImpedimento, ClientePais, NonWorkingDay
    if not NonWorkingDay.query.first():
        try:
            for x in buscar_dias_sem_producao():
                if _data(x.get('date')):
                    db.session.add(NonWorkingDay(date=_data(x.get('date')), machine_id=None, comment=x.get('note')))
            db.session.commit()
        except Exception as e:  # noqa: BLE001
            db.session.rollback()
            ESTADO['dias_erro'] = str(e)[:200]
    if not ArtigoImpedimento.query.first():
        try:
            ESTADO['impedimentos'] = gravar_impedimentos_hub(buscar_impedimentos())
        except Exception as e:  # noqa: BLE001
            db.session.rollback()
            ESTADO['impedimentos_erro'] = str(e)[:200]
    if not ClientePais.query.first():
        try:
            ESTADO['paises'] = gravar_paises(buscar_paises())
        except Exception as e:  # noqa: BLE001
            db.session.rollback()
            ESTADO['paises_erro'] = str(e)[:200]
    from app.models import HubArquivo
    if not HubArquivo.query.first():            # historicos do Hub: copia integral, uma unica vez (depois, so pela tela de conferencia)
        try:
            from app import hubhistorico
            ESTADO['historicos'] = hubhistorico.importar()
        except Exception as e:  # noqa: BLE001
            db.session.rollback()
            ESTADO['historicos_erro'] = str(e)[:200]


def atualizar(app) -> bool:
    if not _trava.acquire(blocking=False):
        return False
    ESTADO.update(rodando=True, erro=None, msg='Lendo a programação do PCP Hub…')
    try:
        linhas = buscar_linhas()
        with app.app_context():
            res = aplicar(linhas)
        ESTADO.update(ultima=datetime.now().isoformat(timespec='seconds'), n=len(linhas), msg=f'{len(linhas)} sequências · {res["criadas"]} novas, {res["removidas"]} removidas')
        if not ESTADO.get('base_inicial_ok'):                      # importacoes de uma vez so (dias sem producao, impedimentos, paises, historicos) em segundo plano: nao seguram a programacao
            ESTADO['base_inicial_ok'] = True

            def base():
                try:
                    with app.app_context():
                        importar_base_inicial()
                except Exception:  # noqa: BLE001
                    traceback.print_exc()
            threading.Thread(target=base, daemon=True, name='pcp-hub-base-inicial').start()
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
        time.sleep(3)
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


@pcphub_bp.route('/historicos')
@login_required
def historicos():
    """So administrador: importa (de novo, sem duplicar) todos os historicos do PCP Hub e mostra a conferencia Hub x Gestao, tabela a tabela."""
    if not current_user.is_admin:
        abort(403)
    if not configurado():
        return jsonify(ok=False, erro='PCP Hub não configurado no .env.'), 400
    from app import hubhistorico
    res = hubhistorico.importar()
    return jsonify(completo=hubhistorico.conferir(res), tabelas=res)
