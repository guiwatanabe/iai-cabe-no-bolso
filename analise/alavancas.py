import pandas as pd, numpy as np
P = '/Users/manasses/Documents/ChatGPT/Batalha de Agentes/'
df = pd.read_csv(P + 'data/extrato_sintetico.csv.gz', dtype={'id_usuario': str, 'descr': str})
df['descr'] = df['descr'].fillna(''); df['c'] = (df.vlr * 100).round().astype('int64')
df['dia'] = pd.to_datetime(df.anomesdia, utc=True).dt.day
fat = df[df.nom_cate_micro == 'Pagamento de fatura'].copy(); fat['parc'] = ~fat.descr.str.contains('integral')
# 1) ciclo: pagar parcial num mes aumenta a chance de pagar parcial no seguinte?
p = fat.sort_values(['id_usuario', 'anomes']).copy(); p['prox'] = p.groupby('id_usuario').parc.shift(-1)
p = p.dropna(subset=['prox'])
print('P(parcial no mes seguinte | este mes parcial) =', round(p[p.parc].prox.mean(), 3), '| | este mes integral =', round(p[~p.parc].prox.mean(), 3))
pay = set(fat[fat.parc].id_usuario); q = p[p.id_usuario.isin(pay)]
print('  so entre os 689:', round(q[q.parc].prox.mean(), 3), 'vs', round(q[~q.parc].prox.mean(), 3))
runs = fat[fat.id_usuario.isin(pay)].sort_values(['id_usuario', 'anomes']).groupby('id_usuario').parc.apply(lambda s: max((len(list(g)) for k, g in __import__('itertools').groupby(s) if k), default=0))
print('  maior sequencia de meses parciais seguidos: mediana', runs.median(), '| >=3 meses seguidos:', int((runs >= 3).sum()), 'clientes')
# 2) vencimento x dia da renda
inc = df[df.nom_cate_micro.isin(['Salario CLT', 'Beneficio INSS'])].groupby('id_usuario').dia.agg(lambda s: s.mode().iloc[0])
fd = fat.groupby('id_usuario').dia.agg(lambda s: s.mode().iloc[0]); rate = fat.groupby('id_usuario').parc.mean()
u = pd.DataFrame({'venc': fd, 'renda': inc, 'taxa': rate}).dropna(); u['gap'] = (u.venc - u.renda) % 30
print('taxa de pagamento parcial por dia de vencimento:', u.groupby('venc').taxa.agg(['mean', 'size']).round(3).to_dict())
print('por distancia renda->vencimento (dias):', u.groupby(pd.cut(u.gap, [0, 10, 15, 20, 30])).taxa.agg(['mean', 'size']).round(3).to_dict())
# 3) tamanho do problema em relacao a renda, por perfil
has = lambda micro: set(df[df.nom_cate_micro == micro].id_usuario)
U = pd.DataFrame(index=sorted(df.id_usuario.unique()))
U['perfil'] = np.select([U.index.isin(has('Beneficio INSS')), U.index.isin(has('Pagamento de aluguel')), U.index.isin(has('Recebimento Aluguel')), U.index.isin(has('Financiamento de imovel'))],
                        ['INSS', 'PIX+aluguel', 'CLT+financ+recebe aluguel', 'CLT+financ'], 'CLT sem financ')
E = df[df.tipo == 'E'].groupby('id_usuario').c.sum() / 100
U['renda_mes'] = (E / 12).reindex(U.index)
U['juros_mes'] = (df[df.nom_cate_micro == 'Juros pagos'].groupby('id_usuario').c.sum() / 1200).reindex(U.index).fillna(0)
U['cartao_mes'] = (df[df.descr.str.startswith('cart credito')].groupby('id_usuario').c.sum() / 1200).reindex(U.index).fillna(0)
U['fatura_mes'] = (fat.groupby('id_usuario').c.sum() / 1200).reindex(U.index).fillna(0)
fixos = ['Financiamento de imovel', 'Pagamento de aluguel', 'Mensalidade escolar', 'Condominio', 'Seguro de automovel', 'Energia eletrica', 'Agua e esgoto', 'Gas', 'TV Internet celular e telefone', 'Celular', 'Emprestimos', 'Outros emprestimos', 'Consorcio']
U['fixos_mes'] = (df[df.nom_cate_micro.isin(fixos)].groupby('id_usuario').c.sum() / 1200).reindex(U.index).fillna(0)
U['fixos_%renda'] = U.fixos_mes / U.renda_mes; U['juros_%renda'] = U.juros_mes / U.renda_mes; U['cartao_%renda'] = U.cartao_mes / U.renda_mes
U['meses_parc'] = fat[fat.parc].groupby('id_usuario').anomes.nunique().reindex(U.index).fillna(0)
print(U.groupby('perfil')[['renda_mes', 'fixos_%renda', 'cartao_%renda', 'juros_mes', 'juros_%renda', 'meses_parc']].median().round(3).to_string())
print('corr(fixos_%renda, meses_parc) =', round(U['fixos_%renda'].corr(U.meses_parc), 3), '| corr(cartao_%renda, meses_parc) =', round(U['cartao_%renda'].corr(U.meses_parc), 3))
