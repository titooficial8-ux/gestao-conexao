"""Conciliacao FISFL076 (faturamento por data de emissao) x Relatorio 150: busca as notas do periodo na API (mesma busca do Relatorio 150,
por data de EMISSAO) e abre o total por status, tipo e operacao, mostrando o que entra e o que fica fora do 150 e quanto cada pedaco pesa."""
import threading
import traceback
from datetime import date, datetime

from app.totvs import api, base, servico

ESTADO = dict(rodando=False, resultado=None, erro=None, periodo=None)
_trava = threading.Lock()


def _soma(n):
    return sum(float(i.get('netValue') or 0) for i in (n.get('items') or []))


def calcular(ini: date, fim: date) -> dict:
    api.OPERACOES_150 = servico._operacoes_150(base) or api.OPERACOES_150
    cli = api.TotvsModaClient()
    cli.autenticar()
    empresas = servico._empresas_do_usuario(api, cli)
    try:
        notas = api.buscar_notas(cli, ini, fim, empresas)
    except api.EmpresaNaoPermitida:
        notas = []
        for emp in empresas:
            try:
                notas.extend(api.buscar_notas(cli, ini, fim, [emp]))
            except api.EmpresaNaoPermitida:
                pass
    por_status, validas = {}, []
    for n in notas:
        s = n.get('invoiceStatus') or '?'
        x = por_status.setdefault(s, dict(notas=0, total_nf=0.0))
        x['notas'] += 1
        x['total_nf'] += float(n.get('totalValue') or 0)
        if s in api.STATUS_VALIDOS:
            validas.append(n)
    ops = api.buscar_operacoes(cli, [n.get('operationCode') for n in validas if n.get('operationCode')], api.Diagnostico(servico.DIR / 'diagnostico_conciliacao'))
    em150 = set()
    mod = api.OPERACOES_150
    for n in validas:
        cod = n.get('operationCode')
        saida = str(n.get('operationType', '')).lower() in ('output', '2', 's', 'saida', 'saída')
        dev = 'DEV' in str(n.get('operatioName') or '').upper()
        if mod not in ('', 'financeiro', 'todas'):
            ok = cod in {int(x) for x in mod.replace(';', ',').split(',') if x.strip()}
        elif mod == 'financeiro':
            ok = bool(ops.get(cod, {}).get('isFinancial'))
        else:
            ok = True
        if ok and mod in ('financeiro', 'vendas') and not (saida or dev):
            ok = False
        if ok:
            em150.add(id(n))
    grupos = {}
    for n in validas:
        cod = n.get('operationCode')
        g = grupos.setdefault(cod, dict(operacao=cod, nome=n.get('operatioName'), tipo=str(n.get('operationType')), financeiro=bool(ops.get(cod, {}).get('isFinancial')),
                                        notas=0, total_nf=0.0, soma_itens=0.0, no_150=id(n) in em150))
        g['notas'] += 1
        g['total_nf'] += float(n.get('totalValue') or 0)
        g['soma_itens'] += _soma(n)
    lista = sorted(grupos.values(), key=lambda g: -abs(g['total_nf']))
    tot = lambda f, k: round(sum(g[k] for g in lista if f(g)), 2)
    diverg = sorted(({'nf': n.get('invoiceCode'), 'operacao': n.get('operationCode'), 'emissao': str(n.get('issueDate') or '')[:10], 'fatura': str(n.get('invoiceDate') or '')[:10],
                      'total_nf': float(n.get('totalValue') or 0), 'soma_itens': round(_soma(n), 2), 'diferenca': round(float(n.get('totalValue') or 0) - _soma(n), 2)}
                     for n in validas if id(n) in em150 and abs(float(n.get('totalValue') or 0) - _soma(n)) > 0.01), key=lambda x: -abs(x['diferenca']))
    fora_mes = [{'nf': n.get('invoiceCode'), 'emissao': str(n.get('issueDate') or '')[:10], 'fatura': str(n.get('invoiceDate') or '')[:10], 'total_nf': float(n.get('totalValue') or 0)}
                for n in validas if id(n) in em150 and str(n.get('invoiceDate') or '')[:7] != str(n.get('issueDate') or '')[:7]]
    return dict(periodo=f'{ini:%d/%m/%Y} a {fim:%d/%m/%Y}', notas_recebidas=len(notas), por_status=por_status, operacoes=lista, modo_150=mod,
                totais=dict(todas_validas_total_nf=tot(lambda g: True, 'total_nf'), saidas_total_nf=tot(lambda g: g['tipo'].lower() in ('output', '2', 's', 'saida', 'saída'), 'total_nf'),
                            no_150_total_nf=tot(lambda g: g['no_150'], 'total_nf'), no_150_soma_itens=tot(lambda g: g['no_150'], 'soma_itens'),
                            fora_do_150_total_nf=tot(lambda g: not g['no_150'], 'total_nf')),
                notas_com_total_diferente_dos_itens=diverg[:30], soma_dessas_diferencas=round(sum(x['diferenca'] for x in diverg), 2), qtd_dessas=len(diverg), emissao_e_fatura_em_meses_diferentes=fora_mes[:30],
                gerado_em=datetime.now().isoformat(timespec='seconds'))


def iniciar(ini: date, fim: date) -> bool:
    if not _trava.acquire(blocking=False):
        return False

    def run():
        ESTADO.update(rodando=True, resultado=None, erro=None, periodo=f'{ini:%d/%m/%Y} a {fim:%d/%m/%Y}')
        try:
            ESTADO['resultado'] = calcular(ini, fim)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            ESTADO['erro'] = f'{type(e).__name__}: {str(e)[:300]}'
        finally:
            ESTADO['rodando'] = False
            _trava.release()
    threading.Thread(target=run, daemon=True, name='totvs-conciliacao').start()
    return True
