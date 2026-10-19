"""Servidor de producao (VPS): sem modo debug, com Waitress, e com os agendadores ligados (pesagens, pedidos, BI, e-mail das 08h).

Uso (na pasta do projeto, com a venv ativa):
    python run_producao.py

Variaveis opcionais no .env: PORTA (padrao 5000), THREADS (padrao 16).
"""
import os

os.environ.setdefault('BI_AGENDADOR', '1')          # sem o reloader do modo debug, os agendadores sobem por esta variavel

from dotenv import load_dotenv

load_dotenv()

from waitress import serve

from app import create_app

app = create_app()

if __name__ == '__main__':
    porta = int(os.getenv('PORTA', '5000'))
    print(f'Gestao Conexao em producao: http://0.0.0.0:{porta}', flush=True)
    serve(app, host='0.0.0.0', port=porta, threads=int(os.getenv('THREADS', '16')), max_request_body_size=int(2.2 * 1024 ** 3))      # uploads de ate 2 GB
