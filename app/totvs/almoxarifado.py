"""Almoxarifado: relacao de MATERIA-PRIMA (so MP) com saldo no local 34, direto da API do TOTVS (somente leitura).

  product/balances/search -> produtos com saldo no estoque 34 (ALMOX_ESTOQUE no .env, padrao 34)
  product/products/search -> dados do produto (isRawMaterial, unidade, nome); so fica quem e materia-prima

Atualiza sozinho: saldo a cada ALMOX_INTERVALO_MIN minutos (padrao 5) e kardex a cada ALMOX_KARDEX_MIN (padrao 15) e guarda em instance/totvs/almoxarifado.json.
"""
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
import time
import traceback
from datetime import datetime

from . import api

BASE_URL = api.BASE_URL
BALANCES_URL = f'{BASE_URL}/api/totvsmoda/product/v2/balances/search'
PRODUCTS_URL = f'{BASE_URL}/api/totvsmoda/product/v2/products/search'
EMPRESA = int(os.getenv('PEDIDOS_EMPRESA', '1'))
ESTOQUE_FIXO = os.getenv('ALMOX_ESTOQUE', '').strip()                # opcional: codigo do estoque; sem ele, acha pelo NOME (ALMOX_NOME, padrao ALMOXARIFADO)
NOME_ESTOQUE = os.getenv('ALMOX_NOME', 'ALMOXARIFADO').strip().upper()
INTERVALO_SEG = max(20, int(os.getenv('ALMOX_INTERVALO_SEG', '60')))      # saldo: a cada 1 min
KARDEX_URL = f'{BASE_URL}/api/totvsmoda/product/v2/kardex-movement'
TIPO_SALDO_FIXO = os.getenv('ALMOX_TIPO_SALDO', '').strip()           # "tipo de saldo" do kardex (PRDFL017); padrao = o proprio codigo do estoque
OPERACOES_SAIDA = {x.strip() for x in os.getenv('ALMOX_OPERACOES_SAIDA', '2011').split(',') if x.strip()}      # consumo = saida do almoxarifado para a PRODUCAO (op. 2011: transferencia almox para prod)
KARDEX_DIAS = 179                                                  # a API limita a janela a 180 dias
KARDEX_INTERVALO_MIN = max(2, int(os.getenv('ALMOX_KARDEX_MIN', '5')))
TRANSF_INTERVALO_SEG = max(20, int(os.getenv('ALMOX_TRANSF_SEG', '60')))        # transferencias para a producao: a cada 1 min (tempo real)
ARQUIVO = api.OUT_DIR / 'almoxarifado.json'
ARQ_KARDEX = api.OUT_DIR / 'almoxarifado_kardex.json'
ARQ_TRANSF = api.OUT_DIR / 'almoxarifado_transferencias.json'
ARQ_ACERTOS = api.OUT_DIR / 'almoxarifado_acertos.json'
ARQ_OPERADORES = api.OUT_DIR / 'almoxarifado_operadores.json'
INVOICES_URL = api.INVOICES_URL
NOMES_OPERADORES = {k.strip(): v.strip() for k, _, v in (x.partition('=') for x in os.getenv('ALMOX_OPERADORES', '').split(';') if '=' in x)}      # "103=MARCOS;104=JULIA": codigo do operador (FISFL031) -> nome
OPS_ACERTO = {'entrada': int(os.getenv('ALMOX_OP_ACERTO_ENTRADA', '2040')), 'saida': int(os.getenv('ALMOX_OP_ACERTO_SAIDA', '2041'))}      # BALFM015: 2040 ajuste de estoque fisico (entrada) / 2041 (saida)
ACUR_DIAS = int(os.getenv('ALMOX_ACUR_DIAS', '180'))
TRX_URL = f'{BASE_URL}/api/totvsmoda/general/v2/transactions/search'
VERSAO = 1
ESTADO = dict(rodando=False, ultima=None, erro=None, msg='')
_trava = threading.Lock()


def configurado() -> bool:
    return bool(os.getenv('TOTVS_CLIENT_ID') and os.getenv('TOTVS_CLIENT_SECRET'))


def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0.0


def _paginar(cli, url, corpo, tam=1000):
    pag, itens = 1, []
    while True:
        r = cli.post(url, dict(corpo, pageSize=tam, page=pag))
        if r.status_code != 200:
            raise RuntimeError(f'{url.split("/v2/")[1]}: HTTP {r.status_code} {r.text[:200]}')
        j = r.json()
        itens += j.get('items') or []
        if not j.get('hasNext') or pag >= 100:
            return itens
        pag += 1


UNIDADE = os.getenv('ALMOX_UNIDADE', 'KG').strip().upper()                 # so itens nesta unidade (KG); vazio = todas
CLASSIF_MP = os.getenv('ALMOX_CLASSIFICACAO', 'MP').strip().upper()      # nome/codigo da classificacao de produto que marca materia-prima
TIPO_CLASSIF = os.getenv('ALMOX_TIPO_CLASSIFICACAO', '512').strip()       # codigo do TIPO de classificacao "MP" no TOTVS (512 = MP: materia prima de terceiro ou proprio)


def _eh_mp_classif(p: dict) -> bool:
    """Materia-prima = o produto tem uma classificacao do tipo MP (typeCode 512, ou tipo chamado "MP")."""
    for c in p.get('classifications') or []:
        if (TIPO_CLASSIF.isdigit() and c.get('typeCode') == int(TIPO_CLASSIF)) or str(c.get('typeName') or '').strip().upper() == CLASSIF_MP:
            return True
    return False


def _classif(p: dict, tipo: int):
    for c in p.get('classifications') or []:
        if c.get('typeCode') == tipo:
            return (c.get('name') or c.get('code') or '').strip() or None
    return None


def _fornecedor(p: dict):
    """Nome do fornecedor do produto (primeiro da lista); None se o cadastro nao tiver."""
    for f in p.get('suppliers') or []:
        if not isinstance(f, dict):
            continue
        for k in ('supplierName', 'fantasyName', 'name', 'supplierFantasyName', 'description', 'companyName'):
            if f.get(k):
                return str(f[k]).strip()[:50]
        if f.get('supplierCode') or f.get('code'):
            return 'Fornecedor ' + str(f.get('supplierCode') or f.get('code'))
    return None


def _amostra_classif(produtos: dict) -> list:
    return [dict(produto=c, nome=p.get('productName'), isRawMaterial=p.get('isRawMaterial'), classificacoes=[dict(tipo=x.get('typeCode'), tipo_nome=x.get('typeName'), codigo=x.get('code'), nome=x.get('name')) for x in (p.get('classifications') or [])])
            for c, p in list(produtos.items())[:12]]


def _tipos_classif(cli):
    try:
        r = cli.get(f'{BASE_URL}/api/totvsmoda/product/v2/classificationType', {})
        return r.json() if r.status_code == 200 else f'HTTP {r.status_code}'
    except Exception as e:  # noqa: BLE001
        return str(e)[:120]


_RES = {}                                                          # {'codigo', 'nome', 'estoques': {codigo: descricao}}


def descobrir_estoques(cli) -> dict:
    """Estoques que tem saldo ({codigo: descricao}). A API exige o codigo do estoque no filtro, entao testa os codigos 1..ALMOX_VARRER (padrao 150), 1 produto cada."""
    limite = int(os.getenv('ALMOX_VARRER', '150'))

    def um(cod):
        r = cli.post(BALANCES_URL, dict(filter=dict(hasStock=True, branchStockCode=EMPRESA, stockCode=cod), option=dict(balances=[dict(branchCode=EMPRESA, stockCodeList=[cod])]), page=1, pageSize=1))
        if r.status_code != 200:
            return cod, None
        for x in r.json().get('items') or []:
            for b in x.get('balances') or []:
                if b.get('stockCode') == cod:
                    return cod, str(b.get('stockDescription') or '').strip()
        return cod, None

    achados = {}
    with ThreadPoolExecutor(max_workers=4) as ex:
        for cod, desc in ex.map(um, range(1, limite + 1)):
            if desc is not None:
                achados[cod] = desc
    if not achados:
        raise RuntimeError('Nenhum estoque com saldo foi encontrado nos codigos 1 a %d (veja ALMOX_VARRER / ALMOX_ESTOQUE no .env).' % limite)
    return achados


def resolver_estoque(cli) -> tuple:
    """(codigo, nome) do estoque do almoxarifado: ALMOX_ESTOQUE do .env, ou o estoque cujo nome traz ALMOX_NOME."""
    if ESTOQUE_FIXO.isdigit():
        _RES.update(codigo=int(ESTOQUE_FIXO), nome=_RES.get('nome') or f'estoque {ESTOQUE_FIXO}')
        return _RES['codigo'], _RES['nome']
    if _RES.get('codigo') is not None:
        return _RES['codigo'], _RES['nome']
    est = descobrir_estoques(cli)
    _RES['estoques'] = est
    cand = [(c, d) for c, d in est.items() if NOME_ESTOQUE in d.upper()]
    if not cand:
        raise RuntimeError(f'Nao achei um estoque com "{NOME_ESTOQUE}" no nome. Estoques que o TOTVS devolveu: ' + '; '.join(f'{c}={d}' for c, d in sorted(est.items())[:30]))
    cand.sort(key=lambda cd: (cd[1].upper() != NOME_ESTOQUE, len(cd[1])))      # nome exato primeiro
    _RES.update(codigo=cand[0][0], nome=cand[0][1])
    return _RES['codigo'], _RES['nome']


def estoque_atual() -> int:
    return _RES.get('codigo') if _RES.get('codigo') is not None else (int(ESTOQUE_FIXO) if ESTOQUE_FIXO.isdigit() else None)


_PROD, _PROD_TS = {}, [0.0]                                         # cache do cadastro dos produtos
PRODUTOS_CACHE_SEG = int(os.getenv('ALMOX_CADASTRO_MIN', '30')) * 60
_AUTO = {}                                                          # {'tipo_saldo', 'so_data'}: o que funcionou na sondagem do kardex


def tipo_saldo() -> int:
    if TIPO_SALDO_FIXO.isdigit():
        return int(TIPO_SALDO_FIXO)
    return _AUTO.get('tipo_saldo') if _AUTO.get('tipo_saldo') is not None else (estoque_atual() or 0)


def _datas(ini: date, fim: date) -> tuple:
    if _AUTO.get('so_data'):
        return ini.isoformat(), fim.isoformat()
    return f'{ini.isoformat()}T00:00:00', f'{fim.isoformat()}T23:59:59'


def _sondar_kardex(cli, codigos_por_saldo: list, hoje: date) -> None:
    """O kardex responde 'MovementDataNotFound' quando os parametros nao casam. Testa tipo de saldo e formato de data nos produtos de maior saldo e guarda o que funcionar."""
    if TIPO_SALDO_FIXO.isdigit() and _AUTO.get('so_data') is not None:
        return
    base = estoque_atual() or 0
    tipos = [int(TIPO_SALDO_FIXO)] if TIPO_SALDO_FIXO.isdigit() else [base] + [t for t in (1, 0, 2, 3) if t != base]
    ini = hoje - timedelta(days=90)
    for t in tipos:
        for so_data in (False, True):
            for cod in codigos_por_saldo[:6]:
                a, b = (ini.isoformat(), hoje.isoformat()) if so_data else (f'{ini.isoformat()}T00:00:00', f'{hoje.isoformat()}T23:59:59')
                r = cli.get(KARDEX_URL, dict(ProductCode=cod, BranchCode=EMPRESA, StartDate=a, EndDate=b, BalanceType=t))
                if r.status_code == 200 and ((r.json() or [{}])[0] if isinstance(r.json(), list) and r.json() else r.json() or {}).get('movements'):
                    _AUTO.update(tipo_saldo=t, so_data=so_data)
                    print(f'[almoxarifado] kardex: tipo de saldo {t}, {"so data" if so_data else "data e hora"} (produto {cod})', flush=True)
                    return
    print('[almoxarifado] kardex: nenhuma combinacao de parametros trouxe movimentos na sondagem', flush=True)


def ler() -> dict:
    try:
        return json.loads(ARQUIVO.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def _gravar(d: dict) -> None:
    ARQUIVO.parent.mkdir(parents=True, exist_ok=True)
    tmp = ARQUIVO.with_suffix('.tmp')
    tmp.write_text(json.dumps(d, ensure_ascii=False), encoding='utf-8')
    api.trocar_arquivo(tmp, ARQUIVO)


def buscar(cli=None, amostra: bool = False) -> dict:
    """Consulta o TOTVS. amostra=True: diagnostico (devolve exemplos crus das duas consultas, sem gravar)."""
    if cli is None:
        cli = api.TotvsModaClient()
        cli.autenticar()
    ESTOQUE, NOME = resolver_estoque(cli)
    opcao = dict(balances=[dict(branchCode=EMPRESA, stockCodeList=[ESTOQUE])])
    saldos = _paginar(cli, BALANCES_URL, dict(filter=dict(hasStock=True, branchStockCode=EMPRESA, stockCode=ESTOQUE), option=opcao), tam=50 if amostra else 1000) if not amostra else \
        cli.post(BALANCES_URL, dict(filter=dict(hasStock=True, branchStockCode=EMPRESA, stockCode=ESTOQUE), option=opcao, page=1, pageSize=5)).json().get('items') or []
    por_prod = {}
    for x in saldos:
        for b in x.get('balances') or []:
            if b.get('stockCode') == ESTOQUE:
                por_prod[x.get('productCode')] = dict(nome=x.get('productName'), saldo=_num(b.get('stock')), disponivel=_num(b.get('stock')) - _num(b.get('salesOrder')),
                                                       entradas=_num(b.get('inputTransaction')), saidas=_num(b.get('outputTransaction')))
    codigos = sorted(c for c in por_prod if c is not None)
    if time.time() - _PROD_TS[0] > PRODUTOS_CACHE_SEG:                  # cadastro (MP, unidade, classificacao) muda pouco: relê a cada 30 min
        _PROD.clear()
        _PROD_TS[0] = time.time()
    faltam = [c for c in codigos if c not in _PROD]
    for i in range(0, len(faltam), 200):
        for p in _paginar(cli, PRODUCTS_URL, dict(filter=dict(productCodeList=faltam[i:i + 200]), option=dict(branchInfoCode=EMPRESA), expand='classifications,suppliers'), tam=500):
            _PROD[p.get('productCode')] = p
    produtos = {c: _PROD[c] for c in codigos if c in _PROD}
    if amostra:
        return dict(estoque=ESTOQUE, estoque_nome=NOME, classificacao_mp=CLASSIF_MP, classificacoes_amostra=_amostra_classif(produtos), tipos_de_classificacao=_tipos_classif(cli), estoques_no_totvs={str(c): d for c, d in sorted((_RES.get('estoques') or descobrir_estoques(cli)).items())}, saldos_amostra=saldos[:3], fornecedores_amostra=[p.get('suppliers') for p in produtos.values() if p.get('suppliers')][:3], produtos_amostra=list(produtos.values())[:3], n_saldos=len(saldos))
    # materia-prima = "Tipo de produto M.P." do cadastro (o mesmo filtro do PRDFC014/PRDFF005) OU a classificacao MP (tipo 512)
    por_tipo = {c for c, p in produtos.items() if p.get('isRawMaterial')}
    por_classif = {c for c, p in produtos.items() if _eh_mp_classif(p)}
    eh_mp = por_tipo | por_classif
    em_unidade = {c for c, p in produtos.items() if not UNIDADE or str(p.get('measuredUnit') or '').strip().upper() == UNIDADE}
    sem_mp = not (eh_mp & em_unidade)                                  # nenhuma MP na unidade: mostra tudo que esta na unidade (e avisa)
    itens = []
    for cod, v in por_prod.items():
        p = produtos.get(cod) or {}
        if cod not in em_unidade or (cod not in eh_mp and not sem_mp) or p.get('isActive') is False:      # so KG, so MP, so ativos (como o PRDFC014)
            continue
        itens.append(dict(codigo=cod, nome=(p.get('productName') or v['nome'] or '')[:120], unidade=p.get('measuredUnit'), saldo=round(v['saldo'], 3),
                          disponivel=round(v['disponivel'], 3), entradas=round(v['entradas'], 3), saidas=round(v['saidas'], 3),
                          origem=_classif(p, 512), tipo_mp=_classif(p, 501), fornecedor=_fornecedor(p), minimo=_num(p.get('minimumStockAmount')), maximo=_num(p.get('maximumStockAmount')), ideal=_num(p.get('idealStockAmount')), bloqueado=bool(p.get('isBlocked'))))
    itens.sort(key=lambda x: x['nome'])
    return dict(versao=VERSAO, estoque=ESTOQUE, estoque_nome=NOME, atualizado=datetime.now().isoformat(timespec='seconds'), itens=itens,
                n_com_saldo=len(por_prod), n_materia_prima=len(itens), n_tipo_mp=len(por_tipo), n_classif_mp=len(por_classif), n_em_unidade=len(em_unidade & set(por_prod)), filtro_mp=(('unidade ' + UNIDADE + ' · ') if UNIDADE else '') + ('so itens da unidade (nenhum marcado como MP)' if sem_mp else 'tipo M.P. do cadastro ou classificacao MP'))


def atualizar() -> bool:
    if not _trava.acquire(blocking=False):
        return False
    ESTADO.update(rodando=True, erro=None, msg='Consultando o almoxarifado no TOTVS...')
    try:
        d = buscar()
        _gravar(d)
        ESTADO.update(ultima=d['atualizado'], msg=f"{d['n_materia_prima']} materias-primas em {d['estoque_nome']}")
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        ESTADO.update(erro=str(e)[:300], msg='Falha ao consultar - mantive os dados anteriores')
    finally:
        ESTADO['rodando'] = False
        _trava.release()
    return ESTADO['erro'] is None


def iniciar_agendador(app) -> None:
    if not configurado():
        return

    def loop():
        time.sleep(10)
        while True:
            try:
                atualizar()
            except Exception:  # noqa: BLE001
                traceback.print_exc()
            time.sleep(INTERVALO_SEG)
    threading.Thread(target=loop, daemon=True, name='totvs-almoxarifado').start()


# ---------------------------------------------------------------------------
# Kardex por produto -> giro, cobertura, curva ABC, tendencia mensal
# ---------------------------------------------------------------------------
_trava_k = threading.Lock()
ESTADO_K = dict(rodando=False, ultima=None, erro=None, msg='', feitos=0, total=0)


def ler_kardex() -> dict:
    try:
        return json.loads(ARQ_KARDEX.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


_VISTAS = {}                                                         # (operacao, nome, historico) das saidas vistas no ultimo kardex: mostra o que a API realmente manda


def _saida_producao(m: dict) -> bool:
    """Saida do almoxarifado para a PRODUCAO: operacao 2011 (transferencia almox para prod) ou, se o kardex nao trouxer a operacao, o historico de transferencia entre saldos."""
    cod, nome, hist = str(m.get('operationCode') or '').strip(), str(m.get('operationName') or '').upper(), str(m.get('historyName') or '').upper()
    if not OPERACOES_SAIDA or cod in OPERACOES_SAIDA or 'ALMOX PARA PROD' in nome:
        return True
    return not cod and not nome and 'TRANSFERENCIA ENTRE SALDOS' in hist


def _resumir(resp: dict, hoje: date) -> dict:
    """Agrega as movimentacoes de um produto: por mes (entradas, saidas, n), saidas 30/60/90 dias, ultima saida."""
    meses, out30, out60, out90, in90, n30, n90, ult_saida, ult_mov = {}, 0.0, 0.0, 0.0, 0.0, 0, 0, None, None
    for m in resp.get('movements') or []:
        d = str(m.get('movementDate') or '')[:10]
        if not d:
            continue
        ent, sai = abs(_num(m.get('inQuantity'))), abs(_num(m.get('outQuantity')))
        if sai:
            _VISTAS[(str(m.get('operationCode')), str(m.get('operationName') or '')[:40], str(m.get('historyName') or '')[:40])] = _VISTAS.get((str(m.get('operationCode')), str(m.get('operationName') or '')[:40], str(m.get('historyName') or '')[:40]), 0) + 1
            if not _saida_producao(m):
                sai = 0.0                                              # so conta a saida do almoxarifado para a producao
        mm = meses.setdefault(d[:7], [0.0, 0.0, 0])
        mm[0] += ent
        mm[1] += sai
        mm[2] += 1
        ult_mov = max(ult_mov or d, d)
        dias = (hoje - date.fromisoformat(d)).days
        if sai > 0:
            ult_saida = max(ult_saida or d, d)
            if dias < 30:
                out30 += sai
            if dias < 60:
                out60 += sai
            if dias < 90:
                out90 += sai
        if dias < 90:
            in90 += ent
        if dias < 30:
            n30 += 1
        if dias < 90:
            n90 += 1
    return dict(meses={k: [round(v[0], 3), round(v[1], 3), v[2]] for k, v in meses.items()}, out30=round(out30, 3), out60=round(out60, 3), out90=round(out90, 3), in90=round(in90, 3),
                n30=n30, n90=n90, ultima_saida=ult_saida, ultima_mov=ult_mov, saldo_anterior=_num(resp.get('previousBalance')))


def atualizar_kardex() -> bool:
    """Kardex (180 dias) de cada materia-prima com saldo no local; so leitura. Roda em ate 3 consultas ao mesmo tempo."""
    if not _trava_k.acquire(blocking=False):
        return False
    base = ler()
    codigos = [i['codigo'] for i in base.get('itens', [])]
    ESTADO_K.update(rodando=True, erro=None, msg='Lendo o kardex das materias-primas...', feitos=0, total=len(codigos))
    try:
        if not codigos:
            raise RuntimeError('Ainda nao ha lista de materias-primas (aguarde a consulta do almoxarifado).')
        cli = api.TotvsModaClient()
        cli.autenticar()
        hoje = date.today()
        ini = (hoje - timedelta(days=KARDEX_DIAS)).isoformat()
        produtos, erros, sem_mov = {}, [], 0
        _VISTAS.clear()
        grandes = [i['codigo'] for i in sorted(base.get('itens', []), key=lambda x: -x['saldo'])]
        _sondar_kardex(cli, grandes, hoje)

        def um(cod):
            a_, b_ = _datas(hoje - timedelta(days=KARDEX_DIAS), hoje)
            r = cli.get(KARDEX_URL, dict(ProductCode=cod, BranchCode=EMPRESA, StartDate=a_, EndDate=b_, BalanceType=tipo_saldo()))
            if r.status_code == 400 and 'MovementDataNotFound' in r.text:      # sem movimento nem saldo no periodo: nao e erro
                return cod, dict(_resumir({}, hoje), sem_mov=True)
            if r.status_code != 200:
                raise RuntimeError(f'kardex {cod}: HTTP {r.status_code} {r.text[:120]}')
            j = r.json()
            return cod, _resumir(j[0] if isinstance(j, list) and j else (j if isinstance(j, dict) else {}), hoje)

        with ThreadPoolExecutor(max_workers=3) as ex:
            for fut in [ex.submit(um, c) for c in codigos]:
                try:
                    cod, res = fut.result()
                    produtos[str(cod)] = res
                    if res.get('sem_mov'):
                        sem_mov += 1
                except Exception as e:  # noqa: BLE001
                    erros.append(str(e)[:160])
                ESTADO_K['feitos'] += 1
        if not produtos:
            raise RuntimeError('Nenhum kardex lido: ' + (erros[0] if erros else 'sem resposta'))
        ARQ_KARDEX.parent.mkdir(parents=True, exist_ok=True)
        tmp = ARQ_KARDEX.with_suffix('.tmp')
        tmp.write_text(json.dumps(dict(versao=VERSAO, atualizado=datetime.now().isoformat(timespec='seconds'), dias=KARDEX_DIAS + 1, produtos=produtos, erros=len(erros), sem_mov=sem_mov, amostra_erro=erros[:2], ops_saida=[dict(operacao=k[0], nome=k[1], historico=k[2], n=v) for k, v in sorted(_VISTAS.items(), key=lambda kv: -kv[1])[:8]]), ensure_ascii=False), encoding='utf-8')
        api.trocar_arquivo(tmp, ARQ_KARDEX)
        ESTADO_K.update(ultima=datetime.now().isoformat(timespec='seconds'), msg=f'{len(produtos)} kardex lidos' + (f' ({len(erros)} com erro)' if erros else ''))
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        ESTADO_K.update(erro=str(e)[:300], msg='Falha ao ler o kardex - mantive os dados anteriores')
    finally:
        ESTADO_K['rodando'] = False
        _trava_k.release()
    return ESTADO_K['erro'] is None


def indicadores() -> dict:
    """Indicadores para diretoria/CEO a partir do saldo atual e do kardex (180 dias)."""
    base, k = ler(), ler_kardex()
    prod = k.get('produtos') or {}
    itens = []
    for i in base.get('itens', []):
        r = prod.get(str(i['codigo']))
        if not r:
            continue
        saldo, out30, out90 = i['saldo'], r['out30'], r['out90']
        diario = out90 / 90.0 if out90 else 0.0
        itens.append(dict(codigo=i['codigo'], nome=i['nome'], unidade=i.get('unidade'), saldo=saldo, out30=out30, out60=r['out60'], out90=out90, in90=r['in90'], n90=r['n90'],
                          diario=round(diario, 3), cobertura=round(saldo / diario, 1) if diario else None, giro=round(out90 / saldo, 2) if saldo > 0 else None,
                          ultima_saida=r['ultima_saida'], minimo=i.get('minimo') or 0))
    if not itens:
        return dict(pronto=False, kardex_atualizado=k.get('atualizado'), n_materia_prima=len(base.get('itens', [])))
    tot_out90 = sum(x['out90'] for x in itens)
    aviso = None
    if tot_out90 == 0:
        aviso = (f"O kardex nao trouxe nenhuma saida nos ultimos 180 dias para as {len(itens)} materias-primas ({k.get('sem_mov', 0)} sem movimento no TOTVS). "
                 "Se isso nao for verdade, o tipo de saldo do kardex pode estar errado: ajuste ALMOX_TIPO_SALDO no .env e abra /estoque-almox/diagnostico-kardex.")
    # curva ABC pelas saidas de 90 dias: A = ate 80% do consumo, B = ate 95%, C = resto
    acum, ordenados = 0.0, sorted(itens, key=lambda x: -x['out90'])
    for x in ordenados:
        acum += x['out90']
        pct = acum / tot_out90 if tot_out90 else 1
        x['classe'] = 'A' if (x['out90'] > 0 and pct - x['out90'] / tot_out90 < 0.80) else ('B' if x['out90'] > 0 and pct - x['out90'] / tot_out90 < 0.95 else 'C')
        x['pct_acum'] = round(pct, 4)
    parados = [x for x in itens if x['saldo'] > 0 and x['out90'] == 0]
    criticos = sorted([x for x in itens if x['cobertura'] is not None and x['cobertura'] < 15 and x['out90'] > 0], key=lambda x: x['cobertura'])
    atencao = [x for x in itens if x['cobertura'] is not None and 15 <= x['cobertura'] < 30]
    saldo_total = sum(x['saldo'] for x in itens)
    out30, out60 = sum(x['out30'] for x in itens), sum(x['out60'] for x in itens)
    ant30 = out60 - out30                                              # saidas dos 30 dias anteriores aos ultimos 30
    classe_a = [x for x in itens if x['classe'] == 'A']
    # tendencia mensal (6 meses): entradas x saidas, somando todas as materias-primas
    meses = {}
    for cod, r in prod.items():
        if cod not in {str(x['codigo']) for x in itens}:
            continue
        for mes, (e, sa, n) in r['meses'].items():
            m = meses.setdefault(mes, [0.0, 0.0, 0])
            m[0] += e
            m[1] += sa
            m[2] += n
    serie = [dict(mes=m, entradas=round(v[0], 1), saidas=round(v[1], 1), movimentos=v[2]) for m, v in sorted(meses.items())][-6:]
    consumo_diario = tot_out90 / 90.0
    return dict(
        pronto=True, aviso=aviso, kardex_atualizado=k.get('atualizado'), saldo_atualizado=base.get('atualizado'), estoque=base.get('estoque'), estoque_nome=base.get('estoque_nome'), n_materia_prima=len(itens), kardex_erros=k.get('erros', 0),
        cards=dict(saldo_total=round(saldo_total, 1), n_itens=len(itens), saidas30=round(out30, 1), saidas30_ant=round(ant30, 1),
                   var30=round(out30 / ant30 - 1, 4) if ant30 else None, consumo_diario=round(consumo_diario, 1),
                   cobertura_geral=round(saldo_total / consumo_diario, 1) if consumo_diario else None,
                   n_criticos=len(criticos), n_atencao=len(atencao), n_classe_a=len(classe_a), pct_consumo_a=round(sum(x['out90'] for x in classe_a) / tot_out90, 4) if tot_out90 else 0,
                   n_parados=len(parados), kg_parados=round(sum(x['saldo'] for x in parados), 1), pct_parados=round(sum(x['saldo'] for x in parados) / saldo_total, 4) if saldo_total else 0),
        top_consumo=[dict(codigo=x['codigo'], nome=x['nome'], out90=x['out90'], pct_acum=x['pct_acum'], classe=x['classe']) for x in ordenados[:15]],
        todos=[dict(codigo=x['codigo'], out30=x['out30'], out90=x['out90'], cobertura=x['cobertura'], saldo=x['saldo']) for x in itens], criticos=criticos[:12], alta_rotatividade=[x for x in ordenados if x['classe'] == 'A'][:15], parados=sorted(parados, key=lambda x: -x['saldo'])[:10], serie_mensal=serie)


_ULT_ACERTOS = [0.0]


def iniciar_agendador_kardex(app) -> None:
    if not configurado():
        return

    def loop():
        while not ler().get('itens'):                                   # espera a 1a lista de materias-primas
            time.sleep(15)
        while True:
            try:
                atualizar_transferencias()
                if time.time() - _ULT_ACERTOS[0] > 1800:
                    _ULT_ACERTOS[0] = time.time()
                    atualizar_acertos()
            except Exception:  # noqa: BLE001
                traceback.print_exc()
            time.sleep(TRANSF_INTERVALO_SEG)
    threading.Thread(target=loop, daemon=True, name='totvs-almox-kardex').start()


# ---------------------------------------------------------------------------
# Saidas para a PRODUCAO = transacoes da operacao 2011 (transferencia almoxarifado -> producao), direto das transacoes do TOTVS
# ---------------------------------------------------------------------------
def ler_transf() -> dict:
    try:
        return json.loads(ARQ_TRANSF.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def _cancelada(t: dict) -> bool:
    return any(t.get(k) is True for k in ('isCanceled', 'canceled', 'isCancelled', 'cancelled'))


def atualizar_transferencias() -> bool:
    """Busca as transacoes da operacao 2011 (so as novas/alteradas depois da ultima vez; na 1a vez, 180 dias) e guarda produto x kg por transacao."""
    if not _trava_k.acquire(blocking=False):
        return False
    ESTADO_K.update(rodando=True, erro=None, msg='Lendo as transferencias para a producao...', feitos=0, total=0)
    try:
        d = ler_transf()
        if d.get('versao') != 3:
            d = {}                                                     # formato antigo (sem documento): le de novo
        trans = d.get('transacoes', {})
        hoje = date.today()
        ops = [int(o) for o in OPERACOES_SAIDA if o.isdigit()] or [2011]
        ult = d.get('ultima_mudanca')
        ini = (date.fromisoformat(ult[:10]) - timedelta(days=2)) if ult else hoje - timedelta(days=KARDEX_DIAS)
        cli = api.TotvsModaClient()
        cli.autenticar()
        mais_recente, novas = ult or '', 0
        a = ini
        while a <= hoje:
            b = min(hoje, a + timedelta(days=14))
            pag = 1
            while True:
                r = cli.post(TRX_URL, dict(branchCodeList=[EMPRESA], operationCodeList=ops, startChangeDate=f'{a.isoformat()}T00:00:00', endChangeDate=f'{(b + timedelta(days=1)).isoformat()}T00:00:00', page=pag, pageSize=500))
                if r.status_code != 200:
                    raise RuntimeError(f'transferencias {a}..{b}: HTTP {r.status_code} {r.text[:200]}')
                j = r.json()
                for t in j.get('items') or []:
                    cod = str(t.get('transactionCode'))
                    mud = str(t.get('lastchangeDate') or '')[:19]
                    mais_recente = max(mais_recente, mud)
                    if _cancelada(t):
                        trans.pop(cod, None)
                        continue
                    itens = []
                    for it in t.get('items') or []:
                        if UNIDADE and str(it.get('measuredUnit') or '').strip().upper() != UNIDADE:
                            continue
                        kg = abs(_num(it.get('quantity')))
                        if kg and it.get('productCode') is not None:
                            itens.append([it['productCode'], round(kg, 3)])
                    if cod not in trans:
                        novas += 1
                    quando = str(t.get('insertDate') or t.get('lastchangeDate') or t.get('transactionDate') or '')[:19]
                    rel = t.get('relatedFiscalDocument') or {}
                    usuario = ''                                       # a API de transacoes nao informa quem fez; guarda o documento fiscal da transferencia
                    doc = str(rel.get('invoiceSequence') or rel.get('invoiceCode') or '')
                    trans[cod] = [str(t.get('transactionDate') or '')[:10], itens, quando, usuario, doc[:20] or cod]
                ESTADO_K['feitos'] += 1
                if not j.get('hasNext') or pag >= 60:
                    break
                pag += 1
            a = b + timedelta(days=1)
        limite = (hoje - timedelta(days=200)).isoformat()
        trans = {c: v for c, v in trans.items() if v[0] >= limite}
        ARQ_TRANSF.parent.mkdir(parents=True, exist_ok=True)
        tmp = ARQ_TRANSF.with_suffix('.tmp')
        tmp.write_text(json.dumps(dict(versao=3, atualizado=datetime.now().isoformat(timespec='seconds'), ultima_mudanca=mais_recente or ult, operacoes=ops, transacoes=trans), ensure_ascii=False), encoding='utf-8')
        api.trocar_arquivo(tmp, ARQ_TRANSF)
        try:
            enriquecer_operadores(cli)
        except Exception:  # noqa: BLE001  - o operador e um extra: sem ele o painel mostra so o documento
            traceback.print_exc()
        ESTADO_K.update(ultima=datetime.now().isoformat(timespec='seconds'), msg=f'{len(trans)} transferencias para a producao ({novas} novas)')
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        ESTADO_K.update(erro=str(e)[:300], msg='Falha ao ler as transferencias - mantive os dados anteriores')
    finally:
        ESTADO_K['rodando'] = False
        _trava_k.release()
    return ESTADO_K['erro'] is None


def consumo_mensal() -> dict:
    """Fios com mais saida para a producao em cada um dos ultimos meses: top 15 por mes + total do mes (a partir das transferencias da operacao 2011)."""
    base, d = ler(), ler_transf()
    nomes = {str(i['codigo']): i for i in base.get('itens', [])}
    por_item, n_mov = {}, {}
    for _cod, v in (d.get('transacoes') or {}).items():
        data, itens = v[0], v[1]
        if len(data) < 7:
            continue
        for prod, kg in itens:
            c = str(prod)
            if c not in nomes:
                continue
            por_item.setdefault(c, {})
            por_item[c][data[:7]] = por_item[c].get(data[:7], 0) + kg
            n_mov[(c, data[:7])] = n_mov.get((c, data[:7]), 0) + 1
    meses = sorted({m for v in por_item.values() for m in v})[-6:]
    saida = {}
    for m in meses:
        linhas = [dict(codigo=int(c), nome=nomes[c]['nome'], kg=round(v[m], 1), movimentos=n_mov.get((c, m), 0), saldo=nomes[c]['saldo']) for c, v in por_item.items() if v.get(m, 0) > 0]
        linhas.sort(key=lambda x: -x['kg'])
        saida[m] = dict(total=round(sum(x['kg'] for x in linhas), 1), n_itens=len(linhas), itens=linhas[:15])
    return dict(pronto=bool(meses), meses=meses, por_mes=saida, por_item={c: {m: round(v.get(m, 0), 1) for m in meses} for c, v in por_item.items()},
                kardex_atualizado=d.get('atualizado'), n_transacoes=len(d.get('transacoes') or {}), operacoes=d.get('operacoes'),
                aviso=None if meses else ('Nenhuma transferencia da operacao ' + ', '.join(sorted(OPERACOES_SAIDA)) + ' com matéria-prima em KG foi encontrada nos ultimos 180 dias.' if d.get('atualizado') else None))


def movimentacoes(n: int = 25) -> dict:
    """Ultimas transferencias do almoxarifado para a producao (quem, quando, o que, quantos kg): alimenta o painel ao vivo."""
    base, d = ler(), ler_transf()
    nomes = {str(i['codigo']): i['nome'] for i in base.get('itens', [])}
    linhas = []
    for cod, v in (d.get('transacoes') or {}).items():
        itens = [dict(codigo=p, nome=nomes[str(p)], kg=kg) for p, kg in v[1] if str(p) in nomes]
        if not itens:
            continue
        quando = (v[2] if len(v) > 2 and v[2] else v[0]) or ''
        doc = v[4] if len(v) > 4 else ''
        linhas.append(dict(id=cod, quando=quando, data=v[0], usuario=nome_operador(doc), documento=doc, itens=itens, total=round(sum(i['kg'] for i in itens), 1)))
    linhas.sort(key=lambda x: (x['quando'], x['id']), reverse=True)
    hoje = date.today().isoformat()
    do_dia = [x for x in linhas if x['data'] == hoje]
    return dict(atualizado=d.get('atualizado'), itens=linhas[:n], hoje_kg=round(sum(x['total'] for x in do_dia), 1), hoje_n=len(do_dia), tem_usuario=any(x['usuario'] for x in linhas[:50]))


# ---------------------------------------------------------------------------
# Acuracidade do estoque: ajustes de inventario (BALFM015) = operacoes 2040 (entrada) e 2041 (saida)
# ---------------------------------------------------------------------------
def ler_acertos() -> dict:
    try:
        return json.loads(ARQ_ACERTOS.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def atualizar_acertos() -> bool:
    """Ajustes de inventario dos ultimos ACUR_DIAS dias (so leitura): guarda produto, kg e sentido de cada acerto."""
    try:
        cli = api.TotvsModaClient()
        cli.autenticar()
        hoje = date.today()
        a, trans = hoje - timedelta(days=ACUR_DIAS), {}
        while a <= hoje:
            b = min(hoje, a + timedelta(days=29))
            pag = 1
            while True:
                r = cli.post(TRX_URL, dict(branchCodeList=[EMPRESA], operationCodeList=list(OPS_ACERTO.values()), startChangeDate=f'{a.isoformat()}T00:00:00', endChangeDate=f'{(b + timedelta(days=1)).isoformat()}T00:00:00', page=pag, pageSize=500))
                if r.status_code != 200:
                    raise RuntimeError(f'acertos {a}..{b}: HTTP {r.status_code} {r.text[:200]}')
                j = r.json()
                for t in j.get('items') or []:
                    if _cancelada(t):
                        continue
                    sinal = 1 if str(t.get('operationCode')) == str(OPS_ACERTO['entrada']) else -1
                    itens = [[it['productCode'], round(abs(_num(it.get('quantity'))) * sinal, 3)] for it in t.get('items') or []
                             if it.get('productCode') is not None and (not UNIDADE or str(it.get('measuredUnit') or '').strip().upper() == UNIDADE) and _num(it.get('quantity'))]
                    trans[str(t.get('transactionCode'))] = [str(t.get('transactionDate') or '')[:10], itens]
                if not j.get('hasNext') or pag >= 60:
                    break
                pag += 1
            a = b + timedelta(days=1)
        ARQ_ACERTOS.parent.mkdir(parents=True, exist_ok=True)
        tmp = ARQ_ACERTOS.with_suffix('.tmp')
        tmp.write_text(json.dumps(dict(versao=1, atualizado=datetime.now().isoformat(timespec='seconds'), dias=ACUR_DIAS, transacoes=trans), ensure_ascii=False), encoding='utf-8')
        api.trocar_arquivo(tmp, ARQ_ACERTOS)
        return True
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        return False


def acuracidade() -> dict:
    """Taxa de acuracidade = 1 - (kg ajustados no inventario / estoque atual); mostra tambem quantos itens tiveram ajuste e a data do ultimo inventario."""
    base, d = ler(), ler_acertos()
    if not d.get('atualizado'):
        return dict(taxa=None, texto='lendo os ajustes de inventário no TOTVS…')
    saldos = {str(i['codigo']): i['saldo'] for i in base.get('itens', [])}
    total = sum(saldos.values())
    ajust_kg, itens_aj, ultimo = 0.0, set(), ''
    for _cod, (data, itens) in (d.get('transacoes') or {}).items():
        usou = False
        for prod, kg in itens:
            if str(prod) in saldos:
                ajust_kg += abs(kg)
                itens_aj.add(str(prod))
                usou = True
        if usou:
            ultimo = max(ultimo, data)
    if not itens_aj:
        return dict(taxa=1.0 if total else None, texto=f'nenhum ajuste de inventário em {d.get("dias", ACUR_DIAS)} dias · {len(saldos)} itens', n_ajustados=0, n_itens=len(saldos), kg_ajustado=0, ultimo_inventario=None)
    taxa = max(0.0, 1 - ajust_kg / total) if total else None
    return dict(taxa=round(taxa, 4) if taxa is not None else None, n_ajustados=len(itens_aj), n_itens=len(saldos), kg_ajustado=round(ajust_kg, 1), ultimo_inventario=ultimo,
                texto=f'{len(itens_aj)} de {len(saldos)} itens com ajuste · {nf_kg(ajust_kg)} kg ajustados · último inventário {ultimo[8:10]}/{ultimo[5:7]}')


def nf_kg(v: float) -> str:
    return f'{v:,.0f}'.replace(',', '.')


# ---------------------------------------------------------------------------
# Operador da transferencia: o "Operador/Atualizacao" da capa da nota fiscal (FISFL031) do documento da transferencia
# ---------------------------------------------------------------------------
def _ler_operadores() -> dict:
    try:
        return json.loads(ARQ_OPERADORES.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def _campo_operador(nota: dict):
    """Acha na nota o codigo/nome do operador (campos cujo nome traz operator/user), no nivel principal."""
    cod = nome = None
    for k, v in nota.items():
        kl = k.lower()
        if v in (None, '') or isinstance(v, (dict, list)):
            continue
        if ('operator' in kl or 'user' in kl or 'update' in kl) and 'date' not in kl:
            if 'name' in kl:
                nome = nome or str(v)
            else:
                cod = cod or str(v)
    return cod, nome


_FISCAL = {}                                                          # propriedades do filtro de invoices/search (lidas do swagger)


def _props_filtro_fiscal(cli) -> list:
    """Nomes dos campos do filtro da busca de notas, lidos do swagger do servico fiscal (para nao adivinhar)."""
    if 'props' in _FISCAL:
        return _FISCAL['props']
    props = []
    try:
        r = cli.get(f'{BASE_URL}/api/totvsmoda/fiscal/v2/swagger/v1/swagger.json', {})
        if r.status_code == 200:
            d = r.json()
            sch = d.get('components', {}).get('schemas', {})

            def resolve(n):
                return sch.get((n or {}).get('$ref', '').split('/')[-1], n or {})
            op = (d.get('paths', {}).get('/api/totvsmoda/fiscal/v2/invoices/search') or {}).get('post') or {}
            corpo = resolve(((op.get('requestBody') or {}).get('content') or {}).get('application/json', {}).get('schema'))
            filtro = resolve((corpo.get('properties') or {}).get('filter'))
            props = list((filtro.get('properties') or {}).keys())
    except Exception:  # noqa: BLE001
        traceback.print_exc()
    _FISCAL['props'] = props
    return props


def _variantes_nota(cli, seq: str, d0: str) -> list:
    props = _props_filtro_fiscal(cli)
    out = []
    if seq.isdigit():
        for k in props:
            kl = k.lower()
            if ('sequence' in kl or kl in ('invoicecodelist', 'invoicenumberlist')) and kl.endswith('list'):
                out.append(dict(branchCodeList=[EMPRESA], **{k: [int(seq)]}))
    for k in props:
        if k.lower().startswith('start') and 'date' in k.lower():
            fim = 'end' + k[5:]
            if fim in props:
                out.append(dict(branchCodeList=[EMPRESA], **{k: f'{d0}T00:00:00', fim: f'{d0}T23:59:59'}))
    if not out:                                                        # swagger indisponivel: formas comuns
        out = [dict(branchCodeList=[EMPRESA], startInvoiceDate=f'{d0}T00:00:00', endInvoiceDate=f'{d0}T23:59:59'), dict(branchCodeList=[EMPRESA], startIssueDate=f'{d0}T00:00:00', endIssueDate=f'{d0}T23:59:59')]
    return out


def _buscar_nota(cli, seq: str, data: str, tentativas: list = None):
    """Procura a nota (fatura) da transferencia na API fiscal; devolve o dict da nota ou None. `tentativas` (opcional) recebe o log de cada filtro."""
    d0 = data or date.today().isoformat()
    for filtro in _variantes_nota(cli, str(seq), d0):
        pag = 1
        while pag <= 6:
            r = cli.post(INVOICES_URL, dict(filter=filtro, page=pag, pageSize=500))
            if tentativas is not None:
                tentativas.append(dict(filtro={k: str(v)[:40] for k, v in filtro.items()}, pagina=pag, http=r.status_code, erro=(r.text[:140] if r.status_code != 200 else None)))
            if r.status_code != 200:
                break
            j = r.json()
            for n in j.get('items') or []:
                if str(n.get('invoiceSequence') or n.get('invoiceCode') or '') == str(seq):
                    return n
            if not j.get('hasNext'):
                break
            pag += 1
    return None


def enriquecer_operadores(cli, limite: int = 25) -> int:
    """Descobre o operador das transferencias mais recentes (cache por documento); devolve quantos foram resolvidos agora."""
    d = ler_transf()
    trans = d.get('transacoes') or {}
    cache = _ler_operadores()
    agora = time.time()
    recentes = sorted(trans.items(), key=lambda kv: (kv[1][2] if len(kv[1]) > 2 else '', kv[0]), reverse=True)[:limite]
    novos = 0
    for cod, v in recentes:
        fatura = v[4] if len(v) > 4 else ''
        if not fatura or fatura in cache and (cache[fatura].get('codigo') or agora - cache[fatura].get('tentativa', 0) < 6 * 3600):
            continue
        nota = _buscar_nota(cli, fatura, v[0])
        cod_op, nome = _campo_operador(nota) if nota else (None, None)
        cache[fatura] = dict(codigo=cod_op, nome=nome, tentativa=agora)
        if cod_op or nome:
            novos += 1
    ARQ_OPERADORES.parent.mkdir(parents=True, exist_ok=True)
    ARQ_OPERADORES.write_text(json.dumps(cache, ensure_ascii=False), encoding='utf-8')
    return novos


def nome_operador(fatura: str) -> str:
    info = _ler_operadores().get(str(fatura)) or {}
    cod = str(info.get('codigo') or '')
    return (info.get('nome') or NOMES_OPERADORES.get(cod) or (f'Operador {cod}' if cod else '')).strip()
