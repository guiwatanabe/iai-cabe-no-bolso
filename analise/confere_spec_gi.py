"""Confere os números da Spec da Gi (27/09) contra a reconstrução exata da fatura (docs/01 §3).

Rodar da raiz ou de qualquer lugar: python3 analise/confere_spec_gi.py
"""
from pathlib import Path

import numpy as np
import pandas as pd

RAIZ = Path(__file__).resolve().parents[1]
df = pd.read_csv(RAIZ / 'data/extrato_sintetico.csv.gz', dtype={'id_usuario': str, 'descr': str})
df['descr'] = df['descr'].fillna('')
df['c'] = (df.vlr * 100).round().astype('int64')
df['dia'] = pd.to_datetime(df.anomesdia).dt.day
mi = df.nom_cate_micro

# fatura por mês, regra por modo (igual a grupos_abc.py / persona_b.py)
fat = df[mi == 'Pagamento de fatura'].copy()
fat['modo'] = np.select([fat.descr.str.contains('minimo'), fat.descr.str.contains('parcial')], ['minimo', 'parcial'], 'integral')
fat = fat.set_index(['id_usuario', 'anomes']).sort_index()
fat['juros'] = df[mi == 'Juros pagos'].groupby(['id_usuario', 'anomes']).c.sum().reindex(fat.index).fillna(0).values
fat['F'] = np.select([fat.modo == 'integral', fat.modo == 'minimo'], [fat.c, (fat.c / 0.15).round()], (fat.c + fat.juros / 0.14).round())
fat['nao_pago'] = fat.F - fat.c
cart = df[df.descr.str.startswith('cart credito')].groupby(['id_usuario', 'anomes']).c.sum()
fat['compras'] = cart.reindex(fat.index).fillna(0).values
fat['compras_ant'] = fat.groupby(level=0).compras.shift(1)

ids = fat.index.get_level_values(0).unique()
k = fat[fat.modo != 'integral'].groupby(level=0).size().reindex(ids).fillna(0)
grp = pd.Series(np.select([k >= 6, k >= 3, k >= 1], ['C', 'B', 'A'], 'nunca'), index=ids)
fat['grupo'] = grp.reindex(fat.index.get_level_values(0)).values
rs = lambda c: round(c / 100)

print('1. Juros por cliente no ano (mediana, R$): total | rotativo (meses rolados) | cheque especial (meses integrais)')
for g in ['A', 'B', 'C']:
    x = fat[fat.grupo == g]
    tot = x.groupby(level=0).juros.sum()
    rot = x[x.modo != 'integral'].groupby(level=0).juros.sum().reindex(tot.index).fillna(0)
    che = x[x.modo == 'integral'].groupby(level=0).juros.sum().reindex(tot.index).fillna(0)
    print(f'   {g}: {rs(tot.median())} | {rs(rot.median())} | {rs(che.median())}   '
          f'clientes com juros em mês integral: {(che > 0).mean():.0%}   soma rotativo R$ {rs(rot.sum()):,} | cheque R$ {rs(che.sum()):,}')

print('2. Não pago (reconstrução exata) x spec (fator 1,33)')
spec = {'A': ('7,4%', 'R$ 146 mil', '92,6%'), 'B': ('19,9%', 'R$ 1,59 mi', '80,1%')}
for g in ['A', 'B']:
    x = fat[fat.grupo == g]
    share = x.nao_pago.sum() / x.F.sum()
    print(f'   {g}: não pago {share:.1%} (spec {spec[g][0]}) | R$ {rs(x.nao_pago.sum()):,} no ano (spec {spec[g][1]}) | '
          f'fatura paga no vencimento {1 - share:.1%} (spec {spec[g][2]})')
x = fat[fat.grupo.isin(['A', 'B'])]
print(f'   A+B: R$ {rs(x.nao_pago.sum()):,} não pago no ano (spec: ~R$ 1,7 mi)')

print('3. Fator fatura / compras no cartão do mês anterior')
integ = fat[(fat.modo == 'integral') & (fat.compras_ant > 0)]
print(f'   meses integrais: mediana {(integ.F / integ.compras_ant).median():.2f} (spec 1,33)')
rol = fat[(fat.modo != 'integral') & (fat.compras_ant > 0)]
err = (1.33 * rol.compras_ant - rol.F) / rol.F
print(f'   meses rolados, erro de 1,33 x compras_ant contra a fatura exata: mediana {err.median():+.0%}, '
      f'|erro| mediano {err.abs().median():.0%}, |erro| > 20% em {(err.abs() > 0.2).mean():.0%} dos meses')

print('4. Gasto no cartão no ano por cliente (mediana, R$)')
ano = fat.groupby(level=0).compras.sum()
print('   ', {g: rs(ano[grp == g].median()) for g in ['nunca', 'A', 'B', 'C']}, '(spec: 15,8 a 17,3 mil)')

print('5. Dia do vencimento x rolar a fatura (docs/01 §6: não muda a taxa)')
fat['dia_venc'] = df[mi == 'Pagamento de fatura'].groupby(['id_usuario', 'anomes']).dia.first().reindex(fat.index).values
sal = df[(df.tipo == 'E') & mi.str.contains('Sal', case=False, na=False)]
dia_sal = sal.groupby('id_usuario').dia.median()
fat['dist'] = fat.dia_venc - dia_sal.reindex(fat.index.get_level_values(0)).values
ab = fat[fat.grupo.isin(['A', 'B'])].dropna(subset=['dist'])
ab['faixa'] = pd.cut(ab.dist, [-31, 5, 10, 15, 20, 31])
print('    taxa de fatura rolada por dias entre salário e vencimento (A+B, CLT):',
      ab.groupby('faixa', observed=True).modo.agg(lambda s: f'{(s != "integral").mean():.0%} (n={len(s)})').to_dict())
