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
    """(meta, colunas, linhas) da fonte, ou None se ainda nao foi gerada."""
    c = caminho_dados(tag)
    if not c.exists():
        return None
    return _carregar_cache(str(c), c.stat().st_mtime)


_cache = {}


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


def _rodar(inicio: date, fim: date, tag: str, origem: str) -> None:
    try:
        m = gerar_dados(inicio, fim, tag)
        ESTADO.update(erro=None, msg=f'Concluido: {m["n_itens"]} itens em {m["n_validas"]} notas validas.')
        if origem == 'agenda':
            _escrever(DIR / 'agenda.json', dict(ultima_ok=_agora().date().isoformat(), tentativa=_agora().isoformat(timespec='seconds')))
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        ESTADO.update(erro=str(e)[:600], msg='Falhou')
        if origem == 'agenda':
            _escrever(DIR / 'agenda.json', {**_ler_agenda(), 'tentativa': _agora().isoformat(timespec='seconds')})
    finally:
        ESTADO['rodando'] = False


def iniciar(inicio: date, fim: date, tag: str = 'custom', origem: str = 'botao') -> bool:
    """Dispara a geracao em segundo plano. False se ja ha uma em andamento."""
    with _lock:
        if ESTADO['rodando']:
            return False
        ESTADO.update(rodando=True, tag=tag, inicio=inicio.isoformat(), fim=fim.isoformat(), msg='Iniciando...',
                      iniciado_em=_agora().isoformat(timespec='seconds'), erro=None, origem=origem)
    threading.Thread(target=_rodar, args=(inicio, fim, tag, origem), daemon=True, name='totvs-gerar').start()
    return True


# ------------------------------------------------------------------ agenda 08:00
def hora_agendada() -> tuple[int, int]:
    txt = os.getenv('BI_HORA_ATUALIZACAO', HORA_PADRAO)
    try:
        h, m = txt.split(':')
        return int(h), int(m)
    except ValueError:
        return 8, 0


def _ler_agenda() -> dict:
    try:
        return json.loads((DIR / 'agenda.json').read_text(encoding='utf-8'))
    except Exception:
        return {}


def proxima_atualizacao() -> str:
    h, m = hora_agendada()
    agora = _agora()
    alvo = agora.replace(hour=h, minute=m, second=0, microsecond=0)
    ag = _ler_agenda()
    if agora >= alvo and ag.get('ultima_ok') == agora.date().isoformat():
        alvo += timedelta(days=1)
    elif agora >= alvo:
        return 'pendente (roda assim que possivel)'
    return alvo.strftime('%d/%m/%Y %H:%M')


def _tick() -> None:
    if not configurado() or ESTADO['rodando']:
        return
    h, m = hora_agendada()
    agora = _agora()
    if agora < agora.replace(hour=h, minute=m, second=0, microsecond=0):
        return
    ag = _ler_agenda()
    if ag.get('ultima_ok') == agora.date().isoformat():
        return
    ult = ag.get('tentativa')
    if ult:  # depois de uma falha espera 30 min antes de tentar de novo
        try:
            if (agora - datetime.fromisoformat(ult)).total_seconds() < 1800 and ag.get('ultima_ok') != agora.date().isoformat() and ESTADO.get('erro'):
                return
        except ValueError:
            pass
    iniciar(date(agora.year, 1, 1), agora.date(), 'padrao', 'agenda')


def iniciar_agendador() -> None:
    """Thread leve que confere a cada 30s se ja passou das 08:00 e hoje ainda nao atualizou
    (se o servidor ficou desligado de manha, atualiza assim que subir)."""
    def loop():
        import time
        time.sleep(15)
        while True:
            try:
                _tick()
            except Exception:  # noqa: BLE001
                traceback.print_exc()
            time.sleep(30)
    threading.Thread(target=loop, daemon=True, name='totvs-agenda').start()
