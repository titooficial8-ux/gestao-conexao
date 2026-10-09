"""Slide mensal de receita (replica da aba "2 - SLIDE_LIQ" da planilha de Receitas).

Entrada: linhas derivadas (base.derivar) + planos importados (BPLANO, BPLANO_V, BP REP).
Cada bloco devolve linhas [rotulo, ano-1, plano, realizado, %vs-ano-anterior, %vs-plano, acum-ano-1, acum-plano, acum-realizado, %, %]
exatamente como o slide do Edmar, para qualquer mes/ano.
"""
from __future__ import annotations

from collections import defaultdict
from functools import lru_cache

from app.totvs import base

SEGMENTOS_RS = ['AUTOMOTIVO', 'CALÇADO', 'COLCHÃO', 'COLCHÃO M.E.', 'FACÇÃO', 'FACÇÃO AUT', 'VESTUARIO', 'MOVELEIRO', 'ATIVO',
                'REVENDA FIO', 'SEGUNDA QUALIDADE', 'SUCATA', 'SUCATA PAPELAO']
SEGMENTOS_MT = ['AUTOMOTIVO', 'CALÇADO', 'COLCHÃO', 'COLCHÃO M.E.', 'MOVELEIRO']
SEGMENTOS_KG = ['FACÇÃO', 'FACÇÃO AUT', 'VESTUARIO', 'COLCHÃO']
SEGMENTOS_PM = ['AUTOMOTIVO', 'CALÇADO', 'COLCHÃO', 'COLCHÃO M.E.', 'FACÇÃO', 'FACÇÃO AUT', 'VESTUARIO']
REPRESENTANTES = ['DIRETO', 'FABIANO', 'RENATO REPRES', 'ARIEROM', 'CEZANNE', 'GILBERTO LIRA', 'JEMA', 'MAJOV REPRES.', 'MARCOS MARTINS',
                  'MORAES REPRESENTACOE', 'MARCO ANTONIO ROQUE MOVEIS', 'PDS REPRESENTACAO COMERCIAL LTDA', 'F FRANCO REPRES.']
FILIAIS_ORTOBOM = ['AMAZONAS', 'BAIANA', 'BELEM', 'CEARENSE', 'CENTRO OESTE', 'CONTAGEM', 'CUIABA', 'D JUAN', 'NORTE PARANAENSE',
                   'OLINDA', 'QUEIMADOS', 'RIO GRANDENSE', 'RIO SUL']
PAISES = ['URUGUAI', 'PARAGUAI', 'CHILE', 'BOLIVIA', 'PERU', 'EQUADOR', 'COLOMBIA', 'VENEZUELA', 'ESTADOS UNIDOS', 'AMERICA CENTRAL',
          'ARGENTINA', 'COMEX']


def _var(a, b):
    """=SE(b=0;0;SE(a=0;0;a/b-1)) como na planilha."""
    return 0 if (not b or not a) else a / b - 1


@lru_cache(maxsize=1)
def planos():
    ler = base._ler
    bp = ler('bplano')
    bpv = ler('bplano_v')
    bpr = ler('bp_rep')
    g = lambda r, i: r[i] if i < len(r) else None
    seg = defaultdict(lambda: dict(val=0, acum=0, mt=0, mt_acum=0))      # BPLANO A,B,C,F,H,I (valor em R$ mil)
    orto = {}                                                              # BPLANO L,M,N,O
    for r in bp[2:]:
        if g(r, 0) and isinstance(g(r, 1), (int, float)):
            seg[(base._k(g(r, 0)), int(g(r, 1)))] = dict(val=(g(r, 2) or 0) * 1000, acum=(g(r, 5) or 0) * 1000, mt=g(r, 7) or 0, mt_acum=g(r, 8) or 0)
        if isinstance(g(r, 11), (int, float)) and isinstance(g(r, 12), (int, float)):
            orto[(int(g(r, 11)), int(g(r, 12)))] = dict(mt=g(r, 13) or 0, acum=g(r, 14) or 0)
    # BPLANO_V: metros acumulados por segmento (A mes,B ano,C seg,G metros,H metros acum) e paises (P mes,Q ano,R pais,S,T)
    seg_v, pais = {}, {}
    for r in bpv[3:]:
        if g(r, 2) and isinstance(g(r, 0), (int, float)):
            seg_v[(base._k(g(r, 2)), int(g(r, 0)), int(g(r, 1) or 0))] = dict(mt=g(r, 6) or 0, mt_acum=g(r, 7) or 0)
        if g(r, 17) and isinstance(g(r, 15), (int, float)):
            pais[(base._k(g(r, 17)), int(g(r, 15)), int(g(r, 16) or 0))] = dict(val=g(r, 18) or 0, acum=g(r, 19) or 0)
    rep = {}
    for r in bpr[1:]:
        if g(r, 2) and isinstance(g(r, 3), (int, float)):
            k = (base._k(g(r, 2)), int(g(r, 3)))
            a = rep.setdefault(k, dict(mt=0, acum=0))
            a['mt'] += g(r, 4) if isinstance(g(r, 4), (int, float)) else 0
            a['acum'] += g(r, 5) if isinstance(g(r, 5), (int, float)) else 0
    return dict(seg=seg, orto=orto, seg_v=seg_v, pais=pais, rep=rep)


def montar(regs: list[dict], mes: int, ano: int) -> dict:
    """regs = lista de base.derivar(...). Devolve o slide do mes/ano."""
    P = planos()
    aj = base.ajustes().get('constantes_slide', {})
    ocultos = {base._k(x) for x in (base.ajustes().get('segmentos_ocultos') or {}).get('lista', [])}
    regs = [x for x in regs if base._k(x['seg']) not in ocultos]            # ex.: CALCADO e MOVELEIRO ficam fora de tudo
    ant = ano - 1

    def soma(campo, **f):
        t = 0.0
        for x in regs:
            if (f.get('ano') is not None and x['ano'] != f['ano']) or (f.get('mes') is not None and x['mes'] != f['mes']):
                continue
            if f.get('ate') is not None and not (x['mes'] and x['mes'] <= f['ate']):
                continue
            if f.get('seg') is not None and x['seg'] != f['seg']:
                continue
            if f.get('esp') is not None and x['esp'] != f['esp']:
                continue
            if f.get('rep') is not None and x['rep'] != f['rep']:
                continue
            if f.get('fant') is not None and x['fantasia'] != f['fantasia' if False else 'fant']:
                continue
            if f.get('ext') is not None and x['ext'] != f['ext']:
                continue
            if f.get('tipo') is not None and x['tipo'] != f['tipo']:
                continue
            t += x[campo]
        return t

    def linha(rot, a_ant, plano, real, y_ant, y_plano, y_real):
        return dict(rotulo=rot, mes=dict(ant=a_ant, plano=plano, real=real, v_ant=_var(real, a_ant), v_plano=_var(real, plano)),
                    acum=dict(ant=y_ant, plano=y_plano, real=y_real, v_ant=_var(y_real, y_ant), v_plano=_var(y_real, y_plano)))

    def totalizar(rot, linhas, plano_total=None):
        s = lambda b, k: sum(x[b][k] for x in linhas)
        return linha(rot, s('mes', 'ant'), s('mes', 'plano'), s('mes', 'real'), s('acum', 'ant'), s('acum', 'plano'), s('acum', 'real'))

    # ---- receita (R$ liquida) por segmento
    rs = []
    for seg in SEGMENTOS_RS:
        p = P['seg'].get((base._k(seg), mes), {})
        rs.append(linha(seg, soma('liquida', mes=mes, ano=ant, seg=seg), p.get('val', 0), soma('liquida', mes=mes, ano=ano, seg=seg),
                        soma('liquida', ate=mes, ano=ant, seg=seg), p.get('acum', 0), soma('liquida', ate=mes, ano=ano, seg=seg)))
    tot_rs = totalizar('TOTAL R$', rs)
    bruta = soma('bruta', mes=mes, ano=ano)
    dev_b, dev_l = -soma('bruta', mes=mes, ano=ano, tipo='DEV'), -soma('liquida', mes=mes, ano=ano, tipo='DEV')
    real_liq = tot_rs['mes']['real']
    cab = dict(receita_bruta=bruta, devolucao_bruta=dev_b, devolucao_liquida=dev_l, receita_liquida=real_liq,
               deducoes=bruta - real_liq, deducoes_pct=(bruta - real_liq) / bruta if bruta else 0,
               liquida_sem_devolucao=real_liq - dev_l,
               a_atingir_meta=tot_rs['mes']['plano'] - real_liq + (aj.get('valor_a_atingir_meta_soma') or 0))

    # ---- quantidades em metros / quilos
    def quant(lista, esp):
        out = []
        for seg in lista:
            p = P['seg'].get((base._k(seg), mes), {}) if esp == 'MT' else {}      # plano so existe em metros
            pv = P['seg_v'].get((base._k(seg), mes, ano), {}) if esp == 'MT' else {}
            out.append(linha(seg, soma('quant_raw', mes=mes, ano=ant, seg=seg, esp=esp), p.get('mt', 0), soma('quant', mes=mes, ano=ano, seg=seg, esp=esp),
                             soma('quant', ate=mes, ano=ant, seg=seg, esp=esp), pv.get('mt_acum', 0), soma('quant', ate=mes, ano=ano, seg=seg, esp=esp)))
        return out
    mt = quant(SEGMENTOS_MT, 'MT')
    kg = quant(SEGMENTOS_KG, 'KG')
    tot_mt, tot_kg = totalizar('TOTAL MTS', mt), totalizar('TOTAL KGS', kg)

    # ---- preco medio liquido (R$/MT)
    rs_por = {x['rotulo']: x for x in rs}
    mt_por = {x['rotulo']: x for x in mt}
    kg_por = {x['rotulo']: x for x in kg}
    pm = []
    for seg in SEGMENTOS_PM:
        a, b = rs_por.get(seg), mt_por.get(seg) or kg_por.get(seg)
        if not a or not b:
            pm.append(linha(seg, 0, 0, 0, 0, 0, 0)); continue
        d = lambda x, y: x / y if y else 0
        pm.append(linha(seg, d(a['mes']['ant'], b['mes']['ant']), d(a['mes']['plano'], b['mes']['plano']), d(a['mes']['real'], b['mes']['real']),
                        d(a['acum']['ant'], b['acum']['ant']), d(a['acum']['plano'], b['acum']['plano']), d(a['acum']['real'], b['acum']['real'])))

    # ---- representantes (metros de colchao)
    reps = []
    sub = aj.get('acumulado_representantes_subtrai') or 0
    for i, nome in enumerate(REPRESENTANTES):
        p = P['rep'].get((base._k(nome), mes), {})
        reps.append(linha(nome, soma('quant_raw', mes=mes, ano=ant, rep=nome, esp='MT'), p.get('mt', 0), soma('quant', mes=mes, ano=ano, rep=nome, esp='MT'),
                          soma('quant', ate=mes, ano=ant, rep=nome, esp='MT'), p.get('acum', 0), soma('quant', ate=mes, ano=ano, rep=nome, esp='MT')))
    tot_reps = totalizar('TOTAL MTS - COLCHÃO', reps)

    # ---- Ortobom por filial (metros)
    orto = []
    for nome in FILIAIS_ORTOBOM:
        orto.append(dict(rotulo=nome, mes=dict(ant=soma('quant_raw', mes=mes, ano=ant, fant=nome, esp='MT'), real=soma('quant', mes=mes, ano=ano, fant=nome, esp='MT')),
                         acum=dict(ant=soma('quant', ate=mes, ano=ant, fant=nome, esp='MT'), real=soma('quant', ate=mes, ano=ano, fant=nome, esp='MT'))))
    for x in orto:
        for k in ('mes', 'acum'):
            x[k]['var'] = _var(x[k]['real'], x[k]['ant'])
    po = P['orto'].get((mes, ano), {})
    orto_tot = dict(rotulo='TOTAL EM METROS', mes=dict(ant=sum(x['mes']['ant'] for x in orto), real=sum(x['mes']['real'] for x in orto)),
                    acum=dict(ant=sum(x['acum']['ant'] for x in orto), real=sum(x['acum']['real'] for x in orto)))
    for k in ('mes', 'acum'):
        orto_tot[k]['var'] = _var(orto_tot[k]['real'], orto_tot[k]['ant'])
    orto_plano = dict(rotulo='PLANO', mes=dict(real=po.get('mt', 0), var=_var(orto_tot['mes']['real'], po.get('mt', 0))),
                      acum=dict(real=po.get('acum', 0), var=_var(orto_tot['acum']['real'], po.get('acum', 0))))

    # ---- mercado internacional (R$ liquida por pais)
    intl = []
    for pais in PAISES:
        pv = P['pais'].get((base._k(pais), mes, ano), {})
        real = soma('liquida', mes=mes, ano=ano, ext=pais.title() if False else pais)
        # o pais na Tab_aux vem em MAIUSCULAS (ex.: PARAGUAI); o PROCV nao diferencia caixa
        real = sum(x['liquida'] for x in regs if x['mes'] == mes and x['ano'] == ano and base._k(x['ext']) == base._k(pais))
        yreal = sum(x['liquida'] for x in regs if x['mes'] and x['mes'] <= mes and x['ano'] == ano and base._k(x['ext']) == base._k(pais))
        intl.append(dict(rotulo=pais, mes=dict(plano=pv.get('val', 0), real=real, var=_var(real, pv.get('val', 0))),
                         acum=dict(plano=pv.get('acum', 0), real=yreal, var=_var(yreal, pv.get('acum', 0)))))
    it = dict(rotulo='TOTAL R$', mes=dict(plano=sum(x['mes']['plano'] for x in intl), real=sum(x['mes']['real'] for x in intl)),
              acum=dict(plano=sum(x['acum']['plano'] for x in intl), real=sum(x['acum']['real'] for x in intl)))
    for k in ('mes', 'acum'):
        it[k]['var'] = _var(it[k]['real'], it[k]['plano'])

    # ---- serie mensal (grafico): receita liquida total por mes, ano-1 x ano x plano
    segs = set(SEGMENTOS_RS)
    por = defaultdict(float)
    for x in regs:
        if x['seg'] in segs and x['mes'] and x['ano'] in (ano, ant):
            por[(x['ano'], x['mes'])] += x['liquida']
    serie = [dict(mes=m, ant=por.get((ant, m), 0), real=por.get((ano, m), 0),
                  plano=sum(P['seg'].get((base._k(sg), m), {}).get('val', 0) for sg in SEGMENTOS_RS)) for m in range(1, 13)]

    return dict(serie_mensal=serie, mes=mes, ano=ano, ano_ant=ant, cabecalho=cab, receita=rs, receita_total=tot_rs, metros=mt, metros_total=tot_mt, quilos=kg, quilos_total=tot_kg,
                preco_medio=pm, representantes=reps, representantes_total=tot_reps, ortobom=orto, ortobom_total=orto_tot, ortobom_plano=orto_plano,
                internacional=intl, internacional_total=it)
