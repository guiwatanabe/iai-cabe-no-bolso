import pandas as pd, numpy as np
P = '/Users/manasses/Documents/ChatGPT/Batalha de Agentes/'
df = pd.read_csv(P + 'data/extrato_sintetico.csv.gz', dtype={'id_usuario': str, 'descr': str})
df['descr'] = df['descr'].fillna(''); df['c'] = (df.vlr * 100).round().astype('int64')
df['dia'] = pd.to_datetime(df.anomesdia, utc=True).dt.day
has = lambda col, vals: set(df[df[col].isin(vals)].id_usuario)
U = pd.DataFrame(index=sorted(df.id_usuario.unique()))
flags = {'CLT': ('nom_cate_micro', ['Salario CLT']), 'INSS': ('nom_cate_micro', ['Beneficio INSS']), 'paga_aluguel': ('nom_cate_micro', ['Pagamento de aluguel']),
         'recebe_aluguel': ('nom_cate_micro', ['Recebimento Aluguel']), 'financ_imovel': ('nom_cate_micro', ['Financiamento de imovel']), 'escola': ('nom_cate_micro', ['Mensalidade escolar']),
         'carro': ('nom_cate_macro', ['Veiculos']), 'delivery': ('nom_cate_macro', ['Delivery']), 'app': ('nom_cate_macro', ['Transporte por app']), 'PLR': ('nom_cate_micro', ['Bonus PLR']),
         'emprestimo': ('nom_cate_micro', ['Emprestimos', 'Outros emprestimos'])}
for k, (col, vals) in flags.items(): U[k] = U.index.isin(has(col, vals))
fat = df[df.nom_cate_micro == 'Pagamento de fatura']; par = fat[~fat.descr.str.contains('integral')]
U['meses_parcial'] = par.groupby('id_usuario').anomes.nunique().reindex(U.index).fillna(0)
U['juros'] = (df[df.nom_cate_micro == 'Juros pagos'].groupby('id_usuario').c.sum() / 100).reindex(U.index).fillna(0)
U['renda'] = U.CLT.map({True: 'CLT', False: ''}) + U.INSS.map({True: 'INSS', False: ''})
U['renda'] = U.renda.replace('', 'outra')
U['moradia'] = np.select([U.paga_aluguel, U.financ_imovel], ['aluguel', 'financiamento'], 'propria/outra')
g = U.groupby(['renda', 'moradia', 'recebe_aluguel']).agg(clientes=('juros', 'size'), pagam_parcial=('meses_parcial', lambda s: int((s > 0).sum())), meses_parcial_med=('meses_parcial', 'median'), juros_total=('juros', 'sum'))
print(g.round(0).to_string())
print('\nTaxa de quem paga parcial por flag:')
for k in flags: print(f'  {k}: {int(U[k].sum())} clientes, {U[U[k]].meses_parcial.gt(0).mean():.0%} pagam parcial')
alu = df[df.nom_cate_micro == 'Pagamento de aluguel']
print('\nAluguel pago: dia mais comum', alu.dia.value_counts().head(3).to_dict(), '| mediana R$', alu.c.median() / 100)
inss = df[df.nom_cate_micro == 'Beneficio INSS']; print('INSS: dia', inss.dia.value_counts().head(3).to_dict(), '| mediana R$', inss.c.median() / 100)
outra = U[U.renda == 'outra'].index; e = df[(df.id_usuario.isin(outra)) & (df.tipo == 'E')]
print('Renda "outra" (sem CLT/INSS):', len(outra), 'clientes; entradas por micro:', e.groupby('nom_cate_micro').id_usuario.nunique().to_dict())
