"""Materia-prima necessaria para as OPs da Programacao (consumo pendente de insumos por OP), direto do TOTVS (somente leitura).

Fontes (servicos de producao do TOTVS):
  production-order/v2/pending-material-consumption  -> consumo pendente de materia-prima por OP
  production/v2/material-requirements               -> necessidade de materiais (alternativa)

Os nomes dos campos de cada requisicao sao lidos do swagger do proprio servico (nada e adivinhado); a resposta e
interpretada de forma generica (acha os itens com codigo/descricao de materia-prima e a quantidade pendente/necessaria).
O resultado fica em instance/totvs/empenho_op.json e e atualizado a cada EMPENHO_INTERVALO_MIN minutos (padrao 10).
"""
import json
import os
import re
import threading
import time
import traceback
from datetime import date, datetime, timedelta

from . import api

BASE_URL = api.BASE_URL
EMPRESA = int(os.getenv('PEDIDOS_EMPRESA', '1'))
CICLO = int(os.getenv('EMPENHO_CICLO', '13'))
INTERVALO_MIN = max(2, int(os.getenv('EMPENHO_INTERVALO_MIN', '10')))
ARQUIVO = api.OUT_DIR / 'empenho_op.json'
ENDPOINTS = [
    ('pendente', 'production-order', '/api/totvsmoda/production-order/v2/pending-material-consumption'),
    ('necessidade', 'production', '/api/totvsmoda/production/v2/material-requirements'),
]
ESTADO = dict(rodando=False, ultima=None, erro=None, msg='')
_trava = threading.Lock()
_SW = {}


def configurado() -> bool:
    return bool(os.getenv('TOTVS_CLIENT_ID') and os.getenv('TOTVS_CLIENT_SECRET'))


def ler() -> dict:
    try:
        return json.loads(ARQUIVO.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


# ---------------------------------------------------------------------------
# swagger: requisicao e resposta de cada endpoint
# ---------------------------------------------------------------------------
def _swagger(cli, servico: str) -> dict:
    if servico not in _SW:
        r = cli.get(f'{BASE_URL}/api/totvsmoda/{servico}/v2/swagger/v1/swagger.json', {})
        _SW[servico] = r.json() if r.status_code == 200 else {}
    return _SW[servico]


def _res(sw: dict, no):
    ref = (no or {}).get('$ref')
    return sw.get('components', {}).get('schemas', {}).get(ref.split('/')[-1], {}) if ref else (no or {})


def _props(sw: dict, no, prof: int = 1) -> dict:
    """{campo: descricao curta ou subcampos} de um schema, resolvendo $ref (ate `prof` niveis)."""
    sch = _res(sw, no)
    if sch.get('type') == 'array':
        sch = _res(sw, sch.get('items'))
    out = {}
    for k, v in (sch.get('properties') or {}).items():
        sub = _res(sw, v.get('items') if v.get('type') == 'array' else v)
        if prof > 0 and sub.get('properties'):
            out[k] = _props(sw, v.get('items') if v.get('type') == 'array' else v, prof - 1)
        else:
            out[k] = ((v.get('description') or v.get('type') or '')[:90])
    return out


def _operacao(sw: dict, caminho: str):
    for metodo, op in (sw.get('paths', {}).get(caminho) or {}).items():
        return metodo, op
    return None, None


def esquema(cli, servico: str, caminho: str) -> dict:
    sw = _swagger(cli, servico)
    metodo, op = _operacao(sw, caminho)
    if not op:
        return dict(erro='endpoint nao encontrado no swagger')
    info = dict(metodo=metodo)
    if metodo == 'post':
        corpo = _res(sw, ((op.get('requestBody') or {}).get('content') or {}).get('application/json', {}).get('schema'))
        props = _props(sw, corpo, 1)
        info['requisicao'] = props
    else:
        info['requisicao'] = {p['name']: (p.get('description') or '')[:90] for p in op.get('parameters') or []}
    resp = ((op.get('responses') or {}).get('200') or {}).get('content', {}).get('application/json', {}).get('schema')
    info['resposta'] = _props(sw, resp, 2)
    return info


# ---------------------------------------------------------------------------
# monta a requisicao a partir dos nomes do swagger
# ---------------------------------------------------------------------------
def _preencher(nomes, ops: list, unica: bool) -> dict:
    hoje = date.today()
    d = {}
    for k in nomes:
        kl = k.lower()
        if 'branch' in kl:
            d[k] = [EMPRESA] if kl.endswith('list') or kl.endswith('s') else EMPRESA
        elif 'cycle' in kl and ('list' in kl or kl.endswith('s')):
            d[k] = [CICLO]
        elif 'order' in kl and 'by' not in kl and k != 'order':
            if unica and not (kl.endswith('list') or kl.endswith('s')):
                d[k] = ops[0]
            elif kl.endswith('list') or kl.endswith('s'):
                d[k] = ops
        elif kl.startswith('start') and 'date' in kl:
            d[k] = f'{(hoje - timedelta(days=60)).isoformat()}T00:00:00'
        elif kl.startswith('end') and 'date' in kl:
            d[k] = f'{(hoje + timedelta(days=120)).isoformat()}T23:59:59'
    return d


def montar(cli, servico: str, caminho: str, ops: list, pagina: int = 1):
    """(metodo, url, corpo|params, unica): a requisicao para essas OPs, com os nomes de campo lidos do swagger."""
    sw = _swagger(cli, servico)
    metodo, op = _operacao(sw, caminho)
    url = f'{BASE_URL}{caminho}'
    if metodo == 'post':
        corpo = _res(sw, ((op.get('requestBody') or {}).get('content') or {}).get('application/json', {}).get('schema'))
        props = list((corpo.get('properties') or {}).keys())
        if 'filter' in props:
            fprops = list(_res(sw, corpo['properties']['filter']).get('properties', {}).keys())
            unica = not any(re.search(r'order.*(list|s)$', k, re.I) for k in fprops if 'order' in k.lower() and k.lower() != 'order')
            body = dict(filter=_preencher(fprops, ops, unica))
        else:
            unica = not any(re.search(r'order.*(list|s)$', k, re.I) for k in props if 'order' in k.lower() and k.lower() != 'order')
            body = _preencher(props, ops, unica)
        body.update(page=pagina, pageSize=500)
        return 'post', url, body, unica
    nomes = [p['name'] for p in (op or {}).get('parameters') or []]
    unica = not any(re.search(r'order.*(list)$', k, re.I) for k in nomes)
    params = _preencher(nomes, ops, unica)
    params['Page'] = pagina
    return 'get', url, params, unica


def _enviar(cli, metodo, url, dados):
    return cli.post(url, dados) if metodo == 'post' else cli.get(url, dados)


# ---------------------------------------------------------------------------
# leitura generica da resposta
# ---------------------------------------------------------------------------
_RE_OP = re.compile(r'^(production)?order(code)?$', re.I)
_PRIO = [('pending', 4), ('pendente', 4), ('requirement', 3), ('needed', 3), ('necess', 3), ('consum', 2), ('quantity', 1)]


def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _qtd(item: dict):
    """(valor, nome do campo): a quantidade pendente/necessaria do item."""
    melhor = (None, None, -1)
    for k, v in item.items():
        n = _num(v)
        kl = k.lower()
        if n is None or 'percent' in kl or 'code' in kl or 'sequence' in kl:
            continue
        for chave, peso in _PRIO:
            if chave in kl and peso > melhor[2]:
                melhor = (n, k, peso)
    return melhor[0], melhor[1]


def _achar(obj, op_ctx=None):
    """Percorre a resposta e devolve (op, item) para cada dict que traz codigo de materia-prima."""
    if isinstance(obj, list):
        for x in obj:
            yield from _achar(x, op_ctx)
    elif isinstance(obj, dict):
        for k, v in obj.items():
            if _RE_OP.match(k) and isinstance(v, (int, str)) and str(v).isdigit():
                op_ctx = int(v)
        if any(k in obj for k in ('rawMaterialCode', 'materialProductCode')):
            yield op_ctx, obj
        for v in obj.values():
            if isinstance(v, (list, dict)):
                yield from _achar(v, op_ctx)


def _itens(j):
    if isinstance(j, dict):
        return j.get('items') if isinstance(j.get('items'), list) else [j]
    return j if isinstance(j, list) else []


def buscar_op(cli, ops: list) -> dict:
    """Consumo de materia-prima das OPs: tenta cada endpoint e fica com o primeiro que trouxer itens. Devolve {op: {codigo: [nome, qtd, unidade]}} + metadados."""
    log = []
    for nome, servico, caminho in ENDPOINTS:
        try:
            metodo, url, dados, unica = montar(cli, servico, caminho, ops[:1], 1)
        except Exception as e:  # noqa: BLE001
            log.append(dict(endpoint=nome, erro=f'montar: {e}'[:160]))
            continue
        lotes = [[o] for o in ops] if unica else [ops[i:i + 80] for i in range(0, len(ops), 80)]
        res, campo, n_http_ok = {}, None, 0
        for lote in lotes[:200]:
            pag = 1
            while pag <= 20:
                metodo, url, dados, _ = montar(cli, servico, caminho, lote, pag)
                r = _enviar(cli, metodo, url, dados)
                if r.status_code != 200:
                    log.append(dict(endpoint=nome, http=r.status_code, erro=r.text[:160], corpo={k: (str(v)[:60]) for k, v in dados.items()}))
                    break
                n_http_ok += 1
                j = r.json()
                for op_ctx, it in _achar(j):
                    op = op_ctx or (lote[0] if len(lote) == 1 else None)
                    q, c = _qtd(it)
                    if op is None or q is None:
                        continue
                    campo = campo or c
                    cod = it.get('rawMaterialCode') if it.get('rawMaterialCode') is not None else it.get('materialProductCode')
                    nm = it.get('rawMaterialDescription') or it.get('materialProductName') or ''
                    un = it.get('measuredUnit') or it.get('unit') or ''
                    atual = res.setdefault(op, {}).setdefault(str(cod), [nm, 0.0, un])
                    atual[1] += q
                if not (isinstance(j, dict) and j.get('hasNext')):
                    break
                pag += 1
        log.append(dict(endpoint=nome, http_ok=n_http_ok, ops_com_dados=len(res), campo_quantidade=campo))
        if res:
            return dict(ops=res, endpoint=nome, campo=campo, log=log)
    return dict(ops={}, endpoint=None, campo=None, log=log)


# ---------------------------------------------------------------------------
# atualizacao automatica
# ---------------------------------------------------------------------------
def ops_da_programacao(app) -> list:
    from app.models import Sequencia
    with app.app_context():
        return sorted({int(s.op) for s in Sequencia.query.all() if s.op and str(s.op).isdigit() and (s.status or '') in ('PRODUZINDO', 'EM_SEQUENCIA', 'PCP')})


def atualizar(app) -> bool:
    if not _trava.acquire(blocking=False):
        return False
    ESTADO.update(rodando=True, erro=None, msg='Lendo o consumo de matéria-prima das OPs…')
    try:
        ops = ops_da_programacao(app)
        if not ops:
            raise RuntimeError('Nenhuma OP na Programacao.')
        cli = api.TotvsModaClient()
        cli.autenticar()
        r = buscar_op(cli, ops)
        ARQUIVO.parent.mkdir(parents=True, exist_ok=True)
        tmp = ARQUIVO.with_suffix('.tmp')
        tmp.write_text(json.dumps(dict(versao=1, atualizado=datetime.now().isoformat(timespec='seconds'), endpoint=r['endpoint'], campo_quantidade=r['campo'],
                                       n_ops=len(ops), ops={str(k): v for k, v in r['ops'].items()}, log=r['log']), ensure_ascii=False), encoding='utf-8')
        api.trocar_arquivo(tmp, ARQUIVO)
        ESTADO.update(ultima=datetime.now().isoformat(timespec='seconds'), msg=f"{len(r['ops'])} OPs com consumo de matéria-prima")
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        ESTADO.update(erro=str(e)[:300], msg='Falha ao ler o consumo das OPs')
    finally:
        ESTADO['rodando'] = False
        _trava.release()
    return ESTADO['erro'] is None


def iniciar_agendador(app) -> None:
    if not configurado():
        return

    def loop():
        time.sleep(60)
        while True:
            try:
                atualizar(app)
            except Exception:  # noqa: BLE001
                traceback.print_exc()
            time.sleep(INTERVALO_MIN * 60)
    threading.Thread(target=loop, daemon=True, name='totvs-empenho').start()


# ---------------------------------------------------------------------------
# dados do dashboard: Programacao (OPs em maquina) x consumo de materia-prima x saldo do almoxarifado
# ---------------------------------------------------------------------------
def dashboard() -> dict:
    from app.models import Sequencia
    from . import almoxarifado
    d = ler()
    if not d.get('atualizado'):
        return dict(pronto=False, estado=dict(ESTADO))
    saldos = {str(i['codigo']): i for i in almoxarifado.ler().get('itens', [])}
    seqs = {str(s.op): s for s in Sequencia.query.all() if s.op}
    ops_out, mats = [], {}
    for op, itens in (d.get('ops') or {}).items():
        s = seqs.get(op)
        if not s:
            continue
        lista = sorted(([c, v[0], round(v[1], 1), v[2]] for c, v in itens.items() if v[1] > 0), key=lambda x: -x[2])
        total = round(sum(x[2] for x in lista), 1)
        mes = (s.termino_malharia.strftime('%Y-%m') if s.termino_malharia else None)
        ops_out.append(dict(op=op, maquina=s.machine.number if s.machine else None, artigo=s.artigo, cliente=s.cliente, status=s.status, mes=mes, termino=s.termino_malharia.isoformat() if s.termino_malharia else None,
                            total=total, materias=[dict(codigo=c, nome=(saldos.get(c) or {}).get('nome') or n, kg=k, unidade=u) for c, n, k, u in lista]))
        for c, n, k, u in lista:
            m = mats.setdefault(c, dict(codigo=c, nome=(saldos.get(c) or {}).get('nome') or n, necessario=0.0, ops=0, por_mes={}))
            m['necessario'] += k
            m['ops'] += 1
            if mes:
                m['por_mes'][mes] = m['por_mes'].get(mes, 0) + k
    for c, m in mats.items():
        sd = (saldos.get(c) or {}).get('saldo')
        m['saldo'] = sd
        m['necessario'] = round(m['necessario'], 1)
        m['falta'] = round(max(m['necessario'] - sd, 0), 1) if sd is not None else None
        m['por_mes'] = {k: round(v, 1) for k, v in m['por_mes'].items()}
    return dict(pronto=True, atualizado=d.get('atualizado'), endpoint=d.get('endpoint'), campo=d.get('campo_quantidade'), n_ops_lidas=d.get('n_ops'), ops=sorted(ops_out, key=lambda x: -x['total']),
                materias=sorted(mats.values(), key=lambda x: -x['necessario']), sem_dados=not ops_out, estado=dict(ESTADO), log=d.get('log'))
