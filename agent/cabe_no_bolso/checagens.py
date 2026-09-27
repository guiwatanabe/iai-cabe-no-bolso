"""Checagens em código antes do validador (Notas da Gi, p. 3, tabela "Checagens em código"). Puras e testáveis.

Valem para os dois modos de conversa. Entrada: a saída do modelo (JSON do formato da Gi, ou texto no modo tools já
embrulhado em JSON pelo runtime), o contexto JSON e o índice de números do contexto (texto formatado -> {valor, origem}).
Saída: {ok, falhas: [{regra, trecho, motivo}], acao: 'regenerar' | 'mensagem_segura' | 'nao_enviar' | None}.

| Checagem       | Regra                                                                     | Se falhar                              |
| json           | a saída é JSON válido no formato do modo                                  | regenerar                              |
| numeros        | cada item de numeros_citados existe no contexto; todo número do texto     | regenerar; na 2ª falha, mensagem segura |
|                | está em numeros_citados                                                   |                                        |
| oferta         | oferta_id existe em ofertas_liberadas; lista vazia => oferta_id nulo      | mensagem segura                        |
| consentimento  | sem consentimento a ação não é mostrar_oferta nem abrir_resumo_contrato   | mensagem segura                        |
| tamanho        | insight: só texto, até 160 caracteres; conversa: até 3 mensagens          | regenerar                              |
| termos         | lista fixa (prompt da Gi + 'sujeito a' + produtos fora do plano)          | regenerar                              |
| limite_contato | no máximo uma mensagem proativa por dia para o mesmo cliente              | nao_enviar                             |

Nenhum número nasce aqui: a mensagem segura só usa os textos que já estão no contexto (fatura.valor e fatura.vencimento).
"""
from __future__ import annotations

import json
import re
import unicodedata

INSIGHT_MAX_CARACTERES = 160
CONVERSA_MAX_MENSAGENS = 3
ACOES_CONVERSA = ("nenhuma", "mostrar_formas_de_pagar", "mostrar_oferta", "abrir_resumo_contrato", "registrar_permissao_ampliacao",
                  "mudar_vencimento", "revogar_consentimento", "transferir_humano", "devolver_ao_iai")
ACOES_INSIGHT = ("abrir_chat", "ver_formas_de_pagar", "nenhuma")
ACOES_COM_OFERTA = ("mostrar_oferta", "abrir_resumo_contrato", "registrar_permissao_ampliacao")
ACOES_SEM_CONSENTIMENTO_PROIBIDAS = ("mostrar_oferta", "abrir_resumo_contrato")

# Lista fixa: termos do prompt da Gi (<o_que_voce_nunca_faz>, R4) + 'sujeito a' (docs/06) + produtos fora do plano (docs/05, 6).
TERMOS_PROIBIDOS = (
    "score", "análise de risco", "analise de risco", "negativa de crédito", "negativa de credito", "limite recusado",
    "grupo do cliente", "escorregão", "escorregao", "rolando a fatura", "pedalada", "da mais barata para a mais cara",
    "sujeito a", "sujeita a", "sujeitos a", "sujeitas a",
    "seguro", "prestamista", "cashback", "pontos", "cartão novo", "cartao novo", "novo cartão", "novo cartao",
    "investimento", "investir", "capitalização", "capitalizacao",
    "negativação", "negativacao", "bloqueio do cartão", "bloqueio do cartao",
)
# R8 (proteção): sinais no texto do cliente que exigem transferir_humano sem oferta.
SINAIS_PROTECAO = ("aluguel", "comida", "luz", "muitas dívidas", "muitas dividas", "devendo em todo", "desesper", "não aguento",
                   "nao aguento", "sofr", "não durmo", "nao durmo", "fraude", "não reconheço", "nao reconheco", "não fiz essa compra",
                   "falar com uma pessoa", "falar com alguém", "falar com alguem", "atendente", "humano", "reclama", "péssimo", "pessimo",
                   "absurdo", "vergonha")

_MESES = "jan|fev|mar|abr|mai|jun|jul|ago|set|out|nov|dez"
_RE_ID = re.compile(r"\b[a-z]{2,5}_\d{1,3}\b")
_RE_MES_ANO = re.compile(rf"\b(?:{_MESES})/\d{{4}}\b", re.IGNORECASE)
_RE_DATA = re.compile(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b")
_RE_REAIS = re.compile(r"-?R\$\s?(\d{1,3}(?:\.\d{3})+|\d+)(?:,(\d{1,2}))?")
_RE_PCT = re.compile(r"(\d+(?:[,.]\d+)?)\s?%")
_RE_DIA = re.compile(r"\bdia\s+(\d{1,2})\b", re.IGNORECASE)
_RE_DIAS = re.compile(r"\b(\d{1,3})\s+dias?\b", re.IGNORECASE)
_RE_CONTAGEM = re.compile(r"\b(\d{1,3})\s*(?:x\b|×|parcelas?\b|vezes\b|meses\b|mês\b|faturas?\b|mensagens?\b)", re.IGNORECASE)
_RE_DECIMAL = re.compile(r"\b(\d+),(\d+)\b")
_RE_INT = re.compile(r"\b\d+\b")


# ------------------------------------------------------------------ números em texto (chaves canônicas)
def _chave_reais(inteiro: str, cents: str | None) -> tuple:
    reais = int(inteiro.replace(".", ""))
    c = 0 if cents is None else (int(cents) if len(cents) == 2 else int(cents) * 10)
    return ("reais", reais * 100 + c)


def extrair(texto: str) -> list[dict]:
    """Números que o cliente veria num texto, cada um com a chave canônica: [{texto, chave}].

    Chaves: ('reais', centavos) · ('pct', x10000) · ('dia', n) · ('dias', n) · ('int', n) · ('dec', x100).
    Ids de oferta (cob_01), mês/ano (set/2025) e datas (20/09/2025) não são números do cliente e ficam de fora.
    """
    out: list[dict] = []
    t = _RE_ID.sub(" ", texto or "")
    t = _RE_MES_ANO.sub(" ", t)
    t = _RE_DATA.sub(" ", t)

    def _colhe(rx: re.Pattern, f):
        nonlocal t
        achados = []
        for m in rx.finditer(t):
            achados.append({"texto": m.group(0).strip(), "chave": f(m)})
        t = rx.sub(" ", t)
        out.extend(achados)

    _colhe(_RE_REAIS, lambda m: _chave_reais(m.group(1), m.group(2)))
    _colhe(_RE_PCT, lambda m: ("pct", int(round(float(m.group(1).replace(",", ".")) * 100))))
    _colhe(_RE_DECIMAL, lambda m: ("dec", int(round(float(f"{m.group(1)}.{m.group(2)}") * 100))))
    _colhe(_RE_DIA, lambda m: ("dia", int(m.group(1))))
    _colhe(_RE_DIAS, lambda m: ("dias", int(m.group(1))))
    _colhe(_RE_CONTAGEM, lambda m: ("int", int(m.group(1))))
    _colhe(_RE_INT, lambda m: ("int", int(m.group(0))))
    return out


def chaves(texto: str) -> set[tuple]:
    return {n["chave"] for n in extrair(texto)}


def chaves_do_indice(indice: dict) -> set[tuple]:
    """Chaves canônicas de todos os textos formatados do índice (texto -> {valor, origem})."""
    s: set[tuple] = set()
    for texto in indice or {}:
        s |= chaves(str(texto))
    return s


# ------------------------------------------------------------------ saída do modelo
def analisar_saida(bruto: str | dict | None) -> dict | None:
    """JSON da saída do modelo (tolera cerca ```json e texto em volta). None se não for JSON de objeto."""
    if isinstance(bruto, dict):
        return bruto
    if not bruto or not str(bruto).strip():
        return None
    s = str(bruto).strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
    try:
        v = json.loads(s)
        return v if isinstance(v, dict) else None
    except json.JSONDecodeError:
        pass
    i, j = s.find("{"), s.rfind("}")
    if i >= 0 and j > i:
        try:
            v = json.loads(s[i:j + 1])
            return v if isinstance(v, dict) else None
        except json.JSONDecodeError:
            return None
    return None


def textos_da_saida(saida: dict, modo: str) -> list[str]:
    if modo == "insight":
        t = saida.get("texto")
        return [t] if isinstance(t, str) else []
    msgs = saida.get("mensagens")
    return [m for m in msgs if isinstance(m, str)] if isinstance(msgs, list) else []


def _sem_acento(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def termos_proibidos_em(texto: str) -> list[str]:
    baixo = (texto or "").lower()
    plano = _sem_acento(baixo)
    achados = []
    for termo in TERMOS_PROIBIDOS:
        alvo = _sem_acento(termo)
        if re.search(r"(?<![\w])" + re.escape(alvo) + r"(?![\w])", plano):
            achados.append(termo)
    # 'pontos' só conta como produto (programa de pontos), não 'pontos' de ortografia: exige contexto de produto
    if "pontos" in achados and not re.search(r"(programa|resgat|acumul|troc|ganh|junt)\w*\s+(de\s+|seus\s+|os\s+)?pontos|pontos\s+(do|no)\s+cart", plano):
        achados.remove("pontos")
    return achados


def sinais_protecao(texto_cliente: str) -> list[str]:
    plano = _sem_acento((texto_cliente or "").lower())
    return [s for s in SINAIS_PROTECAO if _sem_acento(s) in plano]


# ------------------------------------------------------------------ checagem principal
def checar(saida: dict | str | None, contexto: dict, indice: dict, *, modo: str = "conversa", consentimento: bool | None = None,
           tentativa: int = 1, proativa_ja_enviada_hoje: bool = False) -> dict:
    """Aplica as checagens da tabela. Devolve {ok, falhas: [{regra, trecho, motivo}], acao}.

    tentativa=2 é a segunda saída do modelo no mesmo turno: falha de número vira mensagem segura em vez de regenerar.
    """
    falhas: list[dict] = []
    acoes: set[str] = set()
    if consentimento is None:
        consentimento = bool((contexto or {}).get("consentimento"))
    modo = modo or (contexto or {}).get("modo") or "conversa"

    def falha(regra: str, trecho: str, motivo: str, acao: str) -> None:
        falhas.append({"regra": regra, "trecho": (trecho or "")[:200], "motivo": motivo})
        acoes.add(acao)

    # limite de contato (só mensagens proativas: insight de fechamento/acompanhamento)
    gatilho = (contexto or {}).get("gatilho")
    if proativa_ja_enviada_hoje and modo == "insight" and gatilho in ("fechamento", "acompanhamento"):
        falha("limite_contato", "", "já houve uma mensagem proativa hoje para este cliente", "nao_enviar")

    s = analisar_saida(saida)
    if s is None:
        falha("json", str(saida)[:120] if saida is not None else "", "a saída não é JSON válido", "regenerar")
        return _fechar(falhas, acoes)

    # formato do modo
    if modo == "insight":
        if not isinstance(s.get("texto"), str):
            falha("json", json.dumps(s, ensure_ascii=False)[:120], "insight sem campo 'texto'", "regenerar")
        bp = s.get("botao_primario")
        if bp is not None and (not isinstance(bp, dict) or bp.get("acao") not in ACOES_INSIGHT):
            falha("json", json.dumps(bp, ensure_ascii=False)[:120], "botao_primario.acao fora das ações do modo insight", "regenerar")
        if any(k in s for k in ("mensagens", "acao")):
            falha("json", ", ".join(k for k in ("mensagens", "acao") if k in s), "campos do modo conversa numa saída de insight", "regenerar")
    else:
        if not isinstance(s.get("mensagens"), list) or not all(isinstance(m, str) for m in s.get("mensagens") or []):
            falha("json", json.dumps(s.get("mensagens"), ensure_ascii=False)[:120], "conversa sem lista 'mensagens' de textos", "regenerar")
        if s.get("acao") not in ACOES_CONVERSA:
            falha("json", str(s.get("acao")), "ação fora da lista do formato de saída", "regenerar")
    if not isinstance(s.get("numeros_citados", []), list):
        falha("json", str(s.get("numeros_citados"))[:120], "numeros_citados não é lista", "regenerar")
    citados = [c for c in (s.get("numeros_citados") or []) if isinstance(c, (str, int, float))] if isinstance(s.get("numeros_citados", []), list) else []

    textos = textos_da_saida(s, modo)
    texto = " ".join(textos)

    # tamanho
    if modo == "insight":
        if isinstance(s.get("texto"), str) and len(s["texto"]) > INSIGHT_MAX_CARACTERES:
            falha("tamanho", s["texto"], f"insight com {len(s['texto'])} caracteres; máximo {INSIGHT_MAX_CARACTERES}", "regenerar")
        if isinstance(s.get("texto"), str) and re.match(r"^\s*(#|\*\*|[A-ZÁÉÍÓÚÂÊÔÃÕÇ][^.!?\n]{0,40}\n)", s["texto"]):
            falha("tamanho", s["texto"][:60], "insight com título; só texto", "regenerar")
    elif len(textos) > CONVERSA_MAX_MENSAGENS:
        falha("tamanho", f"{len(textos)} mensagens", f"conversa com {len(textos)} mensagens; máximo {CONVERSA_MAX_MENSAGENS}", "regenerar")

    # números: cada citado existe no contexto; todo número do texto está em numeros_citados
    chaves_indice = chaves_do_indice(indice)
    acao_num = "regenerar" if tentativa < 2 else "mensagem_segura"
    chaves_citadas: set[tuple] = set()
    for c in citados:
        ks = chaves(str(c))
        if not ks:
            falha("numeros", str(c), "item de numeros_citados sem número reconhecível", acao_num)
            continue
        chaves_citadas |= ks
        fora = [k for k in ks if k not in chaves_indice]
        if fora:
            falha("numeros", str(c), "número citado não existe no contexto", acao_num)
    for n in extrair(texto):
        if n["chave"] not in chaves_citadas:
            motivo = "número do texto fora de numeros_citados" if n["chave"] in chaves_indice else "número do texto que não existe no contexto"
            falha("numeros", n["texto"], motivo, acao_num)

    # oferta
    ofertas = (contexto or {}).get("ofertas_liberadas") or []
    ids = {o.get("id") for o in ofertas if isinstance(o, dict)}
    oid = s.get("oferta_id")
    acao = s.get("acao")
    if oid is not None and oid not in ids:
        falha("oferta", str(oid), "oferta_id não existe em ofertas_liberadas", "mensagem_segura")
    if not ofertas and oid is not None:
        falha("oferta", str(oid), "ofertas_liberadas vazia exige oferta_id nulo", "mensagem_segura")
    if modo != "insight" and acao in ACOES_COM_OFERTA and oid is None:
        falha("oferta", str(acao), "ação de oferta sem oferta_id", "mensagem_segura")
    if modo != "insight" and acao in ACOES_COM_OFERTA and not ofertas:
        falha("oferta", str(acao), "ação de oferta com ofertas_liberadas vazia", "mensagem_segura")

    # consentimento
    if not consentimento and modo != "insight" and acao in ACOES_SEM_CONSENTIMENTO_PROIBIDAS:
        falha("consentimento", str(acao), "sem consentimento a ação não pode ser de oferta", "mensagem_segura")
    if not consentimento and modo == "insight" and isinstance(s.get("botao_primario"), dict) and s["botao_primario"].get("acao") == "abrir_chat" and ofertas:
        falha("consentimento", "abrir_chat", "sem consentimento não há oferta a abrir", "mensagem_segura")

    # termos proibidos
    for termo in termos_proibidos_em(texto):
        falha("termos", termo, "termo proibido", "regenerar")

    return _fechar(falhas, acoes)


def _fechar(falhas: list[dict], acoes: set[str]) -> dict:
    acao = None
    for a in ("nao_enviar", "mensagem_segura", "regenerar"):     # a mais grave decide
        if a in acoes:
            acao = a
            break
    return {"ok": not falhas, "falhas": falhas, "acao": acao}


# ------------------------------------------------------------------ mensagem segura (texto fixo; só números do contexto)
TEXTO_SEGURO = "Sua fatura fechou em {valor} e vence {vencimento}. Veja as formas de pagar."
TEXTO_SEGURO_EQUIPE = "Se quiser, alguém da equipe pode olhar com você o melhor caminho para este mês."
TEXTO_SEGURO_HUMANO = "Vou te passar para alguém da equipe, que pode olhar a sua situação com calma e encontrar o melhor caminho."


def mensagem_segura(contexto: dict, modo: str = "conversa", *, humano: bool = False) -> dict:
    """Texto fixo (Notas da Gi, "O que acontece depois da validação", 3). Com R8, a conversa vai direto para uma pessoa."""
    fat = (contexto or {}).get("fatura") or {}
    valor = str(fat.get("valor") or "").strip()
    venc = str(fat.get("vencimento") or "").strip()
    citados = [x for x in (valor, venc) if x]
    if not valor or not venc:
        frase = "Veja as formas de pagar a sua fatura."
        citados = []
    else:
        frase = TEXTO_SEGURO.format(valor=valor, vencimento=venc)
    if modo == "insight":
        return {"texto": frase, "botao_primario": {"rotulo": "Ver formas de pagar", "acao": "ver_formas_de_pagar"},
                "botao_secundario": None, "numeros_citados": citados, "mensagem_segura": True}
    if humano:
        return {"mensagens": [TEXTO_SEGURO_HUMANO], "acao": "transferir_humano", "oferta_id": None, "numeros_citados": [],
                "mensagem_segura": True}
    return {"mensagens": [frase, TEXTO_SEGURO_EQUIPE], "acao": "mostrar_formas_de_pagar", "oferta_id": None,
            "numeros_citados": citados, "mensagem_segura": True}


__all__ = ["checar", "extrair", "chaves", "chaves_do_indice", "analisar_saida", "textos_da_saida", "termos_proibidos_em",
           "sinais_protecao", "mensagem_segura", "TERMOS_PROIBIDOS", "ACOES_CONVERSA", "ACOES_INSIGHT",
           "INSIGHT_MAX_CARACTERES", "CONVERSA_MAX_MENSAGENS"]
