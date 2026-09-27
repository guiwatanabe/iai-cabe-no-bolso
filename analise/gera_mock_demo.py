"""Gera as respostas gravadas da demo (demo/mock/*.json) para as duas personas, usando o cabe_core real.

Plano B da demo: quando a API não responde (ou com ?mock=1), demo/app.js lê estes JSONs.
Os números e as formas dos cards vêm de agent/cabe_core (capacidade.motor, ofertas.montar, ofertas.plano_de,
acompanhar.ciclo, painel.juri); este script só acrescenta a camada de conversa (mensagens, chips, trace, FinOps).
Nenhum número é calculado aqui. Cada resposta carrega numeros_validados acumulados, como o state do agente.

Rodar da raiz: python3 analise/gera_mock_demo.py   (precisa de pandas e pyyaml; sem rede, sem LLM)
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / 'agent'))

from cabe_core import acompanhar, calendario, capacidade, config, dados, fatura, ofertas, painel, travas  # noqa: E402
from cabe_core.dinheiro import brl  # noqa: E402
from cabe_no_bolso import policy  # noqa: E402

SAIDA = RAIZ / 'demo' / 'mock'
PERSONAS = {
    'ana': dict(cliente_id='755627ab-804b-4211-b0ea-f4ebacc58716', anomes=202508, apelido='Ana', perfil='CLT com financiamento'),
    'bruno': dict(cliente_id='3e7d20b2-4c4f-450a-bbd2-e60bfda81f0b', anomes=202509, apelido='Bruno', perfil='CLT sem financiamento'),
}
VERSAO_CONSENTIMENTO = 'consentimento-v1-2026-09-27'


class Numeros:
    """Acumula numeros_validados como o state da sessão (dedup por valor+origem)."""

    def __init__(self):
        self.itens: list[dict] = []
        self.vistos: set = set()

    def add(self, lista: list[dict]):
        for n in lista or []:
            chave = (n['valor'], n['origem'])
            if chave not in self.vistos:
                self.vistos.add(chave)
                self.itens.append({'valor': n['valor'], 'origem': n['origem']})

    def lista(self) -> list[dict]:
        return [dict(i) for i in self.itens]


def mes(anomes: int) -> str:
    return calendario.rotulo(anomes)


def resposta(nums: Numeros, mensagens: list[dict], cards: list[dict], sugestoes: list[dict] | None = None) -> dict:
    r = {'mensagens': mensagens, 'cards': cards, 'numeros_validados': nums.lista(),
         'guardiao': {'removidos': [], 'termos_bloqueados': []}, 'simulado': True}
    if sugestoes is not None:
        r['sugestoes'] = sugestoes
    return r


def textos(p: dict, m: dict, o: dict, plano: dict | None) -> dict:
    nome = p['apelido']
    f = m['fatura']
    fat, venc, folga, falta = brl(f['valor']), f['vencimento_dia'], brl(m['folga']), brl(m['falta'])
    dias, receb = m['dias_ate_recebimento'], m['dia_recebimento']
    cont = o['continuar_no_rotativo']
    rec = o['opcoes'][o['recomendada']] if o['recomendada'] is not None else None
    t = {
        'abertura': f'Oi, {nome}. Sou o ia.i, uma inteligência artificial do banco. Sua fatura fechou em {fat} e vence dia {venc}.',
        'humano': 'Vou te passar para uma pessoa do time, com o que a gente já viu aqui. Você não precisa repetir nada.',
        'texto_livre': 'Sou uma IA e por aqui só consigo ajudar com a fatura deste mês. Quer ver as opções ou falar com uma pessoa?',
        'nao_quero_padrao': f'Tudo bem. Não volto a esse assunto até a próxima fatura. Se mudar de ideia até o dia {venc}, é só me chamar.',
        'nao_quero_minimo': (f'Tudo bem. Só para você saber: com o mínimo, os juros do cartão do próximo mês ficam em '
                             f'{brl(cont["se_pagar_minimo"]["custo_1_mes"])}. Se mudar de ideia até o dia {venc}, é só me chamar.'),
        'nao_quero_outro': (f'Tudo bem. Só para você saber: com esse valor, os juros do cartão do próximo mês ficam em '
                            f'{brl(cont["se_pagar_valor_gravado"]["custo_1_mes"])}. Se mudar de ideia até o dia {venc}, é só me chamar.'),
    }
    if m['cabe']:
        t.update(insight=(f'Sua fatura de {fat} cabe no seu saldo previsto para o dia {venc}.', '', 'cabe'),
                 consigo_pagar=f'Sim. Sua fatura de {fat} cabe no seu mês. Está tudo certo para pagar o total.',
                 proposta=f'Sua fatura fechou em {fat} e a previsão de saldo no vencimento cobre o total. Está tudo certo para pagar inteira.',
                 comparador='', confirmacao='', fecho='', intercepta='')
    elif o['caminho'] == 'cobertura_curta' and rec:
        t.update(
            insight=(f'Sua fatura fechou em {fat}. Até o dia {venc}, a previsão é ter {folga}. Temos opções para pagar tudo.', 'Ver opções no ia.i', 'falta_pontual'),
            abertura=t['abertura'] + f' Até lá, a previsão é ter {folga} na conta. Seu próximo salário cai dia {receb}.',
            consigo_pagar=(f'Pelo seu mês, cabe pagar {folga} no dia {venc}. Para a fatura inteira, {m["frase"]}. '
                           'Quer ver um jeito de pagar tudo?'),
            proposta=(f'Uma opção: pagar a fatura inteira no dia {venc} usando o limite da conta por {dias} dias, até o salário. '
                      f'Custo estimado: {brl(rec["custo_total"])}. Se pagar só o mínimo, os juros do cartão ficam em '
                      f'{brl(cont["se_pagar_minimo"]["custo_1_mes"])} este mês. Quer ver as duas lado a lado?'),
            comparador=(f'A cobertura pelo limite da conta está liberada para você e custa {brl(rec["custo_total"])} por {dias} dias, '
                        f'contra {brl(cont["custo_1_mes"])} se a diferença ficar nos juros do cartão. Se fizer sentido, confirmo com você antes de qualquer coisa.'),
            confirmacao=(f'Fica assim: no dia {venc} a fatura de {fat} é paga inteira, {brl(o["pagar_agora"])} da sua conta e '
                         f'{brl(rec["valor_financiado"])} pelo limite. No dia {receb} o salário cobre o limite; custo {brl(rec["custo_total"])}.'),
            fecho=f'Combinado. No dia {receb}, quando o salário entrar, o limite da conta é coberto na hora. Te aviso quando zerar.',
            intercepta='Antes de confirmar: tem um jeito de pagar a fatura inteira com uma cobertura que cabe no seu mês.',
        )
    elif o['caminho'] == 'parcelamento' and rec:
        t.update(
            insight=(f'Sua fatura fechou em {fat} e vence dia {venc}. Pelo seu mês, cabe pagar {brl(o["pagar_agora"])}; faltam {falta}. '
                     'Dá para juntar o que falta numa parcela que cabe no seu orçamento.', 'Conversar com o ia.i', 'falta_que_se_repete'),
            abertura=t['abertura'] + f' Dei uma olhada no seu mês: cabe pagar {brl(o["pagar_agora"])} e faltam {falta}.',
            consigo_pagar=(f'Pelo seu mês, cabe pagar {folga} no dia {venc}. Para a fatura inteira, {m["frase"]}. '
                           'Quer ver um jeito de juntar o que falta numa parcela?'),
            proposta=(f'Tenho uma ideia que pode aliviar: juntar esses {falta} num parcelamento só, com {rec["n_parcelas"]} parcelas de '
                      f'{brl(rec["parcela"])}, que cabem no que sobra no seu mês. Assim seu limite do cartão volta inteiro. '
                      'Quer ver as opções lado a lado, com o custo total de cada uma?'),
            comparador=(f'A opção com a {rec["rotulo_cliente"]} está liberada para você e é a de menor custo total: {brl(rec["custo_total"])}, '
                        f'contra até {brl(cont["custo_ate_teto"])} se o que falta ficar nos juros do cartão. '
                        'Se fizer sentido, te mostro o resumo antes de qualquer coisa, e só seguimos com a sua confirmação.'),
            confirmacao=(f'Fica assim: {brl(o["pagar_agora"])} agora; {falta} em {rec["n_parcelas"]} parcelas de {brl(rec["parcela"])}; '
                         f'termina em {mes(rec["termina_em"])}; até a próxima fatura cabem {brl(o["teto_cartao_mes"])} no cartão.'),
            fecho=('Combinado. Te mostro o resumo do contrato antes de qualquer coisa e só seguimos com a sua confirmação. '
                   'Te acompanho nas próximas três faturas.'),
            intercepta='Antes de confirmar: tem um jeito de pagar a fatura inteira com uma parcela que cabe no seu mês.',
        )
    else:
        t.update(insight=(f'Sua fatura fechou em {fat} e vence dia {venc}. Veja as formas de pagar.', 'Ver opções da fatura', 'sem_credito'),
                 consigo_pagar=f'Pelo seu mês, cabe pagar {folga} no dia {venc}. Para a fatura inteira, {m["frase"]}. Uma pessoa do time pode ajudar.',
                 proposta='Estas são as formas de pagar. Quer que eu explique alguma, ou prefere falar com alguém da equipe?',
                 comparador='', confirmacao='', fecho='', intercepta='')
    # por que veio alta: composição da fatura (compras do mês anterior por categoria) e parcelas em curso
    comp = m['flags']['composicao_fatura']
    cats = comp.get('categorias') or []
    partes = [f'Nesta fatura entraram {brl(comp["total_compras"])} de compras de {mes(comp["anomes_compras"])}.']
    if cats:
        partes.append(f'{cats[0]["categoria"]} foi {cats[0]["pct"]}% disso ({brl(cats[0]["valor"])})'
                      + (f' e {cats[1]["categoria"]} {cats[1]["pct"]}% ({brl(cats[1]["valor"])}).' if len(cats) > 1 else '.'))
    if comp.get('mediana_compras'):
        partes.append(f'Seu mês típico de cartão é {brl(comp["mediana_compras"])}.')
    pc = comp.get('parcelas_em_curso') or {}
    if pc.get('quantidade'):
        partes.append(f'Também entrou {pc["quantidade"]} parcela em curso, {brl(pc["valor"])}.' if pc['quantidade'] == 1
                      else f'Também entraram {pc["quantidade"]} parcelas em curso, {brl(pc["valor"])}.')
    partes.append('Quer ver as opções para pagar?')
    t['por_que_alta'] = ' '.join(partes)
    return t


def gerar(chave: str, p: dict, fonte, taxas: dict) -> dict:
    cid, anomes = p['cliente_id'], p['anomes']
    nums = Numeros()
    m = capacidade.motor(fonte, cid, anomes, taxas)
    lib = policy.liberacao_para(cid, taxas)
    o = ofertas.montar(m, m['grupo'], taxas, lib)
    # extensão do mock: o que acontece se o cliente pagar o valor que pagou de verdade na base (função do núcleo)
    rot = taxas['rotativo']
    np_base = m['fatura']['nao_pago_na_base']
    o['continuar_no_rotativo']['se_pagar_valor_gravado'] = {
        'paga_agora': m['fatura']['pago_na_base'], 'nao_pago': np_base,
        'custo_1_mes': travas.custo_rotativo(np_base, float(rot['taxa_mes']), 1, float(rot.get('teto_encargos_pct', 1.0)))}
    o['numeros'].append({'valor': o['continuar_no_rotativo']['se_pagar_valor_gravado']['custo_1_mes'],
                         'origem': 'ofertas.montar:continuar_no_rotativo.se_pagar_valor_gravado.custo_1_mes'})
    o['numeros'].append({'valor': m['fatura']['pago_na_base'], 'origem': 'ofertas.montar:continuar_no_rotativo.se_pagar_valor_gravado.paga_agora'})
    o['numeros'].append({'valor': np_base, 'origem': 'ofertas.montar:continuar_no_rotativo.se_pagar_valor_gravado.nao_pago'})
    plano = ofertas.plano_de(o, None, m) if o['recomendada'] is not None else None
    t = textos(p, m, o, plano)
    f = m['fatura']
    data_simulada = f'{max(f["vencimento_dia"] - 7, 1):02d}/{anomes % 100:02d}/{anomes // 100}'

    # ---- POST /api/sessao
    nums_sessao = Numeros()
    nums_sessao.add([{'valor': f['valor'], 'origem': 'capacidade.motor:fatura.valor'},
                     {'valor': f['vencimento_dia'], 'origem': 'capacidade.motor:fatura.vencimento_dia'},
                     {'valor': f['minimo'], 'origem': 'capacidade.motor:fatura.minimo'},
                     {'valor': f['pago_na_base'], 'origem': 'capacidade.motor:fatura.pago_na_base'}])
    sessao = {
        'sessao_id': f'mock-{chave}', 'modo': 'mock',
        'cliente': {'apelido': p['apelido'], 'perfil': p['perfil'], 'grupo_rotulo': m['grupo']['rotulo'], 'grupo': m['grupo']['grupo']},
        'anomes': anomes, 'mes_rotulo': mes(anomes), 'data_simulada': data_simulada,
        'fatura': {'valor': f['valor'], 'vencimento_dia': f['vencimento_dia'], 'minimo': f['minimo'],
                   'opcoes_pagamento': [{'rotulo': 'Total', 'valor': f['valor'], 'acao': 'pagar_total'},
                                        {'rotulo': 'Mínimo', 'valor': f['minimo'], 'acao': 'pagar_minimo'},
                                        {'rotulo': 'Outro valor', 'valor': None, 'acao': 'pagar_outro_valor', 'valor_gravado': f['pago_na_base']}]},
        'consentimento': False, 'numeros_validados': nums_sessao.lista(), 'simulado': True,
    }
    nums.add(nums_sessao.lista())

    # ---- POST /api/consentimento (com adesão o motor roda no gatilho e o insight aparece no cartão)
    nums.add(m['numeros'])
    consent = {
        'consentimento': True,
        'registro': {'data': f'{data_simulada} (simulada)', 'versao_texto': VERSAO_CONSENTIMENTO,
                     'escopo': 'últimos 90 dias de conta e cartão; revogável a qualquer momento'},
        'insight': {'estado': t['insight'][2], 'texto': t['insight'][0], 'botao': {'rotulo': t['insight'][1], 'acao': 'ver_opcoes'}},
        'numeros_validados': nums.lista(), 'simulado': True,
    }
    consent_negado = {'consentimento': False,
                      'registro': {'data': f'{data_simulada} (simulada)', 'versao_texto': VERSAO_CONSENTIMENTO,
                                   'escopo': 'recusado; o pedido volta no máximo uma vez por mês, nunca no meio do pagamento'},
                      'insight': None, 'numeros_validados': [], 'simulado': True}

    # ---- POST /api/mensagem
    diagnostico = {'tipo': 'diagnostico', 'dados': m}
    chips_humano = {'rotulo': 'Falar com uma pessoa', 'acao': 'falar_com_pessoa', 'secundario': True}
    nums.add(o['numeros'])
    if o['recomendada'] is not None:
        msgs = [{'papel': 'agente', 'texto': t['abertura']}, {'papel': 'agente', 'texto': t['proposta']}, {'papel': 'agente', 'texto': t['comparador']}]
        cards = [diagnostico, {'tipo': 'comparador', 'dados': o}]
        chips = [{'rotulo': 'Quero pagar tudo' if o['caminho'] == 'cobertura_curta' else 'Quero essa opção', 'acao': 'confirmar'},
                 {'rotulo': 'Prefiro continuar como está', 'acao': 'nao_quero', 'secundario': True}, chips_humano]
    elif m['cabe']:
        msgs = [{'papel': 'agente', 'texto': t['abertura']}, {'papel': 'agente', 'texto': t['proposta']}]
        cards = [diagnostico]
        chips = [chips_humano]
    else:
        msgs = [{'papel': 'agente', 'texto': t['abertura']}, {'papel': 'agente', 'texto': t['proposta']}]
        cards = [diagnostico, {'tipo': 'encaminhamento', 'dados': {'motivo': o['motivo'], 'texto': 'Você pode falar com uma pessoa do time a qualquer momento; ela recebe o contexto desta conversa.', 'contratado': False}}]
        chips = [chips_humano, {'rotulo': 'Agora não', 'acao': 'nao_quero', 'secundario': True}]
    ver_opcoes = resposta(nums, msgs, cards, chips)

    consigo = resposta(nums, [{'papel': 'agente', 'texto': t['consigo_pagar']}], [diagnostico],
                       [{'rotulo': 'Ver opções', 'acao': 'ver_opcoes'}, chips_humano] if not m['cabe'] else [chips_humano])
    por_que = resposta(nums, [{'papel': 'agente', 'texto': t['por_que_alta']}],
                       [{'tipo': 'diagnostico', 'dados': {'so_categorias': True, 'fatura': m['fatura'], 'flags': m['flags'], 'anomes': anomes}}],
                       [{'rotulo': 'Ver opções', 'acao': 'ver_opcoes'}, chips_humano])

    def intercepta(escolha: str) -> dict:
        pa = f['minimo'] if escolha == 'minimo' else f['pago_na_base']
        if o['recomendada'] is None:
            texto = t['nao_quero_minimo'] if escolha == 'minimo' else t['nao_quero_outro']
            return resposta(nums, [], [{'tipo': 'aviso', 'dados': {'texto': texto, 'paga_agora': pa}}])
        return resposta(nums, [], [{'tipo': 'insight', 'dados': {
            'estado': 'antes_de_confirmar', 'texto': t['intercepta'], 'paga_agora': pa,
            'botao_primario': {'rotulo': 'Ver opção', 'acao': 'ver_opcoes'},
            'botao_secundario': {'rotulo': 'Continuar com este valor', 'acao': 'nao_quero'}}}])

    if plano:
        nums.add([{'valor': v, 'origem': plano['origem'][k]} for k, v in plano.items() if k in plano['origem']])
        confirmar = resposta(nums, [{'papel': 'agente', 'texto': t['fecho']}],
                             [{'tipo': 'confirmacao', 'dados': {
                                 'resumo': t['confirmacao'], 'plano': plano, 'opcao': o['opcoes'][o['recomendada']],
                                 'teto_cartao_mes': o['teto_cartao_mes'],
                                 'aviso': 'Nada é contratado agora. Depende de aprovação do serviço de crédito e da sua confirmação no app.',
                                 'contratado': False, 'proximo_passo': 'Te acompanho nas próximas três faturas.'}}],
                             [{'rotulo': 'Avançar um mês (demo)', 'acao': 'avancar_mes'}, chips_humano])
    else:
        confirmar = None
    humano = resposta(nums, [{'papel': 'agente', 'texto': t['humano']}],
                      [{'tipo': 'encaminhamento', 'dados': {'motivo': 'pedido do cliente',
                                                            'texto': 'Encaminhado para uma pessoa do time de atendimento, com o contexto desta conversa. Nenhum produto foi oferecido.',
                                                            'contratado': False, 'status': 'encaminhado (simulado)'}}], [])
    aviso_registrado = {'tipo': 'aviso', 'dados': {'texto': 'Registrado. A IA não volta ao assunto até a próxima fatura.', 'contratado': False}}
    mensagem = {
        'ver_opcoes': ver_opcoes,
        'consigo_pagar': consigo,
        'por_que_alta': por_que,
        'pagar_minimo': intercepta('minimo'),
        'pagar_outro_valor': intercepta('outro'),
        'confirmar': confirmar or humano,
        'falar_com_pessoa': humano,
        'nao_quero': {'por_escolha': {
            'padrao': resposta(nums, [{'papel': 'agente', 'texto': t['nao_quero_padrao']}], [aviso_registrado], []),
            'pagar_minimo': resposta(nums, [{'papel': 'agente', 'texto': t['nao_quero_minimo']}], [aviso_registrado], []),
            'pagar_outro_valor': resposta(nums, [{'papel': 'agente', 'texto': t['nao_quero_outro']}], [aviso_registrado], [])}},
        'texto': resposta(nums, [{'papel': 'agente', 'texto': t['texto_livre']}], [],
                          [{'rotulo': 'Ver opções', 'acao': 'ver_opcoes'}, chips_humano]),
    }

    # ---- POST /api/avancar-mes (sem LLM)
    ciclos = []
    pl = plano
    if pl:
        for k in range(1, 4):
            c = acompanhar.ciclo(fonte, cid, pl, calendario.anomes_soma(anomes, k), taxas)
            pl = c['plano']
            nums.add(c['numeros'])
            dados_ciclo = {kk: v for kk, v in c.items() if kk != 'plano'}
            ciclos.append({'anomes': c['anomes'], 'fatura': c['fatura'], 'paga_inteira': c['paga_inteira'], 'ciclos_ok': c['ciclos_ok'],
                           'encerrado': c['encerrado'], 'mensagem': c['frase'],
                           'cards': [{'tipo': 'acompanhamento', 'dados': dados_ciclo}], 'numeros_validados': nums.lista(), 'simulado': True})
            if c['encerrado']:
                break

    # ---- GET /api/painel
    hist = fatura.historico(fonte, cid, 202512, 12)
    ultimo = None
    if ciclos:
        ultimo = {kk: v for kk, v in ciclos[-1]['cards'][0]['dados'].items()}
    juri = painel.juri({'motor': m, 'ofertas': o, 'plano': pl, 'ciclos': [ultimo] if ultimo else [], 'historico_faturas': hist,
                        'numeros_validados': nums.lista()})
    nums.add(juri['numeros'])
    juri['numeros_com_origem'] = nums.lista()
    juri['finops'] = {'chamadas_llm': 0, 'tokens_entrada': 0, 'tokens_saida': 0, 'latencia_p50_ms': None, 'latencia_p95_ms': None,
                      'custo_estimado': None,
                      'nota': 'modo mock: respostas gravadas, nenhuma chamada ao modelo; custo_estimado fica null porque não há preço com fonte em config/taxas.yaml'}
    juri['simulado_lista'] = juri.pop('simulado')
    juri['simulado'] = True

    # ---- GET /api/trace (a demo filtra pela etapa alcançada)
    t0 = datetime(anomes // 100, anomes % 100, max(f['vencimento_dia'] - 7, 1), 9, 0, tzinfo=timezone.utc)
    itens = [
        ('sessao', 'fatura.historico', {'cliente_id': cid[:8] + '…', 'anomes': anomes}, f'fatura reconstruída pelo modo {f["modo_na_base"]}',
         [{'valor': f['valor'], 'origem': 'capacidade.motor:fatura.valor'}, {'valor': f['minimo'], 'origem': 'capacidade.motor:fatura.minimo'}]),
        ('consentimento', 'registrar_consentimento', {'concedido': True, 'escopo': '90 dias', 'versao': VERSAO_CONSENTIMENTO}, 'consentimento registrado; ferramentas de dados liberadas (before_tool_callback)', []),
        ('consentimento', 'capacidade.motor', {'anomes': anomes, 'janela': m['janela']['meses_fechados']}, m['frase'],
         [{'valor': m[k], 'origem': m['origem'][k]} for k in ('renda_recorrente', 'fixos', 'essenciais', 'folga', 'falta', 'dias_ate_recebimento')]),
        ('consentimento', 'grupo.classificar', {'meses_considerados': m['grupo']['meses_considerados'], 'modo_atual': m['grupo']['modo_atual']},
         f'grupo {m["grupo"]["grupo"]} (flag calculada em código, fora do prompt)',
         [{'valor': m['grupo']['roladas_com_atual'], 'origem': 'capacidade.motor:grupo.roladas_com_atual'}]),
        ('consentimento', 'anomalia.renda', {'metodo': 'media_movel_lag'},
         ('renda irregular' if m['flags']['renda_irregular'] else 'renda regular') + f'; {len(m["flags"]["entradas_esporadicas"])} entrada(s) esporádica(s) sinalizada(s), fora da renda',
         [{'valor': e['mediana_mensal'], 'origem': f'capacidade.motor:flags.entradas_esporadicas[{i}].mediana_mensal'} for i, e in enumerate(m['flags']['entradas_esporadicas'])]),
        ('consentimento', 'anomalia.gastos', {'anomes': anomes}, f'{len(m["flags"]["gastos_atipicos"])} categoria(s) fora do padrão da janela',
         [{'valor': g['valor'], 'origem': f'capacidade.motor:flags.gastos_atipicos[{i}].valor'} for i, g in enumerate(m['flags']['gastos_atipicos'])]),
        ('analise', 'ofertas.montar', {'caminho': o['caminho'], 'liberacao': lib}, o['motivo'],
         [{'valor': op['custo_total'], 'origem': f'ofertas.montar:opcoes[{i}].custo_total'} for i, op in enumerate(o['opcoes'])]
         + [{'valor': o['continuar_no_rotativo']['custo_1_mes'], 'origem': 'ofertas.montar:continuar_no_rotativo.custo_1_mes'},
            {'valor': o['teto_cartao_mes'], 'origem': 'ofertas.montar:teto_cartao_mes'}]),
        ('analise', 'policy.travas', {'regras': ['parcela<=folga', 'custo<rotativo no mesmo horizonte', 'liberado', '1x/12m', 'conta voltou ao positivo', 'sem reincidência']},
         '; '.join([f'{op["produto"]}: ok' for op in o['opcoes']] + [f'{op["produto"]}: {", ".join(op["bloqueios"])}' for op in o['descartadas']]) or 'sem opções', []),
        ('analise', 'guardiao.after_model', {'conferido_contra': 'state.numeros_validados', 'lista_negra': ['seguro', 'cashback', 'pontos', 'cartão novo', 'investimento', 'condição vaga (config texto_condicao)']},
         '0 números removidos; 0 termos bloqueados', []),
    ]
    if plano:
        itens.append(('confirmar', 'ofertas.plano_de', {'opcao': plano['produto']}, 'plano registrado no state; nada contratado (depende de aprovação)',
                      [{'valor': plano['parcela'], 'origem': 'ofertas.plano_de:parcela'}, {'valor': plano['teto_cartao_mes'], 'origem': 'ofertas.plano_de:teto_cartao_mes'}]))
    for i, c in enumerate(ciclos):
        d = c['cards'][0]['dados']
        itens.append((f'avancar_mes_{i + 1}', 'acompanhar.ciclo', {'anomes': c['anomes'], 'llm': False}, d['frase'],
                      [{'valor': d['fatura'], 'origem': 'acompanhar.ciclo:fatura'}, {'valor': d['juros_evitados_acumulados'], 'origem': 'acompanhar.ciclo:juros_evitados_acumulados'},
                       {'valor': d['teto_cartao'], 'origem': 'acompanhar.ciclo:teto_cartao'}]))
    trace = [{'ordem': i, 'etapa': etapa, 'ferramenta': ferr, 'argumentos': args, 'resumo': resumo, 'numeros': ns,
              'duracao_ms': None, 'ts': t0.replace(minute=i).isoformat(), 'llm': ferr.startswith('guardiao')}
             for i, (etapa, ferr, args, resumo, ns) in enumerate(itens, 1)]

    return {
        'persona': chave, 'simulado': True, 'gerado_por': 'analise/gera_mock_demo.py (usa agent/cabe_core)',
        'gerado_em': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'nota': 'Plano B da demo. Números e formas dos cards vêm do cabe_core sobre data/extrato_sintetico.csv.gz e config/taxas.yaml; cada número tem origem.',
        'cliente_id': cid, 'anomes': anomes, 'liberacao_simulada': lib,
        'saude': {'ok': True, 'dados': 'mock', 'modelo': None},
        'sessao': sessao, 'consentimento': consent, 'consentimento_negado': consent_negado,
        'mensagem': mensagem, 'avancar_mes': ciclos, 'trace': trace, 'painel': juri,
    }


def main():
    taxas = config.carregar_taxas()
    fonte = dados.FonteCsv()
    SAIDA.mkdir(parents=True, exist_ok=True)
    for chave, p in PERSONAS.items():
        d = gerar(chave, p, fonte, taxas)
        (SAIDA / f'{chave}.json').write_text(json.dumps(d, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
        m = d['mensagem']['ver_opcoes']['cards'][0]['dados']
        o = d['mensagem']['ver_opcoes']['cards'][1]['dados'] if len(d['mensagem']['ver_opcoes']['cards']) > 1 else None
        print(f'\n== {chave} ({p["cliente_id"][:8]}, {p["anomes"]}) grupo {m["grupo"]["grupo"]} | {m["frase"]}')
        print(f'  fatura {brl(m["fatura"]["valor"])} mínimo {brl(m["fatura"]["minimo"])} venc dia {m["fatura"]["vencimento_dia"]} | renda {brl(m["renda_recorrente"])} dia {m["dia_recebimento"]} '
              f'| fixos {brl(m["fixos"])} essenciais {brl(m["essenciais"])} folga {brl(m["folga"])} falta {brl(m["falta"])} dias {m["dias_ate_recebimento"]}')
        if o:
            c = o['continuar_no_rotativo']
            print(f'  caminho {o["caminho"]} | paga agora {brl(o["pagar_agora"])} financia {brl(o["valor_financiado"])} | rotativo 1 mês {brl(c["custo_1_mes"])} teto {brl(c["custo_ate_teto"])} '
                  f'| se mínimo: não pago {brl(c["se_pagar_minimo"]["nao_pago"])} juros {brl(c["se_pagar_minimo"]["custo_1_mes"])} | teto cartão {brl(o["teto_cartao_mes"])}')
            for i, op in enumerate(o['opcoes']):
                det = f'{op["dias"]} dias' if op.get('dias') else f'{op["n_parcelas"]}x {brl(op["parcela"])}'
                print(f'   {"*" if i == o["recomendada"] else " "} {op["produto"]:<20} {det:<20} custo {brl(op["custo_total"]):>12} termina {mes(op["termina_em"])} taxa {op["rotulo_taxa"]}')
        for c in d['avancar_mes']:
            dd = c['cards'][0]['dados']
            print(f'  {c["mensagem"]} | evitados {brl(dd["juros_evitados_acumulados"])} próxima parcela {brl(dd["proxima_parcela"])} teto {brl(dd["teto_cartao"])}')
        print(f'  números validados no fim: {len(d["painel"]["numeros_com_origem"])}')
    (SAIDA / 'saude.json').write_text(json.dumps({'ok': True, 'dados': 'mock', 'modelo': None, 'nota': 'respostas gravadas por analise/gera_mock_demo.py'}, ensure_ascii=False, indent=1), encoding='utf-8')
    print('\nmocks gravados em', SAIDA)


if __name__ == '__main__':
    main()
