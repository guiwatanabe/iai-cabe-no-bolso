import pandas as pd, numpy as np
P = '/Users/manasses/Documents/ChatGPT/Batalha de Agentes/'
df = pd.read_csv(P + 'data/extrato_sintetico.csv.gz', dtype={'id_usuario': str, 'descr': str})
df['descr'] = df['descr'].fillna(''); df['c'] = (df.vlr * 100).round().astype('int64')
df['dia'] = pd.to_datetime(df.anomesdia, utc=True).dt.day
fat = df[df.nom_cate_micro == 'Pagamento de fatura'].copy(); fat['parc'] = ~fat.descr.str.contains('integral')
has = lambda micro: set(df[df.nom_cate_micro == micro].id_usuario)
U = pd.DataFrame(index=sorted(df.id_usuario.unique()))
U['perfil'] = np.select([U.index.isin(has('Beneficio INSS')), U.index.isin(has('Pagamento de aluguel')), U.index.isin(has('Recebimento Aluguel')), U.index.isin(has('Financiamento de imovel'))],
                        ['INSS', 'PIX+aluguel', 'CLT+financ+recebe aluguel', 'CLT+financ'], 'CLT sem financ')
fixos = ['Financiamento de imovel', 'Pagamento de aluguel', 'Mensalidade escolar', 'Condominio', 'Seguro de automovel', 'Energia eletrica', 'Agua e esgoto', 'Gas', 'TV Internet celular e telefone', 'Celular', 'Emprestimos', 'Outros emprestimos', 'Consorcio']
card = df.descr.str.startswith('cart credito')
U['renda_mes'] = (df[df.tipo == 'E'].groupby('id_usuario').c.sum() / 1200).reindex(U.index)
U['cartao_mes'] = (df[card].groupby('id_usuario').c.sum() / 1200).reindex(U.index).fillna(0)
U['fixos_mes'] = (df[df.nom_cate_micro.isin(fixos)].groupby('id_usuario').c.sum() / 1200).reindex(U.index).fillna(0)
U['juros_ano'] = (df[df.nom_cate_micro == 'Juros pagos'].groupby('id_usuario').c.sum() / 100).reindex(U.index).fillna(0)
U['meses_parc'] = fat[fat.parc].groupby('id_usuario').anomes.nunique().reindex(U.index).fillna(0)
U['cart_pct'] = U.cartao_mes / U.renda_mes; U['fix_pct'] = U.fixos_mes / U.renda_mes
pay = U[U.meses_parc > 0]
print('corr dentro dos 689: cartao%', round(pay.cart_pct.corr(pay.meses_parc), 3), '| fixos%', round(pay.fix_pct.corr(pay.meses_parc), 3))
for p, g in pay.groupby('perfil'): print(f'  {p}: n={len(g)} corr cartao%={g.cart_pct.corr(g.meses_parc):.3f} fixos%={g.fix_pct.corr(g.meses_parc):.3f}')
c = U[(U.perfil == 'CLT sem financ') & (U.meses_parc > 0)].copy(); med = c[['cart_pct', 'meses_parc']].median()
c['dist'] = ((c.cart_pct - med.cart_pct) / c.cart_pct.std()) ** 2 + ((c.meses_parc - med.meses_parc) / c.meses_parc.std()) ** 2
uid = c.sort_values('dist').index[0]; print('\nPERSONA', uid); print(c.loc[uid].round(3).to_string())
print('medianas do perfil CLT sem financ:', c[['renda_mes', 'cartao_mes', 'fixos_mes', 'cart_pct', 'juros_ano', 'meses_parc']].median().round(2).to_dict())
u = df[df.id_usuario == uid]; fu = fat[fat.id_usuario == uid].set_index('anomes')
m = pd.DataFrame({'renda': u[u.tipo == 'E'].groupby('anomes').c.sum() / 100, 'cartao': u[card.loc[u.index]].groupby('anomes').c.sum() / 100,
                  'fixos': u[u.nom_cate_micro.isin(fixos)].groupby('anomes').c.sum() / 100, 'fatura_paga': fu.c / 100,
                  'modo': fu.descr.str.extract('(integral|parcial|minimo)')[0], 'juros': u[u.nom_cate_micro == 'Juros pagos'].groupby('anomes').c.sum() / 100})
print(m.fillna(0).round(0).to_string())
print('entradas por tipo:', (u[u.tipo == 'E'].groupby('nom_cate_micro').c.agg(['size', 'median']) / [1, 100]).round(2).to_dict('index'))
print('dia salario:', u[u.nom_cate_micro == 'Salario CLT'].dia.mode().tolist(), '| dia fatura:', fu.dia.mode().tolist())
print('cartao por categoria (R$/ano):', (u[card.loc[u.index]].groupby('nom_cate_macro').c.sum() / 100).sort_values(ascending=False).head(6).round(0).to_dict())
pi = u[u.parcela_total > 1]; print('compras parceladas no ano:', len(pi), '| parcela mediana R$', pi.c.median() / 100, '| parcelas totais mediana', pi.parcela_total.median())
