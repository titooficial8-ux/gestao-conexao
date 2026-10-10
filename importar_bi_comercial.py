"""Importa as tabelas de apoio da planilha de Receitas (RECEITAS_FOL) para o BI COMERCIAL.

Uso:  python importar_bi_comercial.py "caminho/RECEITAS_FOL.xlsx"

Le so as abas pequenas (Tab_aux, CONTROL_0164 e os planos BPLANO / BPLANO_V / BP REP / BP EXPO) e grava
JSON em app/seed_data/bi_comercial/. Os numeros de faturamento NAO vem daqui: vem da API do TOTVS.
Rode de novo quando a area comercial mudar o plano ou os cadastros auxiliares.
"""
import json
import sys
from pathlib import Path

import openpyxl

DESTINO = Path(__file__).resolve().parent / 'app' / 'seed_data' / 'bi_comercial'
ABAS = {'Tab_aux': 'tab_aux', 'CONTROL_0164': 'controle_0164', 'BPLANO': 'bplano', 'BPLANO_V': 'bplano_v', 'BP REP': 'bp_rep', 'BP EXPO': 'bp_expo'}


def _v(x):
    return x.isoformat() if hasattr(x, 'isoformat') else x


def main(caminho):
    wb = openpyxl.load_workbook(caminho, read_only=True, data_only=True)
    DESTINO.mkdir(parents=True, exist_ok=True)
    for aba, nome in ABAS.items():
        linhas = []
        for r in wb[aba].iter_rows(values_only=True):
            linhas.append([_v(x) for x in r[:30]])
        while linhas and all(x is None for x in linhas[-1]):
            linhas.pop()
        (DESTINO / f'{nome}.json').write_text(json.dumps(linhas, ensure_ascii=False), encoding='utf-8')
        print(f'{aba:14s} -> {nome}.json  ({len(linhas)} linhas)')
    # data da planilha (para mostrar "plano de ...")
    (DESTINO / 'origem.json').write_text(json.dumps({'arquivo': Path(caminho).name, 'abas': list(ABAS)}, ensure_ascii=False), encoding='utf-8')


if __name__ == '__main__':
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    main(sys.argv[1])
