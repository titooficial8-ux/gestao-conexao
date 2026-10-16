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
INTERVALO_MIN = max(1, int(os.getenv('ALMOX_INTERVALO_MIN', '5')))
KARDEX_URL = f'{BASE_URL}/api/totvsmoda/product/v2/kardex-movement'
TIPO_SALDO_FIXO = os.getenv('ALMOX_TIPO_SALDO', '').strip()           # "tipo de saldo" do kardex (PRDFL017); padrao = o proprio codigo do estoque
KARDEX_DIAS = 179                                                  # a API limita a janela a 180 dias
KARDEX_INTERVALO_MIN = max(5, int(os.getenv('ALMOX_KARDEX_MIN', '15')))
ARQUIVO = api.OUT_DIR / 'almoxarifado.json'
ARQ_KARDEX = api.OUT_DIR / 'almoxarifado_kardex.json'
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


def tipo_saldo() -> int:
    return int(TIPO_SALDO_FIXO) if TIPO_SALDO_FIXO.isdigit() else (estoque_atual() or 0)


def ler() -> dict:
    try:
        return json.loads(ARQUIVO.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def _gravar(d: dict) -> None:
    ARQUIVO.parent.mkdir(parents=True, exist_ok=True)
    tmp = ARQUIVO.with_suffix('.tmp')
    tmp.write_text(json.dumps(d, ensure_ascii=False), encoding='utf-8')
    tmp.replace(ARQUIVO)


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
    produtos = {}
    for i in range(0, len(codigos), 200):
        for p in _paginar(cli, PRODUCTS_URL, dict(filter=dict(productCodeList=codigos[i:i + 200]), option=dict(branchInfoCode=EMPRESA), expand='classifications'), tam=500):
            produtos[p.get('productCode')] = p
    if amostra:
        return dict(estoque=ESTOQUE, estoque_nome=NOME, classificacao_mp=CLASSIF_MP, classificacoes_amostra=_amostra_classif(produtos), tipos_de_classificacao=_tipos_classif(cli), estoques_no_totvs={str(c): d for c, d in sorted((_RES.get('estoques') or descobrir_estoques(cli)).items())}, saldos_amostra=saldos[:3], produtos_amostra=list(produtos.values())[:3], n_saldos=len(saldos))
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
                          origem=_classif(p, 512), tipo_mp=_classif(p, 501), minimo=_num(p.get('minimumStockAmount')), maximo=_num(p.get('maximumStockAmount')), ideal=_num(p.get('idealStockAmount')), bloqueado=bool(p.get('isBlocked'))))
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
            time.sleep(INTERVALO_MIN * 60)
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


def _resumir(resp: dict, hoje: date) -> dict:
    """Agrega as movimentacoes de um produto: por mes (entradas, saidas, n), saidas 30/60/90 dias, ultima saida."""
    meses, out30, out60, out90, in90, n30, n90, ult_saida, ult_mov = {}, 0.0, 0.0, 0.0, 0.0, 0, 0, None, None
    for m in resp.get('movements') or []:
        d = str(m.get('movementDate') or '')[:10]
        if not d:
            continue
        ent, sai = _num(m.get('inQuantity')), _num(m.get('outQuantity'))
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

        def um(cod):
            r = cli.get(KARDEX_URL, dict(ProductCode=cod, BranchCode=EMPRESA, StartDate=f'{ini}T00:00:00', EndDate=f'{hoje.isoformat()}T23:59:59', BalanceType=tipo_saldo()))
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
        tmp.write_text(json.dumps(dict(versao=VERSAO, atualizado=datetime.now().isoformat(timespec='seconds'), dias=KARDEX_DIAS + 1, produtos=produtos, erros=len(erros), sem_mov=sem_mov, amostra_erro=erros[:2]), ensure_ascii=False), encoding='utf-8')
        tmp.replace(ARQ_KARDEX)
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


def iniciar_agendador_kardex(app) -> None:
    if not configurado():
        return

    def loop():
        while not ler().get('itens'):                                   # espera a 1a lista de materias-primas
            time.sleep(15)
        while True:
            try:
                atualizar_kardex()
            except Exception:  # noqa: BLE001
                traceback.print_exc()
            time.sleep(KARDEX_INTERVALO_MIN * 60)
    threading.Thread(target=loop, daemon=True, name='totvs-almox-kardex').start()
