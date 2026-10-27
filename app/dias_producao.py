"""Dias sem producao (regra do PCP Hub, agora do Gestao): a maquina NAO roda aos domingos (sempre) e nas datas marcadas pelo PCP.
Feriados e sabados normais continuam produzindo, a menos que o PCP marque a data. O calendario mostra os feriados (nacionais, SP, Jundiai) para ajudar a marcar."""
from datetime import date, timedelta


def pascoa(ano: int) -> date:
    """Domingo de Pascoa (Meeus/Jones/Butcher)."""
    a, b, c = ano % 19, ano // 100, ano % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    mes = (h + l - 7 * m + 114) // 31
    dia = ((h + l - 7 * m + 114) % 31) + 1
    return date(ano, mes, dia)


def feriados(ano: int) -> dict:
    """{'AAAA-MM-DD': {'nome': ..., 'tipo': fixed|moveable|state|municipal|event}} (evento = Copa 2026, nao conta como feriado)."""
    saida = {}
    for md, nome in (('01-01', 'Confraternização Universal'), ('04-21', 'Tiradentes'), ('05-01', 'Dia do Trabalho'), ('09-07', 'Independência do Brasil'), ('10-12', 'N. Sra. Aparecida'),
                     ('11-02', 'Finados'), ('11-15', 'Proclamação da República'), ('11-20', 'Consciência Negra'), ('12-25', 'Natal')):
        saida[f'{ano}-{md}'] = dict(nome=nome, tipo='fixed')
    p = pascoa(ano)
    for dias, nome in ((-48, 'Carnaval (segunda)'), (-47, 'Carnaval (terça)'), (-2, 'Sexta-feira Santa'), (60, 'Corpus Christi')):
        saida[(p + timedelta(days=dias)).isoformat()] = dict(nome=nome, tipo='moveable')
    saida[f'{ano}-07-09'] = dict(nome='Revolução Constitucionalista (SP)', tipo='state')
    saida[f'{ano}-12-08'] = dict(nome='N. Sra. do Desterro — Padroeira de Jundiaí', tipo='municipal')
    saida[f'{ano}-12-14'] = dict(nome='Aniversário de Jundiaí', tipo='municipal')
    if ano == 2026:
        d = date(2026, 6, 11)
        while d <= date(2026, 7, 19):
            saida.setdefault(d.isoformat(), dict(nome='Copa do Mundo FIFA 2026', tipo='event'))
            d += timedelta(days=1)
    return saida


def domingos(inicio: date, fim: date) -> set:
    d = inicio + timedelta(days=(6 - inicio.weekday()) % 7)           # primeiro domingo em ou depois de inicio
    saida = set()
    while d <= fim:
        saida.add(d)
        d += timedelta(days=7)
    return saida
