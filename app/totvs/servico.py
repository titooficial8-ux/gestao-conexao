"""Faturamento TOTVS no Gestao: gera os dados, guarda em instance/totvs e agenda a atualizacao diaria.

Duas "fontes" de dados ficam salvas:
  padrao  - ano corrente ate hoje (atualizada todo dia no horario agendado, 08:00)
  custom  - periodo escolhido na tela pelo botao "Gerar relatorio"
"""
from __future__ import annotations

import json
import os
import sys
import threading
import traceback
from datetime import date, datetime, timedelta
from pathlib import Path

DIR = Path(os.getenv('FATURAMENTO_SAIDA', Path(__file__).resolve().parents[2] / 'instance' / 'totvs'))
TAGS = ('padrao', 'custom')
HORA_PADRAO = '08:00'

try:  # console do Windows (cp1252) nao imprime alguns simbolos do script original
    sys.stdout.reconfigure(errors='replace')
    sys.stderr.reconfigure(errors='replace')
except Exception:
    pass

_lock = threading.Lock()
ESTADO = dict(rodando=False, tag=None, inicio=None, fim=None, msg='', iniciado_em=None, erro=None, origem=None)


def configurado() -> bool:
    return all(os.getenv(k) for k in ('TOTVS_CLIENT_ID', 'TOTVS_CLIENT_SECRET'))


def _agora():
    return datetime.now()


def _escrever(caminho: Path, obj) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    tmp = caminho.with_suffix(caminho.suffix + '.tmp')
    tmp.write_text(json.dumps(obj, ensure_ascii=False, default=str), encoding='utf-8')
    os.replace(tmp, caminho)


def caminho_dados(tag: str) -> Path:
    return DIR / f'dados_{tag}.json'


def carregar(tag: str):
    """{meta, colunas, linhas} da fonte, ou None se ainda nao foi gerada.
    'padrao' = ano corrente, montado a partir do armazem mensal (se houver); 'custom' = ultimo periodo gerado."""
    if tag == 'padrao':
        d = _padrao_do_armazem()
        if d:
            return d
    c = caminho_dados(tag)
    if not c.exists():
        return None
    return _carregar_cache(str(c), c.stat().st_mtime)


_cache = {}
BASE_DIR = DIR / 'base'          # um arquivo por mes: 150_AAAA-MM.json (Relatorio 150 daquele mes)


def _blocos():
    return sorted(BASE_DIR.glob('150_????-??.json')) if BASE_DIR.exists() else []


def meses_armazenados():
    return [b.stem[4:] for b in _blocos()]


def _chave_blocos():
    return tuple((b.name, b.stat().st_mtime_ns) for b in _blocos())


_cache_arm = {}


def linhas_armazem():
    """(colunas, linhas) de TODOS os meses guardados (cache enquanto os arquivos nao mudam)."""
    k = _chave_blocos()
    if _cache_arm.get('k') != k:
        cols, linhas, gerado = None, [], ''
        for b in _blocos():
            d = json.loads(b.read_text(encoding='utf-8'))
            cols = cols or d['colunas']
            linhas.extend(d['linhas'])
            gerado = max(gerado, d.get('gerado_em', ''))
        _cache_arm.update(k=k, cols=cols, linhas=linhas, gerado=gerado)
    return _cache_arm.get('cols'), _cache_arm.get('linhas') or [], _cache_arm.get('gerado', '')


def _padrao_do_armazem():
    cols, linhas, gerado = linhas_armazem()
    if not linhas:
        return None
    hoje = _agora().date()
    i = cols.index('DATA')
    ano = [r for r in linhas if str(r[i])[:4] == str(hoje.year)]
    if not ano:
        return None
    ultima = max(str(r[i])[:10] for r in ano)
    m = dict(tag='padrao', inicio=f'{hoje.year}-01-01', fim=ultima, gerado_em=gerado, n_itens=len(ano), n_notas=len({(r[0], r[cols.index('NFE')]) for r in ano}),
             n_validas=len({(r[0], r[cols.index('NFE')]) for r in ano}), total_nf=0.0, arquivos={}, problemas=[], origem='armazem')
    return dict(meta=m, colunas=cols, linhas=ano)


def _carregar_cache(caminho, mtime):
    k = (caminho, mtime)
    if k not in _cache:
        _cache.clear()
        d = json.loads(Path(caminho).read_text(encoding='utf-8'))
        _cache[k] = d
    return _cache[k]


def meta(tag: str):
    d = carregar(tag)
    return d['meta'] if d else None


# ------------------------------------------------------------------ geracao
def _log(msg: str) -> None:
    ESTADO['msg'] = msg
    print(f'[TOTVS] {msg}')


def gerar_dados(inicio: date, fim: date, tag: str) -> dict:
    """Roda a extracao completa (mesma logica do script Faturamento_TOTVS) e grava os arquivos."""
    from app.totvs import api
    import pandas as pd

    _log('Autenticando no TOTVS...')
    cli = api.TotvsModaClient()
    cli.autenticar()
    do_token = api.empresas_do_token(api.claims_do_token(cli.token or ''))
    empresas = api.BRANCHES or do_token
    if not empresas:
        _log('Descobrindo as empresas cadastradas...')
        empresas = api.descobrir_empresas(cli)
    if not empresas:
        raise RuntimeError('Nao consegui descobrir as empresas. Informe TOTVS_BRANCHES no arquivo .env (ex.: TOTVS_BRANCHES=1,2).')

    _log(f'Buscando notas fiscais {inicio:%d/%m/%Y} a {fim:%d/%m/%Y} (empresas {empresas})...')
    notas = []
    try:
        notas = api.buscar_notas(cli, inicio, fim, empresas)
    except api.EmpresaNaoPermitida:
        ok = []
        for emp in empresas:
            try:
                notas.extend(api.buscar_notas(cli, inicio, fim, [emp]))
                ok.append(emp)
            except api.EmpresaNaoPermitida:
                print(f'empresa {emp} nao liberada para o usuario - pulando')
        if not ok:
            raise RuntimeError('Nenhuma empresa informada esta liberada para este usuario do TOTVS.')

    DIR.mkdir(parents=True, exist_ok=True)
    suf = f'{inicio:%Y%m%d}_{fim:%Y%m%d}'
    _log(f'{len(notas)} notas recebidas. Montando o Relatorio 150...')
    df_notas = api.montar_notas(notas)
    try:
        df150, diag = api.montar_relatorio150(cli, notas, DIR / f'diagnostico_{tag}')
    except Exception as e:
        traceback.print_exc()
        df150, diag = pd.DataFrame(columns=api.COLUNAS_150), None
        _log(f'Relatorio 150 incompleto: {e}')

    _log('Salvando Excel / CSV...')
    # apaga arquivos antigos desta fonte (mantem so a ultima geracao)
    for velho in DIR.glob(f'{tag}_*'):
        try:
            velho.unlink()
        except OSError:
            pass
    arquivos = {}
    x_all = DIR / f'{tag}_faturamento_{suf}.xlsx'
    api.salvar_excel(x_all, {'Relatório 150': api.df_150_com_total(df150), 'Notas': df_notas,
                            'Itens': api.montar_itens(notas), **api.resumos(df_notas)})
    arquivos['xlsx'] = x_all.name
    if not df150.empty:
        x150, c150 = DIR / f'{tag}_FISCAL_0150_{suf}.xlsx', DIR / f'{tag}_FISCAL_0150_{suf}.csv'
        api.salvar_xlsx_150(df150, x150)
        api.salvar_csv_150(df150, c150)
        arquivos['xlsx150'], arquivos['csv150'] = x150.name, c150.name

    validas = df_notas[df_notas['Válida p/ Faturamento'] == 'Sim'] if not df_notas.empty else df_notas
    cols = list(df150.columns)
    linhas = []
    for r in df150.itertuples(index=False, name=None):
        linhas.append([None if (isinstance(v, float) and v != v) or v is pd.NaT else (v.isoformat() if hasattr(v, 'isoformat') else v) for v in r])
    m = dict(tag=tag, inicio=inicio.isoformat(), fim=fim.isoformat(), gerado_em=_agora().isoformat(timespec='seconds'),
             n_notas=int(len(df_notas)), n_validas=int(len(validas)), n_itens=len(linhas),
             total_nf=float(validas['Valor Total NF'].fillna(0).sum()) if len(validas) else 0.0,
             arquivos=arquivos, problemas=(diag.problemas if diag else ['Relatorio 150 nao gerado']))
    _escrever(caminho_dados(tag), dict(meta=m, colunas=cols, linhas=linhas))
    return m


_empresas = []


def _empresas_do_usuario(api, cli):
    if _empresas:
        return _empresas
    do_token = api.empresas_do_token(api.claims_do_token(cli.token or ''))
    emp = api.BRANCHES or do_token
    if not emp:
        _log('Descobrindo as empresas cadastradas...')
        emp = api.descobrir_empresas(cli)
    if not emp:
        raise RuntimeError('Nao consegui descobrir as empresas. Informe TOTVS_BRANCHES no arquivo .env (ex.: TOTVS_BRANCHES=1,2).')
    _empresas[:] = emp
    return _empresas


def _meses(inicio: date, fim: date):
    a, m = inicio.year, inicio.month
    while (a, m) <= (fim.year, fim.month):
        ini = date(a, m, 1)
        prox = date(a + (m == 12), 1 if m == 12 else m + 1, 1)
        yield f'{a}-{m:02d}', max(ini, inicio), min(prox - timedelta(days=1), fim)
        a, m = prox.year, prox.month


def gerar_blocos(inicio: date, fim: date, pular_existentes: bool = False) -> dict:
    """Busca no TOTVS mes a mes e grava um bloco (Relatorio 150) por mes em instance/totvs/base."""
    from app.totvs import api
    import pandas as pd

    _log('Autenticando no TOTVS...')
    cli = api.TotvsModaClient()
    cli.autenticar()
    empresas = _empresas_do_usuario(api, cli)
    hoje_ym = _agora().strftime('%Y-%m')
    feitos, total_itens = [], 0
    for ym, ini_m, fim_m in _meses(inicio, fim):
        arq = BASE_DIR / f'150_{ym}.json'
        if pular_existentes and arq.exists() and ym < hoje_ym:
            _log(f'{ym}: ja carregado, pulando')
            continue
        _log(f'{ym}: buscando notas fiscais...')
        try:
            notas = api.buscar_notas(cli, ini_m, fim_m, empresas)
        except api.EmpresaNaoPermitida:
            notas = []
            for emp in empresas:
                try:
                    notas.extend(api.buscar_notas(cli, ini_m, fim_m, [emp]))
                except api.EmpresaNaoPermitida:
                    print(f'empresa {emp} nao liberada - pulando')
        _log(f'{ym}: {len(notas)} notas; montando o Relatorio 150...')
        df150, diag = api.montar_relatorio150(cli, notas, DIR / 'diagnostico_base')
        cols = list(df150.columns)
        linhas = [[None if (isinstance(v, float) and v != v) or v is pd.NaT else (v.isoformat() if hasattr(v, 'isoformat') else v) for v in r]
                  for r in df150.itertuples(index=False, name=None)]
        _escrever(arq, dict(periodo=ym, gerado_em=_agora().isoformat(timespec='seconds'), colunas=cols, linhas=linhas,
                            problemas=(diag.problemas if diag else [])))
        feitos.append(ym)
        total_itens += len(linhas)
    return dict(meses=feitos, n_itens=total_itens)


def _rodar(inicio: date, fim: date, tag: str, origem: str) -> None:
    from app.totvs import api
    try:
        if tag == 'base':
            m = gerar_blocos(inicio, fim, pular_existentes=(origem == 'historico'))
            ESTADO.update(erro=None, msg=f'Concluido: {len(m["meses"])} mes(es) atualizado(s), {m["n_itens"]} itens.')
        else:
            m = gerar_dados(inicio, fim, tag)
            ESTADO.update(erro=None, msg=f'Concluido: {m["n_itens"]} itens em {m["n_validas"]} notas validas.')
        if origem == 'agenda':
            _escrever(DIR / 'agenda.json', dict(ultima_ok=_agora().isoformat(timespec='seconds'), tentativa=_agora().isoformat(timespec='seconds')))
    except api.Cancelado:
        ESTADO.update(erro=None, msg='Busca cancelada.')
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        ESTADO.update(erro=str(e)[:600], msg='Falhou')
        if origem == 'agenda':
            _escrever(DIR / 'agenda.json', {**_ler_agenda(), 'tentativa': _agora().isoformat(timespec='seconds')})
    finally:
        ESTADO['rodando'] = False


def iniciar(inicio: date, fim: date, tag: str = 'custom', origem: str = 'botao') -> bool:
    """Dispara a geracao em segundo plano. False se ja ha uma em andamento."""
    from app.totvs import api
    with _lock:
        if ESTADO['rodando']:
            return False
        api.CANCELAR.clear()
        ESTADO.update(rodando=True, tag=tag, inicio=inicio.isoformat(), fim=fim.isoformat(), msg='Iniciando...',
                      iniciado_em=_agora().isoformat(timespec='seconds'), erro=None, origem=origem)
    threading.Thread(target=_rodar, args=(inicio, fim, tag, origem), daemon=True, name='totvs-gerar').start()
    return True


def cancelar() -> bool:
    """Pede para a busca em andamento parar (ela aborta na proxima chamada a API)."""
    from app.totvs import api
    if not ESTADO['rodando']:
        return False
    api.CANCELAR.set()
    ESTADO['msg'] = 'Cancelando...'
    return True


# ------------------------------------------------------------------ agenda 08:00
def intervalo_min() -> int:
    try:
        return max(5, int(os.getenv('BI_INTERVALO_MIN', '30')))
    except ValueError:
        return 30


def _ler_agenda() -> dict:
    try:
        return json.loads((DIR / 'agenda.json').read_text(encoding='utf-8'))
    except Exception:
        return {}


def proxima_atualizacao() -> str:
    """Texto para a tela: a atualizacao automatica roda a cada N minutos (so com o servidor ligado e historico carregado)."""
    if not meses_armazenados():
        return 'automatica desligada (carregue o historico primeiro)'
    ag = _ler_agenda().get('tentativa')
    try:
        prox = datetime.fromisoformat(ag) + timedelta(minutes=intervalo_min()) if ag else _agora()
    except ValueError:
        prox = _agora()
    return f'a cada {intervalo_min()} min (proxima ~{max(prox, _agora()):%H:%M})'


def _tick() -> None:
    if not configurado() or ESTADO['rodando'] or not meses_armazenados():
        return
    ag = _ler_agenda().get('tentativa')
    if ag:
        try:
            if (_agora() - datetime.fromisoformat(ag)).total_seconds() < intervalo_min() * 60:
                return
        except ValueError:
            pass
    hoje = _agora().date()
    ini = (hoje.replace(day=1) - timedelta(days=1)).replace(day=1)       # mes anterior (pega notas lancadas com atraso)
    iniciar(ini, hoje, 'base', 'agenda')


def iniciar_agendador() -> None:
    """Thread leve: a cada 30 s confere se ja passou o intervalo (30 min) desde a ultima atualizacao automatica."""
    def loop():
        import time
        time.sleep(20)
        while True:
            try:
                _tick()
            except Exception:  # noqa: BLE001
                traceback.print_exc()
            time.sleep(30)
    threading.Thread(target=loop, daemon=True, name='totvs-agenda').start()
