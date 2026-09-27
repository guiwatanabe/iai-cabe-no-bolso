"""Escolhe o cliente da demo no grupo B (3 a 5 meses rolando a fatura) e exporta o mes a mes em JSON."""
import json, itertools
import pandas as pd, numpy as np
P = '/Users/manasses/Documents/ChatGPT/Batalha de Agentes/'
df = pd.read_csv(P + 'data/extrato_sintetico.csv.gz', dtype={'id_usuario': str, 'descr': str})
df['descr'] = df['descr'].fillna(''); df['c'] = (df.vlr * 100).round().astype('int64'); mi = df.nom_cate_micro
df['dia'] = pd.to_datetime(df.anomesdia, utc=True).dt.day
card = df.descr.str.startswith('cart credito')
fat = df[mi == 'Pagamento de fatura'].copy()
fat['modo'] = np.select([fat.descr.str.contains('minimo'), fat.descr.str.contains('parcial')], ['minimo', 'parcial'], 'integral')
fat = fat.sort_values(['id_usuario', 'anomes'])
jm = df[mi == 'Juros pagos'].groupby(['id_usuario', 'anomes']).c.sum()
fat['juros'] = [jm.get((u, a), 0) for u, a in zip(fat.id_usuario, fat.anomes)]
fat['F'] = np.select([fat.modo == 'integral', fat.modo == 'minimo'], [fat.c, fat.c / 0.15], fat.c + fat.juros / 0.14).round()
has = lambda m: set(df[mi == m].id_usuario)
k = fat[fat.modo != 'integral'].groupby('id_usuario').size()
B = k[(k >= 3) & (k <= 5)].index
def seq(s): return max((len(list(g)) for kk, g in itertools.groupby(s != 'integral') if kk), default=0)
runs = fat[fat.id_usuario.isin(B)].groupby('id_usuario').modo.apply(seq)
sem_fin = B[~B.isin(has('Financiamento de imovel')) & ~B.isin(has('Beneficio INSS')) & ~B.isin(has('Pagamento de aluguel'))]
cand = fat[fat.id_usuario.isin(sem_fin)].groupby('id_usuario').agg(meses=('modo', lambda s: (s != 'integral').sum()), juros=('juros', 'sum'))
cand['seq'] = runs.reindex(cand.index); cand = cand[cand.seq >= 3].sort_values(['seq', 'juros'], ascending=False)
print('B sem financiamento com >=3 meses seguidos:', len(cand)); print((cand.head(8) / [1, 100, 1]).round(0).to_string())
uid = cand.index[0]; u = df[df.id_usuario == uid]
fixos = ['Mensalidade escolar', 'Condominio', 'Seguro de automovel', 'Energia eletrica', 'Agua e esgoto', 'Gas', 'TV Internet celular e telefone', 'Celular', 'Emprestimos', 'Outros emprestimos', 'Consorcio', 'Pagamento de aluguel', 'Financiamento de imovel']
meses = []
for a, g in u.groupby('anomes'):
    f = fat[(fat.id_usuario == uid) & (fat.anomes == a)].iloc[0]
    meses.append(dict(anomes=int(a), renda=int(g[g.tipo == 'E'].c.sum()), salario=int(g[mi == 'Salario CLT'].c.sum()), pix_recebido=int(g[g.nom_cate_macro == 'Recebimentos diversos'].c.sum()),
                      fixos=int(g[mi.loc[g.index].isin(fixos)].c.sum()), cartao=int(g[card.loc[g.index]].c.sum()), delivery_app=int(g[g.nom_cate_macro.isin(['Delivery', 'Transporte por app'])].c.sum()),
                      fatura_estimada=int(f.F), pago=int(f.c), modo=f.modo, juros=int(f.juros), dia_fatura=int(f.dia)))
m = pd.DataFrame(meses); print(uid); print((m.set_index('anomes').drop(columns='modo') / 100).round(0).assign(modo=m.modo.values).to_string())
sal = u[mi == 'Salario CLT']
resumo = dict(id_usuario=uid, perfil='CLT sem financiamento', grupo='B', dia_salario=int(sal.dia.mode().iloc[0]), salario_mediano=int(sal.c.median()), dia_fatura=int(m.dia_fatura.mode().iloc[0]),
              meses_rolados=int((m.modo != 'integral').sum()), maior_sequencia=int(seq(m.modo)), juros_ano=int(m.juros.sum()), fatura_mediana=int(m.fatura_estimada.median()), cartao_mediano=int(m.cartao.median()),
              delivery_app_mediano=int(m.delivery_app.median()), fixos_mediano=int(m.fixos.median()), valores_em='centavos', taxa_rotativo_base='0.14 ao mes sobre o nao pago; minimo = 15% da fatura')
print(json.dumps(resumo, ensure_ascii=False, indent=1))
import os; os.makedirs(P + 'data/personas', exist_ok=True)
json.dump(dict(resumo=resumo, meses=meses), open(P + f'data/personas/{uid[:8]}_grupo_b.json', 'w'), ensure_ascii=False, indent=1)
top = u[card.loc[u.index]].groupby('nom_cate_macro').c.sum().sort_values(ascending=False).head(5) / 100
print('cartao por categoria R$/ano:', top.round(0).to_dict())
