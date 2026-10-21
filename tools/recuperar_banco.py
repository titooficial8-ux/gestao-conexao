"""Verifica e recupera o banco SQLite da Gestao ("database disk image is malformed").

Uso (com a Gestao FECHADA):
    venv\Scripts\python tools\recuperar_banco.py                    -> so verifica (integrity_check)
    venv\Scripts\python tools\recuperar_banco.py --recuperar        -> gera gestao_conexao_recuperado.db copiando o que da para ler

O banco e o de GESTAO_DB (no .env) ou gestao_conexao.db na pasta do projeto. O arquivo original nunca e alterado.
Depois de recuperar: feche a Gestao, troque o arquivo velho pelo recuperado (guarde o velho) e abra de novo.
"""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent


def caminho() -> Path:
    try:
        from dotenv import load_dotenv
        load_dotenv(RAIZ / '.env')
    except ImportError:
        pass
    return Path(os.environ.get('GESTAO_DB') or RAIZ / 'gestao_conexao.db')


def verificar(db: Path) -> bool:
    con = sqlite3.connect(f'file:{db.as_posix()}?mode=ro', uri=True)
    try:
        linhas = [r[0] for r in con.execute('PRAGMA integrity_check').fetchall()]
    except sqlite3.DatabaseError as e:
        print('NAO consegui nem verificar:', e)
        return False
    finally:
        con.close()
    if linhas == ['ok']:
        print('Banco OK (integrity_check = ok).')
        return True
    print(f'Banco COM PROBLEMA ({len(linhas)} avisos). Primeiros:')
    for x in linhas[:15]:
        print('  ', x)
    return False


def recuperar(db: Path) -> None:
    saida = db.with_name(db.stem + '_recuperado.db')
    if saida.exists():
        saida.unlink()
    src = sqlite3.connect(f'file:{db.as_posix()}?mode=ro', uri=True)
    dst = sqlite3.connect(saida)
    try:
        esquema = src.execute("SELECT type, name, tbl_name, sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%'").fetchall()
    except sqlite3.DatabaseError as e:
        print('Nao consegui ler nem a estrutura do banco:', e)
        sys.exit(1)
    tabelas = [x for x in esquema if x[0] == 'table']
    for _t, nome, _tb, sql in tabelas:
        dst.execute(sql)
    for _t, nome, _tb, sql in tabelas:
        colunas = [c[1] for c in src.execute(f'PRAGMA table_info("{nome}")').fetchall()]
        marcas = ','.join('?' * len(colunas))
        ok = perdidas = 0
        try:
            cur = src.execute(f'SELECT * FROM "{nome}"')
            while True:
                try:
                    linha = cur.fetchone()
                except sqlite3.DatabaseError:
                    perdidas += 1
                    break
                if linha is None:
                    break
                dst.execute(f'INSERT OR IGNORE INTO "{nome}" VALUES ({marcas})', linha)
                ok += 1
        except sqlite3.DatabaseError:
            pass
        if perdidas:                                                 # leitura em sequencia quebrou: tenta linha a linha pelo rowid
            try:
                maximo = src.execute(f'SELECT max(rowid) FROM "{nome}"').fetchone()[0] or 0
            except sqlite3.DatabaseError:
                maximo = 0
            perdidas = 0
            for rid in range(1, maximo + 1):
                try:
                    linha = src.execute(f'SELECT * FROM "{nome}" WHERE rowid = ?', (rid,)).fetchone()
                except sqlite3.DatabaseError:
                    perdidas += 1
                    continue
                if linha is not None:
                    dst.execute(f'INSERT OR IGNORE INTO "{nome}" VALUES ({marcas})', linha)
            ok = dst.execute(f'SELECT count(*) FROM "{nome}"').fetchone()[0]
        dst.commit()
        print(f'{nome:40s} copiadas {ok:>8}' + (f'   PERDIDAS ~{perdidas}' if perdidas else ''))
    for tipo, nome, _tb, sql in esquema:
        if tipo == 'index':
            try:
                dst.execute(sql)
            except sqlite3.DatabaseError:
                pass
    dst.commit()
    dst.close()
    src.close()
    print(f'\nPronto: {saida}\nFeche a Gestao, guarde o arquivo antigo e coloque o recuperado no lugar (ou aponte GESTAO_DB para ele).')


if __name__ == '__main__':
    arq = caminho()
    print('Banco:', arq)
    if not arq.exists():
        sys.exit('Arquivo nao encontrado.')
    bom = verificar(arq)
    if '--recuperar' in sys.argv:
        recuperar(arq)
    elif not bom:
        print('\nPara recuperar: venv\\Scripts\\python tools\\recuperar_banco.py --recuperar')
