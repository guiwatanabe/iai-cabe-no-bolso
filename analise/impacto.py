import pandas as pd, numpy as np
P = '/Users/manasses/Documents/ChatGPT/Batalha de Agentes/'
df = pd.read_csv(P + 'data/extrato_sintetico.csv.gz', dtype={'id_usuario': str, 'descr': str})
df['descr'] = df['descr'].fillna(''); df['c'] = (df.vlr * 100).round().astype('int64')
df['card'] = df.descr.str.startswith('cart credito'); df['isfat'] = df.nom_cate_micro == 'Pagamento de fatura'
fat = df[df.isfat].copy(); fat['parc'] = ~fat.descr.str.contains('integral'); fat = fat.set_index(['id_usuario', 'anomes'])
jm = df[df.nom_cate_micro == 'Juros pagos'].groupby(['id_usuario', 'anomes']).c.sum() / 100
med_int = fat[~fat.parc].groupby(level=0).c.median() / 100
extra = set(map(tuple, df[df.nom_cate_micro.isin(['13o salario', 'Bonus PLR'])][['id_usuario', 'anomes']].values))
fp = fat[fat.parc].copy(); fp['juros'] = jm.reindex(fp.index).fillna(0); fp['tip'] = med_int.reindex(fp.index.get_level_values(0)).values
fp['extra'] = [i in extra for i in fp.index]
for nome, emask in [('com PIX', df.tipo == 'E'), ('sem PIX', (df.tipo == 'E') & (df.nom_cate_macro != 'Recebimentos diversos'))]:
    g = df.assign(E=np.where(emask, df.c, 0), S=np.where((df.tipo == 'S') & ~df.card & ~df.isfat, df.c, 0)).groupby(['id_usuario', 'anomes'])[['E', 'S']].sum()
    fp['sob_' + nome] = ((g.E - g.S) / 100).reindex(fp.index).values
    ok = fp['sob_' + nome] >= fp.tip
    print(f'JUROS EVITAVEIS {nome}: R$ {fp[ok].juros.sum():,.0f} em {int(ok.sum())} meses, {fp[ok].index.get_level_values(0).nunique()} clientes')
print(f'juros em meses parciais total R$ {fp.juros.sum():,.0f}')
print(f'meses parciais COM renda extra (13o/PLR): {int(fp.extra.sum())}, clientes {fp[fp.extra].index.get_level_values(0).nunique()}, juros R$ {fp[fp.extra].juros.sum():,.0f}')
e13 = df[df.nom_cate_micro == '13o salario']; plr = df[df.nom_cate_micro == 'Bonus PLR']
print('13o: clientes', e13.id_usuario.nunique(), 'R$', round(e13.c.sum() / 1e8, 2), 'mi | PLR: clientes', plr.id_usuario.nunique(), 'R$', round(plr.c.sum() / 1e8, 2), 'mi')
ann = df.assign(E=np.where(df.tipo == 'E', df.c, 0), S=np.where((df.tipo == 'S') & ~df.card, df.c, 0)).groupby('id_usuario')[['E', 'S']].sum() / 100
ann['net'] = ann.E - ann.S
print('saldo anual caixa (entradas - saidas sem compras cartao): positivos', int((ann.net > 0).sum()), '| mediana R$', round(ann.net.median()), '| p25', round(ann.net.quantile(.25)), '| p75', round(ann.net.quantile(.75)))
for seg, micro in [('INSS', 'Beneficio INSS'), ('Aluguel recebido', 'Recebimento Aluguel'), ('Capitalizacao', 'Titulo de capitalizacao'), ('Consorcio', 'Consorcio'), ('Aluguel pago', 'Pagamento de aluguel')]:
    us = set(df[df.nom_cate_micro == micro].id_usuario); pay = set(fat[fat.parc].index.get_level_values(0))
    print(f'{seg}: {len(us)} clientes, {len(us & pay)} pagam parcial ({len(us & pay) / max(len(us), 1):.0%})')
ren = df[df.descr.str.contains('reneg')]; print('renegociacao: linhas', len(ren), 'clientes', ren.id_usuario.nunique())
