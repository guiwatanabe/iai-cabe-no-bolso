"""Exporta o mês a mês de um cliente da base em JSON (centavos), com a MESMA lógica de analise/persona_b.py.

Generalizado por cliente_id; resolve caminhos a partir de __file__ (funciona de qualquer worktree).

Rodar (da raiz ou de qualquer lugar):
  python3 analise/persona_export.py 755627ab-804b-4211-b0ea-f4ebacc58716 --sufixo escorregao --anomes 202508 --apelido Ana
  python3 analise/persona_export.py 3e7d20b2-4c4f-450a-bbd2-e60bfda81f0b --saida /tmp/bruno.json   # confere com o gabarito

Definições (iguais a persona_b.py): centavos = round(vlr * 100); modo pelo descr do 'Pagamento de fatura'
(minimo, parcial, senão integral); fatura por modo (integral = pago; minimo = pago/0,15; parcial = pago + juros/0,14);
renda = todas as entradas tipo E (inclui PIX); cartão = descr começa com 'cart credito'; delivery/app = macros
Delivery e Transporte por app; fixos = lista de subcategorias abaixo (inclusive no cartão).
"""
import argparse
import itertools
import json
from pathlib import Path

import numpy as np
import pandas as pd

RAIZ = Path(__file__).resolve().parents[1]
FIXOS = ['Mensalidade escolar', 'Condominio', 'Seguro de automovel', 'Energia eletrica', 'Agua e esgoto', 'Gas',
         'TV Internet celular e telefone', 'Celular', 'Emprestimos', 'Outros emprestimos', 'Consorcio',
         'Pagamento de aluguel', 'Financiamento de imovel']
GRUPO_NOME = {'nunca': 'em_dia', 'A': 'escorregao', 'B': 'rolando', 'C': 'no_limite'}


def seq(s):
    return max((len(list(g)) for k, g in itertools.groupby(s != 'integral') if k), default=0)


def exportar(cliente_id: str, sufixo: str | None, anomes_demo: int | None, apelido: str | None, saida: Path | None) -> dict:
    df = pd.read_csv(RAIZ / 'data/extrato_sintetico.csv.gz', dtype={'id_usuario': str, 'descr': str})
    df['descr'] = df['descr'].fillna('')
    df['c'] = (df.vlr * 100).round().astype('int64')
    df['dia'] = pd.to_datetime(df.anomesdia, utc=True).dt.day
    u = df[df.id_usuario == cliente_id].copy()
    if u.empty:
        raise SystemExit(f'cliente {cliente_id} não está na base')
    mi = u.nom_cate_micro
    card = u.descr.str.startswith('cart credito')
    fat = u[mi == 'Pagamento de fatura'].copy()
    fat['modo'] = np.select([fat.descr.str.contains('minimo'), fat.descr.str.contains('parcial')], ['minimo', 'parcial'], 'integral')
    fat = fat.sort_values('anomes')
    jm = u[mi == 'Juros pagos'].groupby('anomes').c.sum()
    fat['juros'] = [jm.get(a, 0) for a in fat.anomes]
    fat['F'] = np.select([fat.modo == 'integral', fat.modo == 'minimo'], [fat.c, fat.c / 0.15], fat.c + fat.juros / 0.14).round()
    meses = []
    for a, g in u.groupby('anomes'):
        f = fat[fat.anomes == a]
        m = dict(anomes=int(a), renda=int(g[g.tipo == 'E'].c.sum()), salario=int(g[mi.loc[g.index] == 'Salario CLT'].c.sum()),
                 pix_recebido=int(g[g.nom_cate_macro == 'Recebimentos diversos'].c.sum()),
                 fixos=int(g[mi.loc[g.index].isin(FIXOS)].c.sum()), cartao=int(g[card.loc[g.index]].c.sum()),
                 delivery_app=int(g[g.nom_cate_macro.isin(['Delivery', 'Transporte por app'])].c.sum()))
        if not f.empty:
            f = f.iloc[0]
            m.update(fatura_estimada=int(f.F), pago=int(f.c), modo=f.modo, juros=int(f.juros), dia_fatura=int(f.dia))
        meses.append(m)
    m = pd.DataFrame(meses)
    entradas = set(u[u.tipo == 'E'].nom_cate_micro)
    micros = set(mi)
    if 'Beneficio INSS' in entradas:
        perfil = 'Aposentado INSS'
    elif 'Salario CLT' not in entradas:
        perfil = 'Renda por PIX' + (' e paga aluguel' if 'Pagamento de aluguel' in micros else '')
    else:
        perfil = 'CLT ' + ('com financiamento' if 'Financiamento de imovel' in micros else 'sem financiamento') + \
                 (' que recebe aluguel' if 'Recebimento Aluguel' in entradas else '')
    rolados = int((m.modo != 'integral').sum())
    letra = 'nunca' if rolados == 0 else 'A' if rolados <= 2 else 'B' if rolados <= 5 else 'C'
    sal = u[(mi == 'Salario CLT') | (mi == 'Beneficio INSS')]
    resumo = dict(id_usuario=cliente_id, apelido=apelido, perfil=perfil, grupo=letra, grupo_nome=GRUPO_NOME[letra],
                  anomes_demo=anomes_demo,
                  dia_salario=int(sal.dia.mode().iloc[0]) if not sal.empty else None,
                  salario_mediano=int(sal.groupby('anomes').c.sum().median()) if not sal.empty else 0,
                  dia_fatura=int(m.dia_fatura.mode().iloc[0]), meses_rolados=rolados, maior_sequencia=int(seq(m.modo)),
                  juros_ano=int(m.juros.sum()), fatura_mediana=int(m.fatura_estimada.median()), cartao_mediano=int(m.cartao.median()),
                  delivery_app_mediano=int(m.delivery_app.median()), fixos_mediano=int(m.fixos.median()), valores_em='centavos',
                  taxa_rotativo_base='0.14 ao mes sobre o nao pago; minimo = 15% da fatura')
    if anomes_demo:
        f = fat[fat.anomes == anomes_demo]
        if not f.empty:
            f = f.iloc[0]
            resumo['mes_demo'] = dict(anomes=int(anomes_demo), fatura=int(f.F), pago=int(f.c), modo=f.modo, juros=int(f.juros),
                                      nao_pago=int(f.F - f.c), dia_vencimento=int(f.dia),
                                      roladas_antes=int((fat[fat.anomes < anomes_demo].modo != 'integral').sum()))
    dados = dict(resumo=resumo, meses=meses)
    destino = saida or (RAIZ / 'data/personas' / f"{cliente_id[:8]}_{sufixo or GRUPO_NOME[letra]}.json")
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(json.dumps(dados, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    print(json.dumps(resumo, ensure_ascii=False, indent=1))
    print((m.set_index('anomes').drop(columns='modo') / 100).round(0).assign(modo=m.modo.values).to_string())
    print('gravado em', destino)
    return dados


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('cliente_id')
    ap.add_argument('--sufixo', help='sufixo do arquivo: data/personas/<id8>_<sufixo>.json (padrão: nome do grupo)')
    ap.add_argument('--anomes', type=int, help='mês da demo (AAAAMM) para gravar o resumo do mês')
    ap.add_argument('--apelido', help='apelido fictício da persona na demo')
    ap.add_argument('--saida', type=Path, help='caminho de saída explícito (para conferir sem sobrescrever)')
    a = ap.parse_args()
    exportar(a.cliente_id, a.sufixo, a.anomes, a.apelido, a.saida)
