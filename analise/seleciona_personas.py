"""Seleciona candidatos a persona da demo pelas regras da Spec da Gi (27/09).

Escorregão: grupo A, mês rolado a partir de abr/2025 (90 dias de histórico), o não pago cabe no
próximo salário em até 25 dias, mês seguinte pago inteiro, próxima fatura sem explosão.
Rolando a fatura: grupo B, mês rolado com pelo menos 3 faturas roladas ANTES dele (flag honesta,
sem olhar o futuro), não pago parcelável, próxima fatura sem explosão.

Rodar: python3 analise/seleciona_personas.py
"""
from pathlib import Path

import numpy as np
import pandas as pd

RAIZ = Path(__file__).resolve().parents[1]
df = pd.read_csv(RAIZ / 'data/extrato_sintetico.csv.gz', dtype={'id_usuario': str, 'descr': str})
df['descr'] = df['descr'].fillna('')
df['c'] = (df.vlr * 100).round().astype('int64')
df['dia'] = pd.to_datetime(df.anomesdia).dt.day
mi = df.nom_cate_micro
FIXOS = ['Mensalidade escolar', 'Condominio', 'Seguro de automovel', 'Energia eletrica', 'Agua e esgoto', 'Gas',
         'TV Internet celular e telefone', 'Celular', 'Emprestimos', 'Outros emprestimos', 'Consorcio',
         'Pagamento de aluguel', 'Financiamento de imovel']

fat = df[mi == 'Pagamento de fatura'].copy()
fat['modo'] = np.select([fat.descr.str.contains('minimo'), fat.descr.str.contains('parcial')], ['minimo', 'parcial'], 'integral')
fat = fat.set_index(['id_usuario', 'anomes']).sort_index()
g = df.groupby(['id_usuario', 'anomes'])
fat['juros'] = df[mi == 'Juros pagos'].groupby(['id_usuario', 'anomes']).c.sum().reindex(fat.index).fillna(0).values
fat['F'] = np.select([fat.modo == 'integral', fat.modo == 'minimo'], [fat.c, (fat.c / 0.15).round()], (fat.c + fat.juros / 0.14).round())
fat['nao_pago'] = fat.F - fat.c
fat['dia_venc'] = df[mi == 'Pagamento de fatura'].groupby(['id_usuario', 'anomes']).dia.first().reindex(fat.index).values
fat['fixos'] = df[mi.isin(FIXOS)].groupby(['id_usuario', 'anomes']).c.sum().reindex(fat.index).fillna(0).values
sal = df[(df.tipo == 'E') & mi.str.contains('Sal', case=False, na=False)]
fat['salario'] = sal.groupby(['id_usuario', 'anomes']).c.sum().reindex(fat.index).fillna(0).values
fat['dia_sal'] = sal.groupby('id_usuario').dia.median().reindex(fat.index.get_level_values(0)).values
fat['F_med'] = fat.groupby(level=0).F.transform('median')
fat['fixos_med'] = fat.groupby(level=0).fixos.transform('median')
fat['sal_med'] = fat.groupby(level=0).salario.transform('median')
fat['F_prox'] = fat.groupby(level=0).F.shift(-1)
fat['modo_prox'] = fat.groupby(level=0).modo.shift(-1)
fat['rolado'] = fat.modo != 'integral'
fat['roladas_antes'] = fat.groupby(level=0).rolado.cumsum() - fat.rolado
ids = fat.index.get_level_values(0).unique()
has = lambda micro: set(df[mi == micro].id_usuario)
perfil = pd.Series(np.select([ids.isin(has('Beneficio INSS')), ids.isin(has('Pagamento de aluguel')), ids.isin(has('Recebimento Aluguel')),
                              ids.isin(has('Financiamento de imovel'))], ['INSS', 'PIX+aluguel', 'CLT+fin+recebe', 'CLT+fin'], 'CLT s/fin'), index=ids)
k = fat.groupby(level=0).rolado.sum()
grupo = pd.Series(np.select([k >= 6, k >= 3, k >= 1], ['C', 'B', 'A'], 'nunca'), index=ids)
fat['perfil'] = perfil.reindex(fat.index.get_level_values(0)).values
fat['grupo'] = grupo.reindex(fat.index.get_level_values(0)).values
# dias do vencimento até o próximo salário (salário cai no dia dia_sal do mês seguinte se já passou)
fat['dias_ate_salario'] = np.where(fat.dia_sal > fat.dia_venc, fat.dia_sal - fat.dia_venc, 30 - fat.dia_venc + fat.dia_sal)
R = lambda c: round(c / 100)
cols = ['perfil', 'modo', 'F', 'nao_pago', 'salario', 'dia_venc', 'dia_sal', 'dias_ate_salario', 'fixos_med', 'F_med', 'F_prox', 'modo_prox', 'roladas_antes']

r = fat.reset_index()
esc = r[(r.grupo == 'A') & r.rolado & (r.anomes >= 202504) & (r.anomes <= 202511) & (r.dias_ate_salario <= 25)
        & (r.nao_pago <= 0.6 * r.sal_med) & (r.modo_prox == 'integral') & (r.F_prox <= 1.3 * r.F_med) & (r.salario > 0)]
esc = esc.sort_values('nao_pago', ascending=False)
print(f'ESCORREGÃO: {len(esc)} meses candidatos em {esc.id_usuario.nunique()} clientes. Top 6 por não pago (valores em R$):')
for _, x in esc.head(6).iterrows():
    print(f"  {x.id_usuario} {x.anomes} {x.perfil} {x.modo} fatura {R(x.F)} não pago {R(x.nao_pago)} salário {R(x.salario)} "
          f"venc dia {int(x.dia_venc)} salário dia {int(x.dia_sal)} ({int(x.dias_ate_salario)} dias) fixos {R(x.fixos_med)} "
          f"fatura mediana {R(x.F_med)} próxima {R(x.F_prox)} roladas antes {int(x.roladas_antes)}")

rol = r[(r.grupo == 'B') & r.rolado & (r.roladas_antes >= 3) & (r.anomes >= 202506) & (r.anomes <= 202511)
        & (r.F_prox <= 1.3 * r.F_med) & (r.salario > 0)]
rol = rol.assign(parcela10=rol.nao_pago / 10, folga=rol.sal_med - rol.fixos_med)
rol = rol[(rol.nao_pago >= 100000) & (rol.parcela10 <= 0.35 * rol.folga)].sort_values('nao_pago', ascending=False)
print(f'\nROLANDO: {len(rol)} meses candidatos em {rol.id_usuario.nunique()} clientes. Top 6 por não pago:')
for _, x in rol.head(6).iterrows():
    print(f"  {x.id_usuario} {x.anomes} {x.perfil} {x.modo} fatura {R(x.F)} não pago {R(x.nao_pago)} salário {R(x.salario)} "
          f"venc dia {int(x.dia_venc)} fixos {R(x.fixos_med)} fatura mediana {R(x.F_med)} próxima {R(x.F_prox)} "
          f"roladas antes {int(x.roladas_antes)} parcela 10x {R(x.parcela10)} folga aprox {R(x.folga)}")
p = r[(r.id_usuario.str.startswith('3e7d20b2')) & (r.anomes == 202509)].iloc[0]
print(f"\nReferência 3e7d20b2 set/2025: fatura {R(p.F)} não pago {R(p.nao_pago)} salário {R(p.salario)} fixos {R(p.fixos_med)} "
      f"fatura mediana {R(p.F_med)} próxima {R(p.F_prox)} roladas antes {int(p.roladas_antes)} venc dia {int(p.dia_venc)}")
print('\nMacros:', sorted(df.nom_cate_macro.dropna().unique()))
