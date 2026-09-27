import pandas as pd, numpy as np, itertools
P = '/Users/manasses/Documents/ChatGPT/Batalha de Agentes/'
df = pd.read_csv(P + 'data/extrato_sintetico.csv.gz', dtype={'id_usuario': str, 'descr': str})
df['descr'] = df['descr'].fillna(''); df['c'] = (df.vlr * 100).round().astype('int64'); mi = df.nom_cate_micro
fat = df[mi == 'Pagamento de fatura'].copy()
fat['modo'] = np.select([fat.descr.str.contains('minimo'), fat.descr.str.contains('parcial')], ['minimo', 'parcial'], 'integral')
fat = fat.set_index(['id_usuario', 'anomes'])
fat['tip'] = fat[fat.modo == 'integral'].groupby(level=0).c.median().reindex(fat.index.get_level_values(0)).values
jm = df[mi == 'Juros pagos'].groupby(['id_usuario', 'anomes']).c.sum()
fat['juros'] = jm.reindex(fat.index).fillna(0).values
fat['saldo_min'] = (df.groupby(['id_usuario', 'anomes']).saldo_apos.min() * 100).reindex(fat.index).values
for modo in ['minimo', 'parcial']:
    f = fat[(fat.modo == modo) & fat.tip.notna()]; r = f.c / f.tip; un = (f.tip - f.c).clip(lower=100)
    q = lambda s: f'mediana {s.median():.3f} | p10 {s.quantile(.1):.3f} | p90 {s.quantile(.9):.3f}'
    print(f'[{modo}] n={len(f)} pago/fatura tipica: {q(r)}')
    print(f'   juros/(tipica-pago): {q(f.juros / un)} | juros/pago: {q(f.juros / f.c)}')
    print(f'   corr(juros, nao pago)={np.corrcoef(f.juros, un)[0, 1]:.3f} | corr(juros, pago)={np.corrcoef(f.juros, f.c)[0, 1]:.3f} | corr(juros, saldo_min)={np.corrcoef(f.juros, f.saldo_min)[0, 1]:.3f}')
modo = fat.modo.to_dict(); j = df[mi == 'Juros pagos']
j = j.assign(modo=[modo.get((u, a), 'sem') for u, a in zip(j.id_usuario, j.anomes)])
print(pd.crosstab(j.descr, j.modo))
ig = fat[(fat.modo == 'integral') & (fat.juros > 0)]
prev = [modo.get((u, a - 1), 'jan') if a % 100 > 1 else 'jan' for u, a in ig.index]
print('meses com fatura inteira e juros:', len(ig), '| modo no mes anterior:', pd.Series(prev).value_counts().to_dict(), '| juros mediano R$', ig.juros.median() / 100)
print('pagamentos parcial/minimo sobre todos:', round((fat.modo != 'integral').mean(), 4))
k = fat[fat.modo != 'integral'].groupby(level=0).size()
print('clientes com >= N meses parcial/minimo:', {n: int((k >= n).sum()) for n in range(1, 10)})
runs = fat.reset_index().sort_values(['id_usuario', 'anomes']).groupby('id_usuario').modo.apply(lambda s: max((len(list(g)) for kk, g in itertools.groupby(s != 'integral') if kk), default=0))
print('clientes com >= N meses seguidos:', {n: int((runs >= n).sum()) for n in range(2, 8)})
card = df.descr.str.startswith('cart credito')
print('compras no cartao aparecem no extrato:', int(card.sum()), 'linhas, R$', round(df[card].c.sum() / 1e8, 2), 'mi | pagamentos de fatura R$', round(df[mi == 'Pagamento de fatura'].c.sum() / 1e8, 2), 'mi')
