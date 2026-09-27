"""Gera as respostas gravadas da demo (demo/mock/*.json) para as duas personas, usando o cabe_core real.

Plano B da demo: quando a API não responde (ou com ?mock=1), demo/app.js lê estes JSONs.
Os números e as formas dos cards vêm de agent/cabe_core (capacidade.motor, ofertas.montar, ofertas.plano_de,
acompanhar.ciclo, painel.juri); este script só acrescenta a camada de conversa (mensagens, chips, trace, FinOps).
Nenhum número é calculado aqui. Cada resposta carrega numeros_validados acumulados, como o state do agente.

As mensagens seguem os exemplos do prompt da Gi (docs/prompt-gi-2026-09-27.md: I2/I3 para o card, C1/C2 para a
conversa, C3 para a recusa, C4 sem oferta, C5 para "por que veio alta", C6 para proteção) adaptados aos números do
núcleo, e trazem os campos do formato dela: acao, oferta_id, numeros_citados, validador (gravado, sem chamada ao
modelo), checagens em código e o registro do turno para o painel da banca. Tudo marcado como resposta gravada.
Para a Ana há a variante `com_pix` (contas refeitas depois de "É renda"), que demo/app.js liga ao receber
confirmar_entrada_regular. Como no modo gi ao vivo, com PIX regular a confirmar o `ver_opcoes` só pergunta se o PIX é
renda (acao nenhuma, chips "É renda" / "Não é renda"), sem oferta; a oferta aparece depois da resposta
(confirmar_entrada_regular -> variante com_pix; nao_contar_pix -> oferta com as contas sem o PIX).
O painel gravado traz `turnos` da jornada (checagens ok, veredito "aprovado (gravado)") e o bloco `finops` com custo
US$ 0 pelo preço com fonte de config/finops.yaml (respostas gravadas: nenhuma chamada ao modelo).

Rodar da raiz (o pacote cabe_no_bolso importa google-adk, então use o ambiente do agente):
    cd agent && uv run python ../analise/gera_mock_demo.py     (sem rede, sem LLM)
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / 'agent'))

from cabe_core import acompanhar, calendario, capacidade, config, dados, fatura, finops, ofertas, painel, travas  # noqa: E402
from cabe_core.dinheiro import brl  # noqa: E402
from cabe_no_bolso import policy  # noqa: E402

SAIDA = RAIZ / 'demo' / 'mock'
PERSONAS = {
    'ana': dict(cliente_id='755627ab-804b-4211-b0ea-f4ebacc58716', anomes=202508, apelido='Ana', perfil='CLT com financiamento'),
    'bruno': dict(cliente_id='3e7d20b2-4c4f-450a-bbd2-e60bfda81f0b', anomes=202509, apelido='Bruno', perfil='CLT sem financiamento'),
}
VERSAO_CONSENTIMENTO = 'consentimento-v1-2026-09-27'
FRASE_VALIDADOR = 'Nenhuma mensagem chega ao cliente sem passar pelo validador'
# mesmo esquema de ids de cabe_no_bolso.contexto e server.conversa
TIPO_OFERTA = {'cheque_especial': 'cobertura_cheque_especial', 'consignado_clt': 'credito_consignado', 'consignado_inss': 'credito_consignado',
               'credito_pessoal': 'credito_pessoal', 'parcelamento_fatura': 'parcelamento_fatura'}
PREFIXO_ID = {'cobertura_cheque_especial': 'cob', 'credito_consignado': 'con', 'credito_pessoal': 'cp', 'parcelamento_fatura': 'par'}
RE_NUM = re.compile(r'R\$\s?\d{1,3}(?:\.\d{3})*(?:,\d{2})?|\b\d{1,3}(?:,\d{1,2})?%|\bdia\s\d{1,2}\b|\b\d{1,3}\s(?:dias?|parcelas?|vezes|meses|faturas?)\b')


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


def ids_ofertas(o: dict) -> list[str]:
    out, contagem = [], {}
    for op in o.get('opcoes') or []:
        tipo = TIPO_OFERTA.get(op['produto'], op['produto'])
        contagem[tipo] = contagem.get(tipo, 0) + 1
        out.append(f"{PREFIXO_ID.get(tipo, 'of')}_{contagem[tipo]:02d}")
    return out


def citados(textos: list[str]) -> list[str]:
    vistos, out = set(), []
    for t in textos:
        for m in RE_NUM.finditer(t or ''):
            if m.group(0) not in vistos:
                vistos.add(m.group(0))
                out.append(m.group(0))
    return out


VALIDADOR_GRAVADO = {'aplicado': True, 'aprovado': True, 'violacoes': [], 'orientacao_para_regenerar': None, 'chamadas': 0, 'gravado': True, 'veredito': 'aprovado (gravado)',
                     'motivo': 'resposta gravada: aprovada na geração dos mocks (analise/gera_mock_demo.py); nenhuma chamada ao modelo'}


def checagens_gravadas(msgs: list[str], acao: str, oferta_id: str | None, ids: list[str], modo: str = 'conversa') -> dict:
    return {
        'formato': {'ok': True, 'detalhe': 'resposta gravada no formato do prompt (mensagens, acao, oferta_id, numeros_citados)'},
        'numeros': {'ok': True, 'citados': len(citados(msgs)), 'no_texto': len(citados(msgs)), 'sem_origem': [], 'fora_de_numeros_citados': [],
                    'detalhe': 'cada número do texto veio do cabe_core e tem origem em numeros_validados'},
        'oferta': {'ok': True, 'oferta_id': oferta_id, 'liberadas': ids},
        'consentimento': {'ok': True, 'consentimento': True, 'acao': acao},
        'tamanho': ({'ok': all(len(t) <= 160 for t in msgs), 'limite': 160, 'maior': max((len(t) for t in msgs), default=0)} if modo == 'insight'
                    else {'ok': len(msgs) <= 3, 'limite': 3, 'mensagens': len(msgs)}),
        'termos_proibidos': {'ok': True, 'encontrados': []},
        'mensagem_segura': False, 'ok': True, 'gravado': True,
    }


class Turnos:
    def __init__(self):
        self.n = 0
        self.itens: list[dict] = []

    def novo(self, acao: str, modo: str, gatilho: str | None, msgs: list[str], acao_agente: str, oferta_id: str | None, ids: list[str]) -> dict:
        self.n += 1
        c = checagens_gravadas(msgs, acao_agente, oferta_id, ids, modo)
        t = {'ordem': self.n, 'ts': None, 'acao': acao, 'modo': modo, 'gatilho': gatilho, 'llm': False, 'modo_conversa': 'gravado',
             'checagens': c, 'validador': dict(VALIDADOR_GRAVADO), 'regeneracoes': 0, 'mensagem_segura': False,
             'chamadas_llm': 0, 'chamadas_validador': 0, 'tokens_entrada': 0, 'tokens_saida': 0, 'latencia_ms': None,
             'custo_usd': 0.0, 'custo_agente_usd': 0.0, 'custo_validador_usd': 0.0, 'moeda': 'USD', 'modelo': None,
             'acao_agente': acao_agente, 'oferta_id': oferta_id, 'numeros_citados': citados(msgs), 'gravado': True}
        self.itens.append(t)
        return t


def resposta(nums: Numeros, turnos: Turnos, acao_cliente: str, gatilho: str | None, mensagens: list[str], cards: list[dict],
             sugestoes: list[dict] | None = None, acao: str = 'nenhuma', oferta_id: str | None = None, ids: list[str] | None = None) -> dict:
    ids = ids or []
    r = {'mensagens': [{'papel': 'agente', 'texto': t} for t in mensagens], 'cards': cards, 'numeros_validados': nums.lista(),
         'guardiao': {'removidos': [], 'termos_bloqueados': []}, 'acao': acao, 'oferta_id': oferta_id, 'numeros_citados': citados(mensagens),
         'validador': dict(VALIDADOR_GRAVADO), 'checagens': checagens_gravadas(mensagens, acao, oferta_id, ids),
         'gatilho': gatilho, 'modo': 'mock', 'modo_conversa': 'gravado', 'simulado': True, 'gravado': True}
    r['turno'] = turnos.novo(acao_cliente, 'conversa', gatilho, mensagens, acao, oferta_id, ids)
    if sugestoes is not None:
        r['sugestoes'] = sugestoes
    return r


def textos(p: dict, m: dict, o: dict, plano: dict | None, contar_pix: bool) -> dict:
    """Mensagens no estilo dos exemplos da Gi, com os números do núcleo (R$ com centavos, como nos cards)."""
    nome = p['apelido']
    f = m['fatura']
    fat, venc, folga, falta = brl(f['valor']), f['vencimento_dia'], brl(max(0, m['folga'])), brl(m['falta'])
    dias, receb = m['dias_ate_recebimento'], m['dia_recebimento']
    cont = o['continuar_no_rotativo']
    rec = o['opcoes'][o['recomendada']] if o['recomendada'] is not None else None
    ids = ids_ofertas(o)
    oid = ids[o['recomendada']] if rec else None
    juros_min = brl(cont['se_pagar_minimo']['custo_1_mes'])
    t = {
        'oferta_id': oid, 'ids': ids,
        'humano': ['Vou te passar para alguém da equipe, que pode olhar a sua situação com calma e encontrar o melhor caminho.'],
        'texto_livre': ['Não consigo ajudar com isso por aqui. Posso te mostrar as formas de pagar a fatura, ou te passar para alguém da equipe.'],
        'nao_quero_padrao': ['Tudo bem, a escolha é sua. Não volto a esse assunto nesta fatura.', f'Se mudar de ideia até o dia {venc}, é só me chamar.'],
        'nao_quero_minimo': [f'Tudo bem, a escolha é sua. Só para você saber: pagando o mínimo, os juros e outros custos do próximo mês ficam em {juros_min}.',
                             f'Se mudar de ideia até o dia {venc}, é só me chamar.'],
        'nao_quero_outro': [f'Tudo bem, a escolha é sua. Só para você saber: com esse valor, os juros e outros custos do próximo mês ficam em '
                            f'{brl(cont["se_pagar_valor_gravado"]["custo_1_mes"])}.', f'Se mudar de ideia até o dia {venc}, é só me chamar.'],
        'pix_pergunta': f'Antes, uma pergunta: vi um PIX de cerca de {brl(m["flags"]["pix_mensal_mediana"])} entrando todo mês. Ele é renda sua?',
        'pix_fora': ['Certo, deixo o PIX de fora das contas.', 'Quer ver os detalhes da opção?'],
    }
    prefixo_pix = 'Refiz as contas contando o PIX. ' if contar_pix else ''
    if m['cabe']:
        t.update(insight=(f'Pela previsão, a fatura de {fat} cabe no seu saldo até o dia {venc}. Se entrarem novos gastos, a previsão pode mudar.', 'Ver fatura', 'nenhuma', 'cabe'),
                 ver_opcoes=([f'Oi, {nome}. Sua fatura fechou em {fat} e vence dia {venc}. Pela previsão, ela cabe no seu saldo até lá.',
                              'Se entrarem novos gastos ou mudarem os recebimentos, a previsão pode mudar.'], 'nenhuma'),
                 consigo_pagar=([f'Pela previsão, sim: a fatura de {fat} cabe no seu saldo até o dia {venc}.'], 'nenhuma'),
                 confirmar=t['humano'], fecho=None, intercepta=None)
    elif o['caminho'] == 'cobertura_curta' and rec:
        t.update(
            insight=(f'{nome}, a fatura fechou em {fat}. Até o dia {venc}, a previsão é ter {folga}. Temos um jeito de pagar tudo.', 'Ver opções', 'abrir_chat', 'falta_pontual'),
            ver_opcoes=([f'{prefixo_pix}Oi, {nome}. Sua fatura fechou em {fat} e vence dia {venc}. Até lá, a previsão é ter {folga} na conta, e seu próximo salário cai dia {receb}.'
                         if not contar_pix else f'Refiz as contas contando o PIX. Até o dia {venc}, a previsão é ter {folga} na conta; faltam {falta} para a fatura de {fat}.',
                         f'Uma opção é pagar a fatura inteira no dia {venc} usando o limite da conta por {dias} dias, até o salário. O custo total fica em {brl(rec["custo_total"])}.',
                         'Quer ver os detalhes?'], 'mostrar_oferta'),
            consigo_pagar=([f'Pela previsão, até o dia {venc} você tem {folga} na conta e a fatura é de {fat}: faltam {falta}, e o salário entra em {dias} dias.',
                            'Quer ver um jeito de pagar tudo?'], 'nenhuma'),
            confirmar=['Combinado. Vou abrir o resumo para você conferir antes de confirmar.',
                       f'Quando o salário entrar no dia {receb}, o limite da conta é coberto na hora, e eu te aviso quando zerar.'],
            confirmacao=(f'Fica assim: no dia {venc} a fatura de {fat} é paga inteira, {brl(o["pagar_agora"])} da sua conta e '
                         f'{brl(rec["valor_financiado"])} pelo limite. No dia {receb} o salário cobre o limite; custo {brl(rec["custo_total"])}.'),
            intercepta='Antes de confirmar: tem um jeito de pagar a fatura inteira com uma cobertura que cabe no seu mês.',
        )
    elif o['caminho'] == 'parcelamento' and rec:
        t.update(
            insight=(f'{nome}, sua fatura fechou em {fat} e vence dia {venc}. Cabe pagar {brl(o["pagar_agora"])}; faltam {falta}. Dá para juntar o que falta numa parcela que cabe.',
                     'Conversar com o ia.i', 'abrir_chat', 'falta_que_se_repete'),
            ver_opcoes=([f'Oi, {nome}. Sua fatura fechou em {fat} e vence dia {venc}. Pelo seu mês, cabe pagar {brl(o["pagar_agora"])} e faltam {falta}.',
                         f'Tenho uma ideia que pode aliviar: {rec["rotulo_cliente"]}, que paga o que falta agora e é devolvido em {rec["n_parcelas"]} parcelas de '
                         f'{brl(rec["parcela"])}. Assim seu limite do cartão volta inteiro.',
                         f'Quer que eu mostre os detalhes, com o custo total de {brl(rec["custo_total"])}?'], 'mostrar_oferta'),
            consigo_pagar=([f'Pelo seu mês, cabe pagar {folga} no dia {venc}. Para a fatura de {fat}, faltam {falta}.',
                            'Quer ver um jeito de juntar o que falta numa parcela que cabe?'], 'nenhuma'),
            confirmar=['Combinado. Vou abrir o resumo para você conferir antes de confirmar.',
                       f'A parcela de {brl(rec["parcela"])} vence junto com a fatura; te acompanho nas próximas 3 faturas.'],
            confirmacao=(f'Fica assim: {brl(o["pagar_agora"])} agora; {falta} em {rec["n_parcelas"]} parcelas de {brl(rec["parcela"])}; '
                         f'termina em {mes(rec["termina_em"])}; até a próxima fatura cabem {brl(o["teto_cartao_mes"])} no cartão.'),
            intercepta='Antes de confirmar: tem um jeito de pagar a fatura inteira com uma parcela que cabe no seu mês.',
        )
    else:
        t.update(insight=(f'A fatura fechou em {fat} e vence dia {venc}. Veja as formas de pagar.', 'Ver formas de pagar', 'ver_formas_de_pagar', 'sem_credito'),
                 ver_opcoes=([f'Sua fatura fechou em {fat} e vence dia {venc}. Estas são as formas de pagar.',
                              'Se quiser, alguém da equipe pode olhar com você o melhor caminho para este mês.'], 'mostrar_formas_de_pagar'),
                 consigo_pagar=([f'Pelo seu mês, cabe pagar {folga} no dia {venc}. Para a fatura de {fat}, faltam {falta}.',
                                 'Quer que eu explique as formas de pagar, ou prefere falar com alguém da equipe?'], 'mostrar_formas_de_pagar'),
                 confirmar=t['humano'], confirmacao='', intercepta=None)
    comp = m['flags']['composicao_fatura']
    cats = comp.get('categorias') or []
    if len(cats) > 1:
        pesou = f'O que mais pesou este mês foram {cats[0]["categoria"].lower()}, {brl(cats[0]["valor"])}, e {cats[1]["categoria"].lower()}, {brl(cats[1]["valor"])}.'
    elif cats:
        pesou = f'O que mais pesou este mês foi {cats[0]["categoria"].lower()}, {brl(cats[0]["valor"])}.'
    else:
        pesou = f'Nesta fatura entraram {brl(comp["total_compras"])} de compras de {mes(comp["anomes_compras"])}.'
    pc = comp.get('parcelas_em_curso') or {}
    if pc.get('quantidade'):
        pesou += f' Também entrou {brl(pc["valor"])} de parcelas de compras anteriores.'
    t['por_que_alta'] = [pesou, 'Quer ver as opções para pagar essa fatura?']
    return t


def gerar(chave: str, p: dict, fonte, taxas: dict, contar_pix: bool = False) -> dict:
    cid, anomes = p['cliente_id'], p['anomes']
    nums = Numeros()
    turnos = Turnos()
    m = capacidade.motor(fonte, cid, anomes, taxas, contar_pix=contar_pix)
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
    t = textos(p, m, o, plano, contar_pix)
    ids, oid = t['ids'], t['oferta_id']
    f = m['fatura']
    data_simulada = f'{max(f["vencimento_dia"] - 7, 1):02d}/{anomes % 100:02d}/{anomes // 100}'
    seguro = f'Sua fatura fechou em {brl(f["valor"])} e vence dia {f["vencimento_dia"]}. Veja as formas de pagar.'
    insight_seguro = {'estado': 'sem_adesao', 'texto': seguro, 'botao': {'rotulo': 'Ver formas de pagar', 'acao': 'ver_formas_de_pagar'},
                      'botao_secundario': None, 'gerado_por': 'codigo', 'numeros_citados': citados([seguro]), 'validador': None}
    formas = {'tipo': 'formas_de_pagar', 'dados': {'texto': 'Formas de pagar esta fatura.', 'opcoes': [
        {'rotulo': 'Total', 'valor': f['valor'], 'acao': 'pagar_total'}, {'rotulo': 'Mínimo', 'valor': f['minimo'], 'acao': 'pagar_minimo'},
        {'rotulo': 'Outro valor', 'valor': None, 'acao': 'pagar_outro_valor'}], 'contratado': False}}

    # ---- POST /api/sessao (sem consentimento: o insight do card é a mensagem segura)
    nums_sessao = Numeros()
    nums_sessao.add([{'valor': f['valor'], 'origem': 'capacidade.motor:fatura.valor'},
                     {'valor': f['vencimento_dia'], 'origem': 'capacidade.motor:fatura.vencimento_dia'},
                     {'valor': f['minimo'], 'origem': 'capacidade.motor:fatura.minimo'},
                     {'valor': f['pago_na_base'], 'origem': 'capacidade.motor:fatura.pago_na_base'}])
    sessao = {
        'sessao_id': f'mock-{chave}', 'modo': 'mock', 'modo_conversa': 'gravado',
        'cliente': {'apelido': p['apelido'], 'perfil': p['perfil'], 'grupo_rotulo': m['grupo']['rotulo'], 'grupo': m['grupo']['grupo']},
        'anomes': anomes, 'mes_rotulo': mes(anomes), 'data_simulada': data_simulada,
        'fatura': {'valor': f['valor'], 'vencimento_dia': f['vencimento_dia'], 'minimo': f['minimo'],
                   'opcoes_pagamento': [{'rotulo': 'Total', 'valor': f['valor'], 'acao': 'pagar_total'},
                                        {'rotulo': 'Mínimo', 'valor': f['minimo'], 'acao': 'pagar_minimo'},
                                        {'rotulo': 'Outro valor', 'valor': None, 'acao': 'pagar_outro_valor', 'valor_gravado': f['pago_na_base']}]},
        'consentimento': False, 'insight': insight_seguro, 'numeros_validados': nums_sessao.lista(), 'simulado': True,
    }
    nums.add(nums_sessao.lista())

    # ---- POST /api/consentimento (com adesão o motor roda no gatilho e o insight aparece no cartão: modo insight, gatilho fechamento)
    nums.add(m['numeros'])
    nums.add(o['numeros'])
    ins_texto, ins_rotulo, ins_acao, ins_estado = t['insight']
    insight = {'estado': ins_estado, 'texto': ins_texto, 'botao': ({'rotulo': ins_rotulo, 'acao': 'ver_opcoes' if ins_acao == 'abrir_chat' else ins_acao} if ins_acao != 'nenhuma' else None),
               'botao_secundario': {'rotulo': 'Agora não', 'acao': 'nenhuma'} if ins_acao == 'abrir_chat' else None,
               'gerado_por': 'gravado (exemplo da Gi com os números do núcleo)', 'numeros_citados': citados([ins_texto]),
               'validador': dict(VALIDADOR_GRAVADO), 'regeneracoes': 0, 'mensagem_segura': False}
    turno_insight = turnos.novo('consentimento', 'insight', 'fechamento', [ins_texto], 'nenhuma', None, ids)
    consent = {
        'consentimento': True,
        'registro': {'data': f'{data_simulada} (simulada)', 'versao_texto': VERSAO_CONSENTIMENTO,
                     'escopo': 'últimos 90 dias de conta e cartão; revogável a qualquer momento'},
        'insight': insight, 'turno': turno_insight, 'modo': 'mock', 'modo_conversa': 'gravado',
        'numeros_validados': nums.lista(), 'simulado': True,
    }
    consent_negado = {'consentimento': False,
                      'registro': {'data': f'{data_simulada} (simulada)', 'versao_texto': VERSAO_CONSENTIMENTO,
                                   'escopo': 'recusado; o pedido volta no máximo uma vez por mês, nunca no meio do pagamento'},
                      'insight': None, 'insight_seguro': insight_seguro, 'numeros_validados': [], 'simulado': True}

    # ---- POST /api/mensagem
    diagnostico = {'tipo': 'diagnostico', 'dados': m}
    chip_humano = {'rotulo': 'Falar com uma pessoa', 'acao': 'falar_com_pessoa', 'secundario': True}
    chips_pix = [{'rotulo': 'É renda', 'acao': 'confirmar_entrada_regular'}, {'rotulo': 'Não é renda', 'acao': 'nao_contar_pix', 'secundario': True}]
    pix_pendente = bool(m['flags'].get('pix_regular')) and not contar_pix
    msgs, acao_vo = t['ver_opcoes']
    chips_oferta = [{'rotulo': 'Quero pagar tudo' if o['caminho'] == 'cobertura_curta' else 'Quero essa opção', 'acao': 'confirmar'},
                    {'rotulo': 'Prefiro continuar como está', 'acao': 'nao_quero', 'secundario': True}, chip_humano]
    if o['recomendada'] is not None and pix_pendente:
        # como o modo gi ao vivo (diretriz entrada_regular_a_confirmar): pergunta uma vez se o PIX é renda ANTES de qualquer
        # oferta; acao nenhuma, só os chips "É renda" / "Não é renda". A oferta vem na resposta seguinte.
        msgs, acao_vo = [msgs[0], t['pix_pergunta']], 'nenhuma'
        cards = [diagnostico]
        chips = chips_pix + [chip_humano]
    elif o['recomendada'] is not None:
        cards = [diagnostico, {'tipo': 'comparador', 'dados': o}]
        chips = chips_oferta
    elif m['cabe']:
        cards = [diagnostico]
        chips = [chip_humano]
    else:
        cards = [diagnostico, {'tipo': 'encaminhamento', 'dados': {'motivo': o['motivo'], 'texto': 'Você pode falar com uma pessoa do time a qualquer momento; ela recebe o contexto desta conversa.', 'contratado': False}}, formas]
        chips = [chip_humano, {'rotulo': 'Agora não', 'acao': 'nao_quero', 'secundario': True}]
    ver_opcoes = resposta(nums, turnos, 'ver_opcoes', 'fechamento', msgs, cards, chips, acao_vo, oid if acao_vo == 'mostrar_oferta' else None, ids)

    msgs_cp, acao_cp = t['consigo_pagar']
    consigo = resposta(nums, turnos, 'consigo_pagar', 'pergunta_cliente', msgs_cp, [diagnostico] + ([formas] if acao_cp == 'mostrar_formas_de_pagar' else []),
                       [{'rotulo': 'Ver opções', 'acao': 'ver_opcoes'}, chip_humano] if not m['cabe'] else [chip_humano], acao_cp, None, ids)
    por_que = resposta(nums, turnos, 'por_que_alta', 'pergunta_cliente', t['por_que_alta'],
                       [{'tipo': 'diagnostico', 'dados': {'so_categorias': True, 'fatura': m['fatura'], 'flags': m['flags'], 'anomes': anomes}}],
                       [{'rotulo': 'Ver opções', 'acao': 'ver_opcoes'}, chip_humano], 'nenhuma', None, ids)

    def intercepta(escolha: str) -> dict:
        pa = f['minimo'] if escolha == 'minimo' else f['pago_na_base']
        if o['recomendada'] is None:   # sem oferta liberada o gatilho não gera texto: o cliente segue para a confirmação (R18)
            texto = t['nao_quero_minimo'][0] if escolha == 'minimo' else t['nao_quero_outro'][0]
            return resposta(nums, turnos, 'pagar_' + ('minimo' if escolha == 'minimo' else 'outro_valor'), 'pagar_outro_valor', [],
                            [{'tipo': 'aviso', 'dados': {'texto': texto, 'paga_agora': pa, 'contratado': False}}], [], 'nenhuma', None, ids)
        return resposta(nums, turnos, 'pagar_' + ('minimo' if escolha == 'minimo' else 'outro_valor'), 'pagar_outro_valor', [t['intercepta']],
                        [{'tipo': 'insight', 'dados': {
                            'estado': 'antes_de_confirmar', 'texto': t['intercepta'], 'paga_agora': pa,
                            'botao_primario': {'rotulo': 'Ver opção', 'acao': 'ver_opcoes'},
                            'botao_secundario': {'rotulo': 'Continuar com este valor', 'acao': 'nao_quero'}}}], [], 'nenhuma', None, ids)

    if plano:
        nums.add([{'valor': v, 'origem': plano['origem'][k]} for k, v in plano.items() if k in plano['origem']])
        confirmar = resposta(nums, turnos, 'confirmar', None, t['confirmar'],
                             [{'tipo': 'confirmacao', 'dados': {
                                 'resumo': t['confirmacao'], 'plano': plano, 'opcao': o['opcoes'][o['recomendada']],
                                 'teto_cartao_mes': o['teto_cartao_mes'],
                                 'aviso': 'Nada é contratado agora. Depende de aprovação do serviço de crédito e da sua confirmação no app.',
                                 'contratado': False, 'proximo_passo': 'Te acompanho nas próximas três faturas.'}}],
                             [{'rotulo': 'Avançar um mês (demo)', 'acao': 'avancar_mes'}, chip_humano], 'abrir_resumo_contrato', oid, ids)
    else:
        confirmar = None
    humano = resposta(nums, turnos, 'falar_com_pessoa', None, t['humano'],
                      [{'tipo': 'encaminhamento', 'dados': {'motivo': 'pedido do cliente',
                                                            'texto': 'Encaminhado para uma pessoa do time de atendimento, com o contexto desta conversa. Nenhum produto foi oferecido.',
                                                            'contratado': False, 'status': 'encaminhado (simulado)'}}], [], 'transferir_humano', None, ids)
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
            'padrao': resposta(nums, turnos, 'nao_quero', None, t['nao_quero_padrao'], [aviso_registrado], [], 'nenhuma', None, ids),
            'pagar_minimo': resposta(nums, turnos, 'nao_quero', None, t['nao_quero_minimo'], [aviso_registrado], [], 'nenhuma', None, ids),
            'pagar_outro_valor': resposta(nums, turnos, 'nao_quero', None, t['nao_quero_outro'], [aviso_registrado], [], 'nenhuma', None, ids)}},
        'nao_contar_pix': (resposta(nums, turnos, 'nao_contar_pix', 'fechamento', [t['pix_fora'][0]] + t['ver_opcoes'][0][1:],
                                    [diagnostico, {'tipo': 'comparador', 'dados': o}], chips_oferta, 'mostrar_oferta', oid, ids)
                           if o['recomendada'] is not None else
                           resposta(nums, turnos, 'nao_contar_pix', 'fechamento', t['pix_fora'][:1], [], [chip_humano], 'nenhuma', None, ids)),
        'texto': resposta(nums, turnos, 'texto', 'pergunta_cliente', t['texto_livre'], [],
                          [{'rotulo': 'Ver opções', 'acao': 'ver_opcoes'}, chip_humano], 'nenhuma', None, ids),
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
    # jornada gravada que a banca percorre: consentimento (insight) -> ver_opcoes -> [PIX] -> confirmar; a demo usa estes turnos
    # na aba Validador/FinOps enquanto a banca ainda não conversou, e os que ela percorreu depois
    jornada = ['consentimento', 'ver_opcoes'] + (['nao_contar_pix'] if pix_pendente else []) + (['confirmar'] if plano else ['falar_com_pessoa'])
    turnos_gravados = []
    for k, nome in enumerate(jornada, 1):
        tt = next((x for x in turnos.itens if x['acao'] == nome), None)
        if tt:
            turnos_gravados.append({**tt, 'ordem': k})
    preco = finops.preco_de(finops.MODELO_PADRAO)
    proj = finops.projecao(0.0)
    juri['finops'] = {'chamadas_llm': 0, 'chamadas_validador': 0, 'tokens_entrada': 0, 'tokens_saida': 0, 'tokens_entrada_validador': 0, 'tokens_saida_validador': 0,
                      'latencia_p50_ms': None, 'latencia_p95_ms': None, 'custo_estimado': 0.0, 'custo_acumulado_sessao_usd': 0.0, 'custo_agente_usd': 0.0,
                      'custo_validador_usd': 0.0, 'moeda': 'USD', 'por_papel': finops.custo_por_papel({}, None, None),
                      'custo_por_turno': [{'ordem': x['ordem'], 'acao': x['acao'], 'modo': x['modo'], 'llm': False, 'chamadas_llm': 0, 'chamadas_validador': 0,
                                           'tokens_entrada': 0, 'tokens_saida': 0, 'latencia_ms': None, 'custo_usd': 0.0, 'custo_agente_usd': 0.0,
                                           'custo_validador_usd': 0.0, 'regeneracoes': 0, 'mensagem_segura': False, 'gravado': True} for x in turnos_gravados],
                      'preco_fonte': preco['fonte'], 'vigencia': preco['vigencia'], 'preco_status': preco['status'],
                      'preco_entrada_por_milhao_usd': preco['entrada_por_milhao_usd'], 'preco_saida_por_milhao_usd': preco['saida_por_milhao_usd'],
                      'projecao_piloto': proj, 'teto_chamadas_por_sessao': finops.teto_chamadas_por_sessao(),
                      'modelo': None, 'modelo_validador': None, 'rotulo_modelo': 'nenhum (mock)', 'modo': 'mock', 'modo_conversa': 'gravado',
                      'nota': ('modo mock: respostas gravadas, nenhuma chamada ao modelo, custo US$ 0. Com a API, o custo por turno e por sessão é '
                               f'tokens x preço de config/finops.yaml ({finops.MODELO_PADRAO}: US$ {preco["entrada_por_milhao_usd"]} entrada / '
                               f'US$ {preco["saida_por_milhao_usd"]} saída por milhão de tokens)')}
    juri['simulado_lista'] = juri.pop('simulado')
    juri['simulado'] = True
    juri['modo_conversa'] = 'gravado'
    juri['rotulo_modelo'] = 'nenhum (mock)'
    juri['turnos'] = turnos_gravados   # jornada gravada; a demo troca pelos turnos que a banca percorreu quando houver
    juri['validador'] = {'ativo': True, 'gravado': True, 'modelo': None, 'frase': FRASE_VALIDADOR, 'turnos': len(turnos_gravados), 'turnos_com_llm': 0,
                         'aprovados': None, 'reprovados': 0, 'sem_veredito': 0, 'regeneracoes': 0, 'mensagens_seguras': 0, 'checagens_reprovadas': 0,
                         'nota': 'respostas gravadas: cada turno foi aprovado nas checagens em código na geração dos mocks (veredito "aprovado (gravado)", '
                                 'sem chamada ao modelo nem ao validador); com a API, cada turno passa pelas checagens em código e pelo validador'}

    # ---- GET /api/trace (a demo filtra pela etapa alcançada)
    t0 = datetime(anomes // 100, anomes % 100, max(f['vencimento_dia'] - 7, 1), 9, 0, tzinfo=timezone.utc)
    itens = [
        ('sessao', 'fatura.historico', {'cliente_id': cid[:8] + '…', 'anomes': anomes}, f'fatura reconstruída pelo modo {f["modo_na_base"]}',
         [{'valor': f['valor'], 'origem': 'capacidade.motor:fatura.valor'}, {'valor': f['minimo'], 'origem': 'capacidade.motor:fatura.minimo'}]),
        ('consentimento', 'registrar_consentimento', {'concedido': True, 'escopo': '90 dias', 'versao': VERSAO_CONSENTIMENTO}, 'consentimento registrado; ferramentas de dados liberadas (before_tool_callback)', []),
        ('consentimento', 'capacidade.motor', {'anomes': anomes, 'janela': m['janela']['meses_fechados'], 'contar_pix': contar_pix}, m['frase'],
         [{'valor': m[k], 'origem': m['origem'][k]} for k in ('renda_recorrente', 'fixos', 'essenciais', 'folga', 'falta', 'dias_ate_recebimento')]),
        ('consentimento', 'grupo.classificar', {'meses_considerados': m['grupo']['meses_considerados'], 'modo_atual': m['grupo']['modo_atual']},
         f'grupo {m["grupo"]["grupo"]} (flag calculada em código, fora do prompt)',
         [{'valor': m['grupo']['roladas_com_atual'], 'origem': 'capacidade.motor:grupo.roladas_com_atual'}]),
        ('consentimento', 'anomalia.renda', {'metodo': 'media_movel_lag'},
         ('renda irregular' if m['flags']['renda_irregular'] else 'renda regular') + f'; {len(m["flags"]["entradas_esporadicas"])} entrada(s) esporádica(s) sinalizada(s), fora da renda',
         [{'valor': e['mediana_mensal'], 'origem': f'capacidade.motor:flags.entradas_esporadicas[{i}].mediana_mensal'} for i, e in enumerate(m['flags']['entradas_esporadicas'])]),
        ('consentimento', 'anomalia.gastos', {'anomes': anomes}, f'{len(m["flags"]["gastos_atipicos"])} categoria(s) fora do padrão da janela',
         [{'valor': g['valor'], 'origem': f'capacidade.motor:flags.gastos_atipicos[{i}].valor'} for i, g in enumerate(m['flags']['gastos_atipicos'])]),
        ('consentimento', 'contexto.montar', {'modo': 'insight', 'gatilho': 'fechamento', 'ofertas_liberadas': ids}, 'contexto JSON da Gi montado em código (números já formatados como texto)', []),
        ('analise', 'ofertas.montar', {'caminho': o['caminho'], 'liberacao': lib}, o['motivo'],
         [{'valor': op['custo_total'], 'origem': f'ofertas.montar:opcoes[{i}].custo_total'} for i, op in enumerate(o['opcoes'])]
         + [{'valor': o['continuar_no_rotativo']['custo_1_mes'], 'origem': 'ofertas.montar:continuar_no_rotativo.custo_1_mes'},
            {'valor': o['teto_cartao_mes'], 'origem': 'ofertas.montar:teto_cartao_mes'}]),
        ('analise', 'policy.travas', {'regras': ['parcela<=folga', 'custo<rotativo no mesmo horizonte', 'liberado', '1x/12m', 'conta voltou ao positivo', 'sem reincidência']},
         '; '.join([f'{op["produto"]}: ok' for op in o['opcoes']] + [f'{op["produto"]}: {", ".join(op["bloqueios"])}' for op in o['descartadas']]) or 'sem opções', []),
        ('analise', 'checagens.checar', {'checagens': ['json', 'numeros', 'oferta', 'consentimento', 'tamanho', 'termos', 'limite_contato']},
         'checagens em código antes do validador: todas ok (respostas gravadas)', []),
        ('analise', 'validador', {'gravado': True}, 'resposta gravada: aprovada na geração dos mocks; com a API, o validador (segundo LlmAgent) aprova ou reprova cada turno', []),
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
              'duracao_ms': None, 'ts': t0.replace(minute=i).isoformat(), 'llm': ferr in ('validador',)}
             for i, (etapa, ferr, args, resumo, ns) in enumerate(itens, 1)]

    return {
        'persona': chave, 'simulado': True, 'gravado': True, 'gerado_por': 'analise/gera_mock_demo.py (usa agent/cabe_core)',
        'gerado_em': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'nota': ('Plano B da demo. Números e formas dos cards vêm do cabe_core sobre data/extrato_sintetico.csv.gz e config/taxas.yaml; cada número tem origem. '
                 'Mensagens no estilo dos exemplos do prompt da Gi, gravadas: sem chamada ao modelo nem ao validador.'),
        'cliente_id': cid, 'anomes': anomes, 'contar_pix': contar_pix, 'liberacao_simulada': lib, 'ofertas_liberadas_ids': ids,
        'saude': {'ok': True, 'dados': 'mock', 'modelo': None, 'modo': 'mock', 'modo_conversa': 'gravado', 'rotulo_modelo': 'nenhum (mock)',
                  'validador': {'ativo': False, 'modelo': None, 'frase': FRASE_VALIDADOR}},
        'sessao': sessao, 'consentimento': consent, 'consentimento_negado': consent_negado,
        'mensagem': mensagem, 'avancar_mes': ciclos, 'trace': trace, 'painel': juri,
    }


def main():
    taxas = config.carregar_taxas()
    fonte = dados.FonteCsv()
    SAIDA.mkdir(parents=True, exist_ok=True)
    for chave, p in PERSONAS.items():
        d = gerar(chave, p, fonte, taxas)
        m = d['mensagem']['ver_opcoes']['cards'][0]['dados']
        if m['flags'].get('pix_regular'):
            # variante depois de "É renda": contas refeitas com o PIX (a demo troca mensagem/avancar_mes/painel/trace)
            v = gerar(chave, p, fonte, taxas, contar_pix=True)
            r = json.loads(json.dumps(v['mensagem']['ver_opcoes']))
            r['turno'] = {**r['turno'], 'acao': 'confirmar_entrada_regular', 'ordem': r['turno']['ordem'] + 1}   # turno próprio no painel
            v['mensagem']['confirmar_entrada_regular'] = r
            d['com_pix'] = {'mensagem': v['mensagem'], 'avancar_mes': v['avancar_mes'], 'painel': v['painel'], 'trace': v['trace'],
                            'consentimento': v['consentimento'], 'ofertas_liberadas_ids': v['ofertas_liberadas_ids']}
            d['mensagem']['confirmar_entrada_regular'] = r
        (SAIDA / f'{chave}.json').write_text(json.dumps(d, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
        o = d['mensagem']['ver_opcoes']['cards'][1]['dados'] if len(d['mensagem']['ver_opcoes']['cards']) > 1 and d['mensagem']['ver_opcoes']['cards'][1]['tipo'] == 'comparador' else None
        print(f'\n== {chave} ({p["cliente_id"][:8]}, {p["anomes"]}) grupo {m["grupo"]["grupo"]} | {m["frase"]}')
        print(f'  fatura {brl(m["fatura"]["valor"])} mínimo {brl(m["fatura"]["minimo"])} venc dia {m["fatura"]["vencimento_dia"]} | renda {brl(m["renda_recorrente"])} dia {m["dia_recebimento"]} '
              f'| fixos {brl(m["fixos"])} essenciais {brl(m["essenciais"])} folga {brl(m["folga"])} falta {brl(m["falta"])} dias {m["dias_ate_recebimento"]}')
        print(f'  insight: {d["consentimento"]["insight"]["texto"]} ({len(d["consentimento"]["insight"]["texto"])} caracteres)')
        for msg in d['mensagem']['ver_opcoes']['mensagens']:
            print(f'  ia.i: {msg["texto"]}')
        print(f'  acao {d["mensagem"]["ver_opcoes"]["acao"]} oferta {d["mensagem"]["ver_opcoes"]["oferta_id"]} citados {d["mensagem"]["ver_opcoes"]["numeros_citados"]}')
        if o:
            c = o['continuar_no_rotativo']
            print(f'  caminho {o["caminho"]} | paga agora {brl(o["pagar_agora"])} financia {brl(o["valor_financiado"])} | rotativo 1 mês {brl(c["custo_1_mes"])} teto {brl(c["custo_ate_teto"])} '
                  f'| se mínimo: não pago {brl(c["se_pagar_minimo"]["nao_pago"])} juros {brl(c["se_pagar_minimo"]["custo_1_mes"])} | teto cartão {brl(o["teto_cartao_mes"])}')
            for i, op in enumerate(o['opcoes']):
                det = f'{op["dias"]} dias' if op.get('dias') else f'{op["n_parcelas"]}x {brl(op["parcela"])}'
                print(f'   {"*" if i == o["recomendada"] else " "} {d["ofertas_liberadas_ids"][i]:<7} {op["produto"]:<20} {det:<20} custo {brl(op["custo_total"]):>12} termina {mes(op["termina_em"])} taxa {op["rotulo_taxa"]}')
        for c in d['avancar_mes']:
            dd = c['cards'][0]['dados']
            print(f'  {c["mensagem"]} | evitados {brl(dd["juros_evitados_acumulados"])} próxima parcela {brl(dd["proxima_parcela"])} teto {brl(dd["teto_cartao"])}')
        if d.get('com_pix'):
            print(f'  com_pix: {d["com_pix"]["mensagem"]["ver_opcoes"]["mensagens"][0]["texto"]}')
        print(f'  números validados no fim: {len(d["painel"]["numeros_com_origem"])}')
    (SAIDA / 'saude.json').write_text(json.dumps({'ok': True, 'dados': 'mock', 'modelo': None, 'modo': 'mock', 'modo_conversa': 'gravado', 'rotulo_modelo': 'nenhum (mock)',
                                                  'validador': {'ativo': False, 'modelo': None, 'frase': FRASE_VALIDADOR},
                                                  'nota': 'respostas gravadas por analise/gera_mock_demo.py'}, ensure_ascii=False, indent=1), encoding='utf-8')
    print('\nmocks gravados em', SAIDA)


if __name__ == '__main__':
    main()
