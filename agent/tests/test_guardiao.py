"""Guardião, consentimento, ferramentas e runtime determinístico: tudo sem chamar o modelo."""
from types import SimpleNamespace

import pytest
from google.genai import types

from cabe_no_bolso import callbacks, runtime, tools
from cabe_no_bolso.callbacks import guardiao_texto, numeros_da_frase
from tests.conftest import ANA, BRUNO

NUMS = [
    {"valor": 361995, "origem": "capacidade.motor:fatura.valor"},
    {"valor": 11051, "origem": "ofertas.montar:opcoes[0].parcela"},
    {"valor": 10, "origem": "ofertas.montar:opcoes[0].n_parcelas"},
    {"valor": 20, "origem": "capacidade.motor:fatura.vencimento_dia"},
    {"valor": 0.035, "origem": "ofertas.montar:opcoes[0].taxa_mes"},
    {"valor": 262920, "origem": "capacidade.motor:falta"},
    {"valor": 45, "origem": "capacidade.motor:flags.composicao_fatura.categorias[0].pct"},
]


# ------------------------------------------------------------------ guardião (texto puro)
def test_guardiao_mantem_numero_com_origem():
    t = "Sua fatura fechou em R$ 3.619,95 e vence dia 20. São 10 parcelas de R$ 110,51 a 3,5% ao mês."
    novo, rel = guardiao_texto(t, NUMS)
    assert novo == t and not rel["removidos"] and not rel["alterado"]


def test_guardiao_remove_numero_sem_origem_e_pede_confirmacao():
    t = "Sua fatura fechou em R$ 3.619,95. O custo total fica em R$ 999,99. Quer ver as opções?"
    novo, rel = guardiao_texto(t, NUMS)
    assert "R$ 999,99" not in novo and "R$ 3.619,95" in novo and "Quer ver as opções?" in novo
    assert callbacks.PEDIDO_DE_CONFIRMACAO in novo
    assert rel["removidos"][0]["numeros"] == ["R$ 999,99"] and rel["removidos"][0]["motivo"] == "número sem origem"


def test_guardiao_aceita_formatos_e_arredondamento():
    assert not guardiao_texto("Faltam R$ 2.629,20 no dia 20.", NUMS)[1]["removidos"]
    assert not guardiao_texto("Faltam R$ 2629,20 no dia 20.", NUMS)[1]["removidos"]
    assert not guardiao_texto("Faltam R$ 2.629 no dia 20.", NUMS)[1]["removidos"]          # frase arredondada (docs/06)
    assert not guardiao_texto("Faltam R$ 2.629,21 no dia 20.", NUMS)[1]["removidos"]       # tolerância de 1 centavo
    assert guardiao_texto("Faltam R$ 2.630,20 no dia 20.", NUMS)[1]["removidos"]
    assert not guardiao_texto("Sua folga ficou em -R$ 773,57.", NUMS + [{"valor": -77357, "origem": "capacidade.motor:folga"}])[1]["removidos"]
    assert guardiao_texto("São 14 parcelas.", NUMS)[1]["removidos"]                        # 14 não está no conjunto
    assert not guardiao_texto("Olho seus últimos 90 dias e 12 meses, por 3 faturas.", NUMS)[1]["removidos"]   # constantes de config
    assert not guardiao_texto("Lazer foi 45% da fatura.", NUMS)[1]["removidos"]            # pct inteiro do núcleo
    assert not guardiao_texto("Os juros do cartão são 14% ao mês e param em 100% do valor.", NUMS)[1]["removidos"]  # config


def test_guardiao_reescreve_sujeito_a():
    novo, rel = guardiao_texto("A opção está sujeita a análise de crédito. As parcelas estão sujeitas a aprovação.", NUMS)
    assert "sujeit" not in novo.lower()
    assert "depende de aprovação" in novo and "dependem de aprovação" in novo
    assert rel["substituicoes"] == 2 and "sujeito a" in rel["termos_bloqueados"]


def test_guardiao_bloqueia_seguro_e_outros_produtos():
    novo, rel = guardiao_texto("Posso incluir um seguro prestamista no plano. Sua fatura fechou em R$ 3.619,95.", NUMS)
    assert "seguro" not in novo.lower() and "prestamista" not in novo.lower()
    assert callbacks.FORA_DO_PLANO in novo and "R$ 3.619,95" in novo
    assert "seguro" in rel["termos_bloqueados"]
    for termo in ("cashback", "pontos", "cartão novo", "investimento"):
        n, r = guardiao_texto(f"Que tal um {termo} para você?", NUMS)
        assert termo not in n.lower() and r["termos_bloqueados"], termo


def test_guardiao_remove_julgamento():
    novo, rel = guardiao_texto("Você gasta demais com delivery. Sua fatura fechou em R$ 3.619,95.", NUMS)
    assert "gasta demais" not in novo and "R$ 3.619,95" in novo
    assert rel["removidos"][0]["motivo"].startswith("julgamento")


def test_guardiao_resposta_vazia_vira_convite():
    novo, _ = guardiao_texto("Você deveria ter controle. Descontrole total.", NUMS)
    assert novo == callbacks.RESPOSTA_VAZIA


def test_numeros_da_frase():
    ns = numeros_da_frase("R$ 1.234,56 e R$ 1234 em 10 parcelas até dia 7, 8,2 vezes o normal, 3,5%")
    tipos = [(n["tipo"], n["valor"]) for n in ns]
    assert ("reais", 123456) in tipos and ("reais", 123400) in tipos and ("int", 10) in tipos and ("int", 7) in tipos
    assert ("float", 8.2) in tipos and ("pct", 0.035) in tipos


# ------------------------------------------------------------------ callbacks do ADK com contexto falso
def _ctx(**state):
    return SimpleNamespace(state=dict(state), session=SimpleNamespace(id="s1"), function_call_id="fc1", invocation_id="inv1")


def test_before_tool_bloqueia_sem_consentimento():
    for nome in tools.FERRAMENTAS_DE_DADOS:
        ctx = _ctx(consentimento=False, cliente_id=BRUNO, anomes=202509)
        r = callbacks.before_tool(SimpleNamespace(name=nome), {"cliente_id": BRUNO}, ctx)
        assert r and r["bloqueado"] and "permissão" in r["mensagem_cliente"]
        assert ctx.state["trace"][-1]["bloqueada"] and ctx.state["trace"][-1]["ferramenta"] == nome
    ctx = _ctx(consentimento=False)
    assert callbacks.before_tool(SimpleNamespace(name="registrar_consentimento"), {"concedido": True}, ctx) is None
    ctx = _ctx(consentimento=True, cliente_id=BRUNO, anomes=202509)
    assert callbacks.before_tool(SimpleNamespace(name="analisar_fatura"), {}, ctx) is None


def test_after_tool_grava_trace_com_numeros_novos():
    ctx = _ctx(consentimento=True, cliente_id=BRUNO, anomes=202509, numeros_validados=[])
    tool = SimpleNamespace(name="analisar_fatura")
    assert callbacks.before_tool(tool, {}, ctx) is None
    resp = tools.analisar_fatura(tool_context=ctx)
    callbacks.after_tool(tool, {"cliente_id": BRUNO}, ctx, resp)
    t = ctx.state["trace"][-1]
    assert t["ferramenta"] == "analisar_fatura" and t["numeros"] and t["duracao_ms"] is not None and t["llm"] is False
    assert t["argumentos"]["cliente_id"].endswith("…") and t["resumo"].startswith("faltam R$ 919,08")


def test_after_model_aplica_guardiao_e_finops():
    ctx = _ctx(numeros_validados=NUMS, finops=None)
    texto = "Sua fatura fechou em R$ 3.619,95. O custo total fica em R$ 999,99. Está sujeito a análise."
    from google.adk.models import LlmResponse
    llm = LlmResponse(content=types.Content(role="model", parts=[types.Part(text=texto)]),
                      usage_metadata=types.GenerateContentResponseUsageMetadata(prompt_token_count=120, candidates_token_count=30))
    out = callbacks.after_model(ctx, llm)
    assert out is not None
    novo = out.content.parts[0].text
    assert "R$ 999,99" not in novo and "R$ 3.619,95" in novo and "depende de aprovação" in novo.lower()
    assert ctx.state["finops"]["chamadas_llm"] == 1 and ctx.state["finops"]["tokens_entrada"] == 120
    assert ctx.state["guardiao"]["removidos"] and ctx.state["guardiao"]["substituicoes"] == 1
    assert ctx.state["trace"][-1]["llm"] is True
    # resposta só com chamada de ferramenta: passa intacta
    llm2 = LlmResponse(content=types.Content(role="model", parts=[types.Part(function_call=types.FunctionCall(name="analisar_fatura", args={}))]))
    assert callbacks.after_model(ctx, llm2) is None and ctx.state["finops"]["chamadas_llm"] == 2


# ------------------------------------------------------------------ ferramentas (wrappers) sem LLM
def test_ferramentas_bruno_fluxo_completo():
    ctx = _ctx(consentimento=True, cliente_id=BRUNO, anomes=202509)
    a = tools.analisar_fatura(tool_context=ctx)
    assert a["fatura"]["valor"] == "R$ 3.619,95" and a["falta"] == "R$ 919,08" and a["tipo_falta"] == "estrutural"
    assert {n["valor"] for n in ctx.state["numeros_validados"]} >= {361995, 91908, 270087}
    o = tools.listar_ofertas(tool_context=ctx)
    assert o["caminho"] == "parcelamento" and o["recomendada"] == 0 and o["opcoes"][0]["parcela"] == "R$ 110,51"
    assert all(op["condicao"] == "depende de aprovação" for op in o["opcoes"])
    d = tools.detalhar_fatura(tool_context=ctx)
    assert d["maiores_categorias"][0]["categoria"] == "Lojas e sites"
    s = tools.simular_continuar_no_rotativo(3076.96, 1, tool_context=ctx)          # não pago se pagar o mínimo
    assert s["custo"] == "R$ 430,77" and s["sobre"] == "nao_pago_se_pagar_o_minimo"
    assert "erro" in tools.simular_continuar_no_rotativo(1234.56, 1, tool_context=ctx)   # valor inventado é recusado
    c = tools.confirmar_plano(0, tool_context=ctx)
    assert c["contratado"] is False and c["parcela"] == "R$ 110,51" and c["n_parcelas"] == 10 and ctx.state["plano"]["produto"] == "consignado_clt"
    assert ctx.state["historico_contratacoes"][-1]["caminho"] == "parcelamento"
    e = tools.encaminhar_humano("pedido do cliente", tool_context=ctx)
    assert e["encaminhado"] and e["produto"] is None and ctx.state["encaminhado"]


def test_ferramentas_ana_pix_pergunta_antes():
    ctx = _ctx(consentimento=True, cliente_id=ANA, anomes=202508)
    a = tools.analisar_fatura(tool_context=ctx)
    assert a["tipo_falta"] == "pontual" and a["pergunta_pendente"] and "PIX" in a["pergunta_pendente"]
    o = tools.listar_ofertas(tool_context=ctx)
    assert o["caminho"] == "cobertura_curta" and o["opcoes"][0]["dias_de_uso"] == 7 and o["opcoes"][0]["custo_total"] == "R$ 7,98"
    a2 = tools.analisar_fatura(contar_pix=True, tool_context=ctx)
    assert a2["falta"] == "R$ 634,58" and ctx.state["ofertas"] is None        # ofertas invalidadas


def test_consentimento_revogado_apaga_analise():
    ctx = _ctx(consentimento=True, cliente_id=BRUNO, anomes=202509)
    tools.analisar_fatura(tool_context=ctx)
    r = tools.registrar_consentimento(False, tool_context=ctx)
    assert r["consentimento"] is False and ctx.state["motor"] is None and ctx.state["consentimento"] is False


def test_simulacao_da_sessao_sobrescreve_liberacao_e_taxas():
    ctx = _ctx(consentimento=True, cliente_id=BRUNO, anomes=202509,
               simulacao={"liberacao": {"parcelamento_fatura": True}, "taxas": {"parcelamento_fatura": {"taxa_mes": 0.5}}})
    tools.analisar_fatura(tool_context=ctx)
    o = tools.listar_ofertas(tool_context=ctx)
    assert o["opcoes"] == [] and o["encaminhar_humano"]          # só parcelamento_fatura liberado, e mais caro que o rotativo
    ctx2 = _ctx(consentimento=True, cliente_id=BRUNO, anomes=202509,
                historico_contratacoes=[{"caminho": "parcelamento", "anomes": 202503}])
    tools.analisar_fatura(tool_context=ctx2)
    o2 = tools.listar_ofertas(tool_context=ctx2)
    assert o2["opcoes"] == [] and "12 meses" in o2["motivo"]     # segundo parcelamento em 12 meses bloqueado


# ------------------------------------------------------------------ runtime determinístico (sem LLM)
def test_runtime_sessao_consentimento_e_acompanhamento():
    s = runtime.criar_sessao(BRUNO, 202509)
    assert s["cliente"]["apelido"] == "Bruno" and s["fatura"]["valor"] == 361995 and s["consentimento"] is False
    assert all("origem" in n for n in s["numeros_validados"])
    c = runtime.consentir(s["sessao_id"], True)
    assert c["consentimento"] and c["insight"]["estado"] == "falta_que_se_repete" and c["registro"]["versao_texto"] == tools.VERSAO_CONSENTIMENTO
    tr = runtime.trace(s["sessao_id"])
    assert [t["ferramenta"] for t in tr] == ["fatura.historico", "registrar_consentimento", "capacidade.motor", "ofertas.montar"]
    pm = runtime.conversar(s["sessao_id"], BRUNO, 202509, "pagar_minimo")
    assert pm["cards"][0]["tipo"] == "insight" and pm["cards"][0]["dados"]["estado"] == "antes_de_confirmar" and pm["mensagens"] == []
    assert runtime.avancar_mes(s["sessao_id"])["erro"] == "sem_plano"
    # confirma o plano pelo caminho das ferramentas (sem modelo) e avança 3 meses
    est = runtime.estado_bruto(s["sessao_id"])
    ctx = _ctx(**est)
    tools.confirmar_plano(0, tool_context=ctx)
    runtime._executar(runtime._aplicar_delta(runtime._runner_da_sessao(s["sessao_id"]),
                                             runtime._executar(runtime._obter_sessao(runtime._runner_da_sessao(s["sessao_id"]), s["sessao_id"])),
                                             {"plano": ctx.state["plano"], "historico_contratacoes": ctx.state["historico_contratacoes"]}))
    ciclos = [runtime.avancar_mes(s["sessao_id"]) for _ in range(3)]
    assert [c["ciclos_ok"] for c in ciclos] == [1, 2, 3] and ciclos[-1]["encerrado"] and ciclos[-1]["cards"][0]["tipo"] == "acompanhamento"
    assert runtime.avancar_mes(s["sessao_id"])["erro"] == "plano_encerrado"
    p = runtime.painel(s["sessao_id"])
    assert p["encerrado"] and p["nao_pago"] == 0 and p["finops"]["chamadas_llm"] == 0 and p["finops"]["custo_estimado"] is None
    assert p["comparativo_real_2025"]["faturas_roladas"] == 5


def test_runtime_sem_consentimento_nao_le_dados():
    s = runtime.criar_sessao(ANA, 202508)
    c = runtime.consentir(s["sessao_id"], False)
    assert c["consentimento"] is False and c["insight"]["estado"] == "sem_adesao"
    est = runtime.estado_bruto(s["sessao_id"])
    assert est["motor"] is None and est["ofertas"] is None
    assert [t["ferramenta"] for t in est["trace"]] == ["fatura.historico", "registrar_consentimento"]
    pm = runtime.conversar(s["sessao_id"], ANA, 202508, "pagar_minimo")
    assert pm["cards"][0]["tipo"] == "aviso" and "permissão" in pm["cards"][0]["dados"]["texto"]


def test_runtime_erros():
    assert runtime.criar_sessao("cliente-inexistente", 202509)["erro"] == "dados_indisponiveis"
    assert runtime.avancar_mes("nao-existe")["erro"] == "sessao_inexistente"
    assert runtime.saude()["ok"] and runtime.saude()["dados"] == "csv"
