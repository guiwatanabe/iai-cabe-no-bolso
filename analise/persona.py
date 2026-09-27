import pandas as pd, numpy as np
df = pd.read_csv('/Users/manasses/Documents/ChatGPT/Batalha de Agentes/data/extrato_sintetico.csv.gz', dtype={'id_usuario': str, 'descr': str})
df['descr'] = df['descr'].fillna(''); df['c'] = (df.vlr * 100).round().astype('int64')
df['dia'] = pd.to_datetime(df.anomesdia, utc=True).dt.day
fat = df[df.nom_cate_micro == 'Pagamento de fatura'].copy()
fat['modo'] = np.select([fat.descr.str.contains('minimo'), fat.descr.str.contains('parcial')], ['minimo', 'parcial'], 'integral')
pf = fat[fat.modo != 'integral']; k = pf.groupby('id_usuario').anomes.nunique()
jur = df[df.nom_cate_micro == 'Juros pagos']; jt = jur.groupby('id_usuario').c.sum()
g = lambda micro: df[df.nom_cate_micro == micro].groupby('id_usuario').c.median()
cand = pd.DataFrame({'sal': g('Salario CLT'), 'imo': g('Financiamento de imovel'), 'esc': g('Mensalidade escolar'), 'mparc': k, 'juros': jt}).dropna()
print('CLT+imovel+escola+parcial+juros:', len(cand))
cand = cand.sort_values(['mparc', 'juros'], ascending=False)
print((cand.head(5) / [100, 100, 100, 1, 100]).round(2).to_string())
for uid in [cand.index[0], '412dae1f-928d-4240-b37a-d29d8f99de82']:
    u = df[df.id_usuario == uid]; uf = fat[fat.id_usuario == uid]
    print('\n###', uid, 'linhas', len(u))
    for micro in ['Salario CLT', 'Financiamento de imovel', 'Mensalidade escolar', '13o salario']:
        x = u[u.nom_cate_micro == micro]
        if len(x): print(micro, 'n', len(x), 'mediana', x.c.median() / 100, 'dias', x.dia.value_counts().head(2).to_dict())
    t = uf[['anomes', 'dia', 'modo', 'c']].copy(); t['c'] = t.c / 100
    t['juros'] = t.anomes.map(jur[jur.id_usuario == uid].groupby('anomes').c.sum() / 100)
    print(t.to_string(index=False))
    print('fatura integral tipica', uf[uf.modo == 'integral'].c.median() / 100, '| juros ano', jur[jur.id_usuario == uid].c.sum() / 100)
