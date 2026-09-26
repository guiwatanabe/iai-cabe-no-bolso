import pandas as pd, numpy as np
P = '/Users/manasses/Documents/ChatGPT/Batalha de Agentes/'
df = pd.read_csv(P + 'data/extrato_sintetico.csv.gz', dtype={'id_usuario': str, 'descr': str})
df['descr'] = df['descr'].fillna(''); df['c'] = (df.vlr * 100).round().astype('int64'); mi = df.nom_cate_micro
fat = df[mi == 'Pagamento de fatura'].copy()
fat['modo'] = np.select([fat.descr.str.contains('minimo'), fat.descr.str.contains('parcial')], ['minimo', 'parcial'], 'integral')
fat = fat.set_index(['id_usuario', 'anomes']).sort_index()
fat['juros'] = df[mi == 'Juros pagos'].groupby(['id_usuario', 'anomes']).c.sum().reindex(fat.index).fillna(0).values
fat['saldo_min'] = df.groupby(['id_usuario', 'anomes']).saldo_apos.min().reindex(fat.index).values
fat['F'] = np.select([fat.modo == 'integral', fat.modo == 'minimo'], [fat.c, fat.c / 0.15], fat.c + fat.juros / 0.14)
fat['nao_pago'] = fat.F - fat.c
has = lambda micro: set(df[mi == micro].id_usuario)
ids = fat.index.get_level_values(0).unique()
perf = pd.Series(np.select([ids.isin(has('Beneficio INSS')), ids.isin(has('Pagamento de aluguel')), ids.isin(has('Recebimento Aluguel')), ids.isin(has('Financiamento de imovel'))], ['INSS', 'PIX+aluguel', 'CLT+fin+recebe', 'CLT+fin'], 'CLT s/fin'), index=ids)
k = fat[fat.modo != 'integral'].groupby(level=0).size().reindex(ids).fillna(0)
grp = pd.Series(np.select([k >= 6, k >= 3, k >= 1], ['C (6+)', 'B (3-5)', 'A (1-2)'], 'nunca'), index=ids)
print(pd.crosstab(perf, grp, margins=True))
ju = fat.groupby(level=0).juros.sum() / 100
print('juros no ano por grupo (R$ total | mediana por cliente):', {g: (round(ju[grp == g].sum()), round(ju[grp == g].median())) for g in ['A (1-2)', 'B (3-5)', 'C (6+)']})
ig = fat[(fat.modo == 'integral') & (fat.juros > 0) & (fat.saldo_min < 0)]
r = (ig.juros / 100) / (-ig.saldo_min)
print('cheque especial: juros / saldo negativo minimo ->', r.describe(percentiles=[.1, .25, .5, .75, .9]).round(4).to_dict())
f = fat.reset_index(); f['F_prox'] = f.groupby('id_usuario').F.shift(-1); f['Fmed'] = f.groupby('id_usuario').F.transform('median')
f = f.dropna(subset=['F_prox'])
print('fatura do mes seguinte / fatura mediana do cliente, por modo do mes atual:', f.groupby('modo').apply(lambda g: round((g.F_prox / g.Fmed).median(), 3)).to_dict())
p = f[f.modo != 'integral']
print('corr(nao pago no mes, fatura seguinte - mediana):', round(np.corrcoef(p.nao_pago, p.F_prox - p.Fmed)[0, 1], 3))
