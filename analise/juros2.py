import pandas as pd, numpy as np, itertools
P = '/Users/manasses/Documents/ChatGPT/Batalha de Agentes/'
df = pd.read_csv(P + 'data/extrato_sintetico.csv.gz', dtype={'id_usuario': str, 'descr': str})
df['descr'] = df['descr'].fillna(''); df['c'] = (df.vlr * 100).round().astype('int64'); mi = df.nom_cate_micro
fat = df[mi == 'Pagamento de fatura'].copy()
fat['modo'] = np.select([fat.descr.str.contains('minimo'), fat.descr.str.contains('parcial')], ['minimo', 'parcial'], 'integral')
fat = fat.set_index(['id_usuario', 'anomes'])
fat['tip'] = fat[fat.modo == 'integral'].groupby(level=0).c.median().reindex(fat.index.get_level_values(0)).values
fat['juros'] = df[mi == 'Juros pagos'].groupby(['id_usuario', 'anomes']).c.sum().reindex(fat.index).fillna(0).values
fat['saldo_min'] = df.groupby(['id_usuario', 'anomes']).saldo_apos.min().reindex(fat.index).values
m = fat[fat.modo == 'minimo']; F = m.c / 0.15
print('minimo: juros/(F-pago) com F=pago/0.15 ->', (m.juros / (F - m.c)).describe(percentiles=[.1, .5, .9]).round(4).to_dict())
p = fat[(fat.modo == 'parcial') & fat.tip.notna()]; Fp = p.c + p.juros / 0.14
print('parcial: F estimada (pago + juros/0.14) / fatura tipica ->', (Fp / p.tip).describe(percentiles=[.1, .25, .5, .75, .9]).round(3).to_dict())
print('parcial: fracao paga da fatura estimada ->', (p.c / Fp).describe(percentiles=[.1, .5, .9]).round(3).to_dict())
ig = fat[(fat.modo == 'integral') & (fat.juros > 0)]
print('meses de fatura inteira com juros:', len(ig), '| com saldo negativo no mes:', round((ig.saldo_min < 0).mean(), 3), '| corr(juros, saldo_min):', round(np.corrcoef(ig.juros, ig.saldo_min)[0, 1], 3), '| juros/fatura paga mediana:', round((ig.juros / ig.c).median(), 4))
has = lambda micro: set(df[mi == micro].id_usuario)
ids = fat.index.get_level_values(0).unique()
perf = pd.Series(np.select([ids.isin(has('Beneficio INSS')), ids.isin(has('Pagamento de aluguel')), ids.isin(has('Recebimento Aluguel')), ids.isin(has('Financiamento de imovel'))], ['INSS', 'PIX+aluguel', 'CLT+fin+recebe', 'CLT+fin'], 'CLT s/fin'), index=ids)
runs = fat.reset_index().sort_values(['id_usuario', 'anomes']).groupby('id_usuario').modo.apply(lambda s: max((len(list(g)) for kk, g in itertools.groupby(s != 'integral') if kk), default=0))
print('maior sequencia de meses seguidos rolando, mediana por perfil:', runs.groupby(perf.reindex(runs.index)).median().to_dict(), '| max:', runs.groupby(perf.reindex(runs.index)).max().to_dict())
