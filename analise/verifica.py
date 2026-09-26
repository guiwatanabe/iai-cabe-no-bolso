import pandas as pd, numpy as np
df = pd.read_csv('/Users/manasses/Documents/ChatGPT/Batalha de Agentes/data/extrato_sintetico.csv.gz', dtype={'id_usuario': str, 'descr': str})
df['descr'] = df['descr'].fillna('')
df['c'] = (df.vlr * 100).round().astype('int64')
df['dia'] = pd.to_datetime(df.anomesdia, utc=True).dt.day
fat = df[df.nom_cate_micro == 'Pagamento de fatura'].copy()
fat['modo'] = np.select([fat.descr.str.contains('minimo'), fat.descr.str.contains('parcial')], ['minimo', 'parcial'], 'integral')
print('modos', fat.modo.value_counts().to_dict())
pf = fat[fat.modo != 'integral']
k = pf.groupby('id_usuario').anomes.nunique()
print('pagadores', len(k), 'meses/pagador med', k.median(), 'p25', k.quantile(.25), 'p75', k.quantile(.75), '>=3', int((k >= 3).sum()), '>=6', int((k >= 6).sum()))
jur = df[df.nom_cate_micro == 'Juros pagos']
pfm = set(zip(pf.id_usuario, pf.anomes)); jm = set(zip(jur.id_usuario, jur.anomes))
def nxt(a):
    y, mo = divmod(int(a), 100)
    return a + 1 if mo < 12 else (y + 1) * 100 + 1
same = sum(1 for x in pfm if x in jm); nextm = sum(1 for (u, a) in pfm if (u, nxt(a)) in jm)
allm = set(zip(df.id_usuario, df.anomes)); nonp = [x for x in allm if x not in pfm]
print('meses parcial', len(pfm), '| juros mesmo mes', same, '| mes seguinte', nextm, '| juros em meses sem parcial', sum(1 for x in nonp if x in jm), 'de', len(nonp))
print('usuarios c/ juros sem parcial', len(set(jur.id_usuario) - set(pf.id_usuario)))
card = df[df.descr.str.startswith('cart credito')]
cm = card.groupby(['id_usuario', 'anomes']).c.sum()
fi = fat[fat.modo == 'integral'].groupby(['id_usuario', 'anomes']).c.sum()
prev_idx = [(u, a - 1 if a % 100 > 1 else None) for u, a in fi.index]
r_prev = pd.Series([fi[i] / cm.get(p, np.nan) if p[1] else np.nan for i, p in zip(fi.index, prev_idx)])
r_same = pd.Series([fi[i] / cm.get(i, np.nan) for i in fi.index])
for nome, r in [('m-1', r_prev), ('m', r_same)]:
    r = r.dropna(); print('integral/compras', nome, 'mediana', round(r.median(), 3), '| dentro de 5%:', round(((r - 1).abs() < .05).mean() * 100, 1), '%')
print('dia do pagamento de fatura (top)', fat.dia.value_counts().head(5).to_dict())
