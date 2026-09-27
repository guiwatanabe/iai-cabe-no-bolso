"""Numeros das duas personas da demo pelas regras do PRD 1.0 (docs/09-prd.html), sem olhar o futuro.

Motor de capacidade: janela de 3 meses fechados antes do mes da fatura.
  renda_recorrente = mediana mensal de Salario CLT + Beneficio INSS (13o, PLR sao esporadicos: flag, nao renda)
  pix_recorrente   = PIX (Recebimentos diversos) presente nos 3 meses da janela, variacao < 30% e >= 10% da renda fixa
                     (parametro pix_recorrente.minimo_pct_renda): vira a pergunta certa
                     ("esse PIX que entra todo mes e renda?"); so entra na renda depois do sim (contar_pix=True)
  fixos            = mediana mensal das subcategorias FIXOS (mesma lista de analise/persona_b.py)
  essenciais       = mediana mensal das macros ESSENCIAIS pagas na conta (fora do cartao e fora das subcategorias FIXOS)
  folga            = renda_recorrente - fixos - essenciais
  fatura           = reconstrucao exata do mes (integral: pago; minimo: pago/0,15; parcial: pago + juros/0,14)
  falta            = fatura - folga  (cabe se <= 0)
  tipo_falta       = pontual se dias_ate_recebimento <= 25 e falta <= folga; senao estrutural
Grupo pelas faturas roladas nos 12 meses anteriores MAIS a rolada do gatilho (pagar abaixo do total e uma rolada):
  1-2 escorregao, 3-5 rolando, 6+ no_limite. Sem contar o gatilho, nenhum primeiro escorregao existiria.
Caminho: escorregao+pontual -> cobertura_curta; escorregao+estrutural ou rolando -> parcelamento; no_limite -> nenhum.
Taxas de config/taxas.yaml. Parcela pela tabela Price sobre a falta; vale a mais barata cujo valor <= folga.

Rodar: python3 analise/numeros_prd.py
"""
from pathlib import Path

import pandas as pd
import yaml

RAIZ = Path(__file__).resolve().parents[1]
TAXAS = yaml.safe_load(open(RAIZ / 'config/taxas.yaml'))
df = pd.read_csv(RAIZ / 'data/extrato_sintetico.csv.gz', dtype={'id_usuario': str, 'descr': str})
df['descr'] = df['descr'].fillna('')
df['c'] = (df.vlr * 100).round().astype('int64')
df['dia'] = pd.to_datetime(df.anomesdia).dt.day
mi, ma = df.nom_cate_micro, df.nom_cate_macro
FIXOS = ['Mensalidade escolar', 'Condominio', 'Seguro de automovel', 'Energia eletrica', 'Agua e esgoto', 'Gas',
         'TV Internet celular e telefone', 'Celular', 'Emprestimos', 'Outros emprestimos', 'Consorcio',
         'Pagamento de aluguel', 'Financiamento de imovel']
ESSENCIAIS = ['Mercado', 'Posto de combustivel', 'Transporte publico', 'Cuidados pessoais', 'Educacao', 'Pets', 'Casa']
cartao = df.descr.str.startswith('cart credito')
PERSONAS = [('755627ab-804b-4211-b0ea-f4ebacc58716', 202508, 'Escorregao'),
            ('8dc79559-e45a-46bd-bd8d-a9b818642251', 202508, 'Escorregao (reserva)'),
            ('3e7d20b2-4c4f-450a-bbd2-e60bfda81f0b', 202509, 'Rolando')]
brl = lambda c: f"R$ {c / 100:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')


def price(principal, taxa, n):
    return round(principal * taxa / (1 - (1 + taxa) ** -n))


def meses_antes(anomes, n=3):
    a, m = divmod(anomes, 100)
    out = []
    for _ in range(n):
        m -= 1
        if m == 0:
            a, m = a - 1, 12
        out.append(a * 100 + m)
    return sorted(out)


def motor(uid, anomes, contar_pix):
    u = df[df.id_usuario == uid]
    jan = meses_antes(anomes)
    w = u[u.anomes.isin(jan)]
    mensal = lambda mask: w[mask.loc[w.index]].groupby('anomes').c.sum().reindex(jan).fillna(0)
    pix = mensal((df.tipo == 'E') & (ma == 'Recebimentos diversos'))
    renda_fixa = int(mensal(mi.isin(['Salario CLT', 'Beneficio INSS'])).median())
    pix_recorrente = bool((pix > 0).all() and pix.std() / pix.mean() < 0.30 and pix.median() >= 0.10 * renda_fixa)
    renda = renda_fixa + (int(pix.median()) if contar_pix else 0)
    fixos = int(mensal(mi.isin(FIXOS)).median())
    essenciais = int(mensal(ma.isin(ESSENCIAIS) & ~mi.isin(FIXOS) & ~cartao).median())
    folga = renda - fixos - essenciais
    f = u[(mi.loc[u.index] == 'Pagamento de fatura') & (u.anomes == anomes)].iloc[0]
    modo = 'minimo' if 'minimo' in f.descr else 'parcial' if 'parcial' in f.descr else 'integral'
    juros = int(u[(mi.loc[u.index] == 'Juros pagos') & (u.anomes == anomes)].c.sum())
    fatura = int(f.c) if modo == 'integral' else round(f.c / 0.15) if modo == 'minimo' else round(f.c + juros / 0.14)
    sal = u[mi.loc[u.index] == 'Salario CLT']
    dia_sal, dia_venc = int(sal.dia.median()), int(f.dia)
    dias = dia_sal - dia_venc if dia_sal > dia_venc else 30 - dia_venc + dia_sal
    falta = fatura - folga
    cabe = falta <= 0
    tipo = 'nenhuma' if cabe else 'pontual' if dias <= 25 and falta <= folga else 'estrutural'
    fat_ant = u[(mi.loc[u.index] == 'Pagamento de fatura') & (u.anomes < anomes)]
    roladas = int(fat_ant.descr.str.contains('minimo|parcial').sum()) + 1  # + a rolada do gatilho
    grupo = 'escorregao' if roladas <= 2 else 'rolando' if roladas <= 5 else 'no_limite'
    caminho = 'nenhum' if cabe or grupo == 'no_limite' else 'cobertura_curta' if grupo == 'escorregao' and tipo == 'pontual' else 'parcelamento'
    compras_mes = int(u[cartao.loc[u.index] & (u.anomes == anomes)].c.sum())
    compras_ant = [int(u[cartao.loc[u.index] & (u.anomes == m)].c.sum()) for m in jan]
    return dict(uid=uid, anomes=anomes, janela=jan, pix=pix, pix_recorrente=pix_recorrente, contar_pix=contar_pix, renda=renda,
                dia_sal=dia_sal, fixos=fixos, essenciais=essenciais, folga=folga, fatura=fatura, modo=modo, pago=int(f.c),
                juros_reais=juros, nao_pago_real=fatura - int(f.c), minimo=round(fatura * TAXAS['rotativo']['minimo_pct_fatura']),
                dia_venc=dia_venc, dias=dias, falta=falta, cabe=cabe, tipo=tipo, roladas=roladas, grupo=grupo, caminho=caminho,
                compras_mes=compras_mes, compras_ant=compras_ant)


def imprimir(r, rotulo):
    print(f"\n== {rotulo} · {r['uid'][:8]} · {r['anomes']} · janela {r['janela']} · pix_recorrente={r['pix_recorrente']} · contar_pix={r['contar_pix']}")
    print(f"renda recorrente {brl(r['renda'])} (salario dia {r['dia_sal']}; PIX mediano na janela {brl(int(r['pix'].median()))}) · fixos {brl(r['fixos'])} · essenciais {brl(r['essenciais'])} · folga {brl(r['folga'])}")
    print(f"fatura {brl(r['fatura'])} ({r['modo']}; minimo {brl(r['minimo'])}) · vence dia {r['dia_venc']} · {r['dias']} dias ate o salario · compras no cartao na janela {[brl(c) for c in r['compras_ant']]} e no mes {brl(r['compras_mes'])}")
    print(f"o que aconteceu de verdade: pagou {brl(r['pago'])}; nao pago {brl(r['nao_pago_real'])}; juros pagos no mes {brl(r['juros_reais'])} (14% x nao pago = {brl(round(r['nao_pago_real'] * 0.14))})")
    print(f"falta {brl(r['falta'])} · cabe {r['cabe']} · tipo {r['tipo']} · roladas 12m incl. gatilho {r['roladas']} · grupo {r['grupo']} · caminho {r['caminho']}")
    falta, folga, dias = r['falta'], r['folga'], r['dias']
    if r['caminho'] == 'cobertura_curta':
        ce = TAXAS['cheque_especial']
        print(f"  cobertura curta: {brl(falta)} por {dias} dias · a {ce['taxa_mes_base'] * 100:.1f}% a.m. (base) = {brl(round(falta * ce['taxa_mes_base'] * dias / 30))} · no teto legal {ce['teto_mes'] * 100:.0f}% a.m. = {brl(round(falta * ce['teto_mes'] * dias / 30))}")
        print(f"  continuar no rotativo 1 mes sobre a falta: {brl(round(falta * TAXAS['rotativo']['taxa_mes']))} · teto do cartao ate a proxima fatura = folga - falta = {brl(folga - falta)}")
    if r['caminho'] == 'parcelamento':
        for prod in ('consignado_clt', 'credito_pessoal', 'parcelamento_fatura'):
            t = TAXAS[prod]
            ops = [(n, price(falta, t['taxa_mes'], n)) for n in t['prazos']]
            print(f"  {prod} {t['taxa_mes'] * 100:.1f}% a.m. ({t['status']}): " + ' · '.join(f"{n}x {brl(p)} juros {brl(p * n - falta)}{' cabe' if p <= folga else ''}" for n, p in ops))
        print(f"  continuar no rotativo: 1 mes {brl(round(falta * TAXAS['rotativo']['taxa_mes']))} · ate o teto legal de 100% {brl(falta)}")
    print(f"proxima fatura projetada (1,33 x compras do mes): {brl(round(1.33 * r['compras_mes']))}")


for uid, anomes, rotulo in PERSONAS:
    r = motor(uid, anomes, contar_pix=False)
    imprimir(r, rotulo)
    if r['pix_recorrente']:
        imprimir(motor(uid, anomes, contar_pix=True), rotulo + ' com PIX confirmado como renda')
