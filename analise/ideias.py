import pandas as pd, numpy as np
P = '/Users/manasses/Documents/ChatGPT/Batalha de Agentes/'
df = pd.read_csv(P + 'data/extrato_sintetico.csv.gz', dtype={'id_usuario': str, 'descr': str})
df['descr'] = df['descr'].fillna(''); df['c'] = (df.vlr * 100).round().astype('int64')
fat = df[df.nom_cate_micro == 'Pagamento de fatura'].copy(); fat['parc'] = ~fat.descr.str.contains('integral')
pm = set(map(tuple, fat[fat.parc][['id_usuario', 'anomes']].values)); pay = set(fat[fat.parc].id_usuario); allu = set(df.id_usuario)
jur = df[df.nom_cate_micro == 'Juros pagos'].copy(); jur['mes_parc'] = [(u, a) in pm for u, a in zip(jur.id_usuario, jur.anomes)]
print('JUROS R$ em meses com parcial?', (jur.groupby('mes_parc').c.sum() / 100).round(0).to_dict())
print('JUROS por tipo:', (jur.groupby(['descr', 'mes_parc']).c.sum() / 100).round(0).to_dict())
inst = set(df[df.parcela_total > 1].id_usuario)
print('tem compra parcelada: pagadores parcial', len(pay & inst), '/', len(pay), '| demais', len((allu - pay) & inst), '/', len(allu - pay))
im = df[df.parcela_total > 1].groupby(['id_usuario', 'anomes']).c.sum()
fi = fat.set_index(['id_usuario', 'anomes']); fi['inst'] = im.reindex(fi.index).fillna(0) / 100
f = fi[fi.index.get_level_values(0).isin(pay)]
print('parcelas no mes R$ mediana: meses parciais', f[f.parc].inst.median(), '| integrais', f[~f.parc].inst.median())
cat = df[df.tipo == 'S'].groupby(['id_usuario', 'nom_cate_macro']).c.sum().unstack(fill_value=0) / 100
g = cat.index.isin(list(pay)); comp = pd.DataFrame({'parcial_689': cat[g].median(), 'demais_311': cat[~g].median()})
comp['razao'] = (comp.parcial_689 / comp.demais_311).round(2); print(comp.round(0).sort_values('razao').to_string())
mr = set(map(tuple, df[df.nom_cate_micro == 'Manutencao e reparo'][['id_usuario', 'anomes']].values))
fr = fat.copy()
fr['mr_same'] = [(u, a) in mr for u, a in zip(fr.id_usuario, fr.anomes)]
fr['mr_prev'] = [(u, a - 1) in mr if a % 100 > 1 else False for u, a in zip(fr.id_usuario, fr.anomes)]
print('taxa parcial: mes c/ oficina', round(fr[fr.mr_same].parc.mean(), 3), int(fr.mr_same.sum()), '| mes apos oficina', round(fr[fr.mr_prev].parc.mean(), 3), int(fr.mr_prev.sum()), '| geral', round(fr.parc.mean(), 3))
big = df[df.descr.str.startswith('cart credito')].groupby(['id_usuario', 'anomes']).c.sum()
p90 = big.groupby(level=0).transform(lambda s: s.quantile(.9))
top = set(big[big >= p90].index)
fr['big_prev'] = [(u, a - 1) in top if a % 100 > 1 else False for u, a in zip(fr.id_usuario, fr.anomes)]
print('taxa parcial no mes apos gasto no cartao >= p90 do cliente:', round(fr[fr.big_prev].parc.mean(), 3), int(fr.big_prev.sum()))
