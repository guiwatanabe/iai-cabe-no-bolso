"""Checagens em código antes do validador (cabe_no_bolso.checagens): puras, sem modelo."""
import json

from cabe_no_bolso import checagens

CTX = {
    "modo": "conversa", "gatilho": "fechamento", "consentimento": True,
    "cliente": {"primeiro_nome": "Ana", "publico_vulneravel": False},
    "fatura": {"valor": "R$ 1.700", "vencimento": "dia 20", "pagamento_minimo": "R$ 255", "encargos_se_pagar_minimo": "R$ 96"},
    "capacidade": {"saldo_previsto_no_vencimento": "R$ 1.280", "proximo_recebimento": {"valor": "R$ 4.100", "data": "dia 5"},
                   "folga_mensal": "R$ 350", "falta_prevista": "R$ 420", "tipo_de_falta": "pontual"},
    "faturas_abaixo_do_total_12m": 2,
    "ofertas_liberadas": [{"id": "cob_01", "tipo": "cobertura_cheque_especial", "prazo": "15 dias", "custo_total": "R$ 38", "cet": "1,3% ao mês"}],
}
INDICE = {"R$ 1.700": {"valor": 170000, "origem": "x"}, "dia 20": {"valor": 20, "origem": "x"}, "R$ 255": {"valor": 25500, "origem": "x"},
          "R$ 96": {"valor": 9600, "origem": "x"}, "R$ 1.280": {"valor": 128000, "origem": "x"}, "R$ 4.100": {"valor": 410000, "origem": "x"},
          "dia 5": {"valor": 5, "origem": "x"}, "R$ 350": {"valor": 35000, "origem": "x"}, "R$ 420": {"valor": 42000, "origem": "x"},
          "15 dias": {"valor": 15, "origem": "x"}, "R$ 38": {"valor": 3800, "origem": "x"}, "1,3%": {"valor": 130, "origem": "x"},
          "90 dias": {"valor": 90, "origem": "config"}}
OK = {"mensagens": ["Oi, Ana. Sua fatura fechou em R$ 1.700 e vence dia 20. Até lá, a previsão é ter R$ 1.280 na conta.",
                    "Uma opção é pagar tudo usando o cheque especial por 15 dias. O custo total fica em R$ 38.", "Quer ver os detalhes?"],
      "acao": "mostrar_oferta", "oferta_id": "cob_01", "numeros_citados": ["R$ 1.700", "dia 20", "R$ 1.280", "15 dias", "R$ 38"]}


def _regras(r):
    return {f["regra"] for f in r["falhas"]}


def test_extrai_numeros_em_chaves_canonicas():
    ns = checagens.extrair("R$ 1.700 e R$ 1700,00 e R$ 38 em 15 dias, dia 5, 12 parcelas, 3,5% ao mês, cob_01, set/2025, 1 de 12, 8,2 vezes")
    chaves = [n["chave"] for n in ns]
    assert ("reais", 170000) in chaves and chaves.count(("reais", 170000)) == 2 and ("reais", 3800) in chaves
    assert ("dias", 15) in chaves and ("dia", 5) in chaves and ("int", 12) in chaves and ("pct", 350) in chaves and ("dec", 820) in chaves
    assert ("int", 1) in chaves and not any(t["texto"] in ("cob_01", "set/2025", "01") for t in ns)


def test_saida_correta_passa():
    r = checagens.checar(OK, CTX, INDICE)
    assert r == {"ok": True, "falhas": [], "acao": None}
    assert checagens.checar(json.dumps(OK, ensure_ascii=False), CTX, INDICE)["ok"]
    assert checagens.checar("```json\n" + json.dumps(OK, ensure_ascii=False) + "\n```", CTX, INDICE)["ok"]


def test_numero_fora_do_contexto_regenera_e_na_segunda_vira_mensagem_segura():
    s = {**OK, "mensagens": ["Sua fatura fechou em R$ 1.750 e vence dia 20."], "numeros_citados": ["R$ 1.750", "dia 20"]}
    r = checagens.checar(s, CTX, INDICE)
    assert not r["ok"] and r["acao"] == "regenerar" and "numeros" in _regras(r)
    assert any(f["trecho"] == "R$ 1.750" and "não existe no contexto" in f["motivo"] for f in r["falhas"])
    assert checagens.checar(s, CTX, INDICE, tentativa=2)["acao"] == "mensagem_segura"


def test_numero_no_texto_fora_de_numeros_citados():
    s = {**OK, "numeros_citados": ["R$ 1.700", "dia 20"]}
    r = checagens.checar(s, CTX, INDICE)
    assert r["acao"] == "regenerar" and any("fora de numeros_citados" in f["motivo"] and f["trecho"] == "R$ 1.280" for f in r["falhas"])
    # número arredondado não vale: R$ 1.280 no contexto, "R$ 1.300" no texto
    s2 = {**OK, "mensagens": ["A previsão é ter R$ 1.300."], "numeros_citados": ["R$ 1.300"]}
    assert "numeros" in _regras(checagens.checar(s2, CTX, INDICE))


def test_oferta_inexistente_ou_lista_vazia_vira_mensagem_segura():
    r = checagens.checar({**OK, "oferta_id": "cob_99"}, CTX, INDICE)
    assert r["acao"] == "mensagem_segura" and "oferta" in _regras(r)
    ctx_vazio = {**CTX, "ofertas_liberadas": []}
    r2 = checagens.checar({**OK, "acao": "nenhuma"}, ctx_vazio, INDICE)
    assert r2["acao"] == "mensagem_segura"                      # oferta_id precisa ser nulo com a lista vazia
    r3 = checagens.checar({**OK, "acao": "mostrar_oferta", "oferta_id": None}, CTX, INDICE)
    assert "oferta" in _regras(r3)


def test_sem_consentimento_nao_pode_ofertar():
    ctx = {**CTX, "consentimento": False}
    r = checagens.checar(OK, ctx, INDICE)
    assert r["acao"] == "mensagem_segura" and "consentimento" in _regras(r)
    r2 = checagens.checar({**OK, "acao": "abrir_resumo_contrato"}, ctx, INDICE, consentimento=False)
    assert "consentimento" in _regras(r2)
    ok = {"mensagens": ["Sua fatura fechou em R$ 1.700 e vence dia 20. Estas são as formas de pagar."], "acao": "mostrar_formas_de_pagar",
          "oferta_id": None, "numeros_citados": ["R$ 1.700", "dia 20"]}
    assert checagens.checar(ok, ctx, INDICE)["ok"]


def test_insight_maior_que_160_e_formato_do_modo():
    ctx = {**CTX, "modo": "insight"}
    longo = {"texto": "Ana, a fatura fechou em R$ 1.700. " + "Pela previsão, a fatura cabe no seu saldo até o dia 20. " * 3,
             "botao_primario": {"rotulo": "Ver", "acao": "abrir_chat"}, "botao_secundario": None, "numeros_citados": ["R$ 1.700", "dia 20"]}
    r = checagens.checar(longo, ctx, INDICE, modo="insight")
    assert r["acao"] == "regenerar" and "tamanho" in _regras(r)
    curto = {**longo, "texto": "Ana, a fatura fechou em R$ 1.700 e vence dia 20. Temos um jeito de pagar tudo."}
    assert checagens.checar(curto, ctx, INDICE, modo="insight")["ok"]
    assert "json" in _regras(checagens.checar({**curto, "botao_primario": {"rotulo": "x", "acao": "mostrar_oferta"}}, ctx, INDICE, modo="insight"))
    quatro = {**OK, "mensagens": OK["mensagens"] + ["Mais uma."]}
    assert "tamanho" in _regras(checagens.checar(quatro, CTX, INDICE))


def test_termo_proibido_regenera():
    for termo in ("Seu score não permite", "Você está no grupo Escorregão", "isso é uma pedalada", "da mais barata para a mais cara",
                  "sujeito a análise", "um seguro prestamista", "ganhe cashback", "um cartão novo", "um investimento melhor"):
        s = {**OK, "mensagens": [OK["mensagens"][0], termo], "numeros_citados": ["R$ 1.700", "dia 20", "R$ 1.280"]}
        r = checagens.checar(s, CTX, INDICE)
        assert "termos" in _regras(r) and r["acao"] == "regenerar", termo
    assert checagens.termos_proibidos_em("Ponto final, dois pontos.") == []     # 'pontos' fora do sentido de produto não conta
    assert checagens.termos_proibidos_em("programa de pontos do cartão") == ["pontos"]


def test_json_invalido_regenera():
    r = checagens.checar("não é json", CTX, INDICE)
    assert r["acao"] == "regenerar" and _regras(r) == {"json"}
    assert checagens.checar(None, CTX, INDICE)["acao"] == "regenerar"
    r2 = checagens.checar({"mensagens": "texto solto", "acao": "voar"}, CTX, INDICE)
    assert "json" in _regras(r2)


def test_limite_de_contato_nao_envia():
    ctx = {**CTX, "modo": "insight", "gatilho": "fechamento"}
    s = {"texto": "Ana, a fatura fechou em R$ 1.700 e vence dia 20.", "botao_primario": {"rotulo": "Ver", "acao": "abrir_chat"},
         "botao_secundario": None, "numeros_citados": ["R$ 1.700", "dia 20"]}
    r = checagens.checar(s, ctx, INDICE, modo="insight", proativa_ja_enviada_hoje=True)
    assert r["acao"] == "nao_enviar" and "limite_contato" in _regras(r)
    assert checagens.checar(s, {**ctx, "gatilho": "pergunta_cliente"}, INDICE, modo="insight", proativa_ja_enviada_hoje=True)["ok"]


def test_mensagem_segura_so_com_numeros_do_contexto():
    ms = checagens.mensagem_segura(CTX, "conversa")
    assert ms["mensagens"][0] == "Sua fatura fechou em R$ 1.700 e vence dia 20. Veja as formas de pagar."
    assert ms["acao"] == "mostrar_formas_de_pagar" and ms["oferta_id"] is None and ms["numeros_citados"] == ["R$ 1.700", "dia 20"]
    assert checagens.checar(ms, CTX, INDICE)["ok"] and checagens.checar(ms, {**CTX, "consentimento": False}, INDICE)["ok"]
    ins = checagens.mensagem_segura(CTX, "insight")
    assert ins["texto"].startswith("Sua fatura fechou em R$ 1.700") and len(ins["texto"]) <= 160 and ins["botao_primario"]["acao"] == "ver_formas_de_pagar"
    assert checagens.checar(ins, {**CTX, "modo": "insight"}, INDICE, modo="insight")["ok"]
    hum = checagens.mensagem_segura(CTX, "conversa", humano=True)
    assert hum["acao"] == "transferir_humano" and hum["numeros_citados"] == [] and not checagens.extrair(hum["mensagens"][0])
    # só os textos do contexto: nada é calculado
    assert set(checagens.chaves(" ".join(ms["mensagens"]))) <= checagens.chaves_do_indice(INDICE)


def test_sinais_de_protecao():
    assert checagens.sinais_protecao("Não tenho dinheiro nem para o aluguel, estou devendo em todo lugar")
    assert checagens.sinais_protecao("quero falar com uma pessoa") and not checagens.sinais_protecao("Ver opções")
