"""Confere a camada gold do Lucas (BigQuery, camada_analitica/) contra o motor cabe_core, para as duas personas da demo.

Lado a lado, por campo: o que a gold diz (janela D-90 da cash90_hackathon) x o que o motor calcula na MESMA janela
(mês de referência = último mês da cash90) x o que o motor calcula no mês da demo (Ana 202508, Bruno 202509).
A tabela alimenta camada_analitica/docs/alinhamento-com-o-motor.md.

Consultas ao BigQuery: no máximo 4 (uma por tabela: gold_contexto_agente + silver_cliente_features,
silver_historico_fatura_12m, cash90_hackathon, silver_cartao), só para os dois clientes, nunca em loop.
O resultado fica em cache (camada_analitica/docs/personas-gold-2026-09-27.json); rodar de novo NÃO consulta
o BigQuery, a menos que se passe --atualizar. Sem credencial, --sem-bigquery imprime só o lado do motor.

Rodar (da raiz; precisa do ambiente do agente para importar cabe_core):
    CLOUDSDK_ACTIVE_CONFIG_NAME=mana-gsoares \
    uv run --project agent --with google-cloud-bigquery python analise/confere_gold_lucas.py [--atualizar] [--sem-bigquery]
Alternativa sem o cliente Python (usa o `bq` do gcloud): acrescentar --via-bq.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "agent"))

from cabe_core import calendario, capacidade, config, dados, fatura, grupo, ofertas  # noqa: E402
from cabe_core.dinheiro import brl, centavos  # noqa: E402
from cabe_no_bolso import policy  # noqa: E402

PROJETO = os.environ.get("GOOGLE_CLOUD_PROJECT", "batalha-time-05-xew3")
DATASET = f"{PROJETO}.hackathon_dados"
CACHE = RAIZ / "camada_analitica" / "docs" / "personas-gold-2026-09-27.json"
PERSONAS = [
    ("Ana", "755627ab-804b-4211-b0ea-f4ebacc58716", 202508),
    ("Bruno", "3e7d20b2-4c4f-450a-bbd2-e60bfda81f0b", 202509),
]
IDS_SQL = ", ".join(f"'{cid}'" for _, cid, _ in PERSONAS)

CONSULTAS = {
    "gold_contexto_agente": f"""
        SELECT g.*, f.data_referencia, f.entradas_90d, f.saidas_90d, f.dia_recebimento_estimado, f.valor_recebimento_tipico,
               f.confianca_recebimento, f.gasto_recorrente_mensal_estimado, f.parcelas_futuras_estimadas,
               f.compras_cartao_mes, f.qtd_minimos_12m, f.ultima_pedalada
        FROM `{DATASET}.gold_contexto_agente` g
        LEFT JOIN `{DATASET}.silver_cliente_features` f USING (id_usuario)
        WHERE g.id_usuario IN ({IDS_SQL})""",
    "silver_historico_fatura_12m": f"""
        SELECT * FROM `{DATASET}.silver_historico_fatura_12m` WHERE id_usuario IN ({IDS_SQL})""",
    "cash90_hackathon": f"""
        SELECT id_usuario, MIN(DATE(anomesdia)) AS dt_min, MAX(DATE(anomesdia)) AS dt_max, MIN(anomes) AS anomes_min,
               MAX(anomes) AS anomes_max, COUNT(*) AS n
        FROM `{DATASET}.cash90_hackathon` WHERE id_usuario IN ({IDS_SQL}) GROUP BY id_usuario""",
    "silver_cartao": f"""
        SELECT * FROM `{DATASET}.silver_cartao` WHERE id_usuario IN ({IDS_SQL}) AND anomes BETWEEN 202505 AND 202512
        ORDER BY id_usuario, anomes""",
}


# ----------------------------------------------------------------------------- BigQuery (4 consultas, com cache)
def _serializavel(v):
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    return v


def consultar_python(sql: str) -> list[dict]:
    from google.cloud import bigquery  # type: ignore

    cli = bigquery.Client(project=PROJETO, location="us-central1")
    return [{k: _serializavel(v) for k, v in dict(r).items()} for r in cli.query(sql).result()]


def consultar_bq(sql: str) -> list[dict]:
    cmd = ["bq", f"--project_id={PROJETO}", "query", "--use_legacy_sql=false", "--format=json", "--max_rows=1000", sql]
    out = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return json.loads(out.stdout or "[]")


def carregar_gold(atualizar: bool, via_bq: bool) -> dict:
    if CACHE.exists() and not atualizar:
        return json.loads(CACHE.read_text(encoding="utf-8"))
    consultar = consultar_bq if via_bq else consultar_python
    resultado = {"consultado_em": datetime.now().isoformat(timespec="seconds"), "projeto": PROJETO, "consultas": len(CONSULTAS),
                 "erros": {}, "tabelas": {}}
    for nome, sql in CONSULTAS.items():
        try:
            resultado["tabelas"][nome] = consultar(sql)
        except Exception as e:  # registra o erro exato e segue para a próxima tabela
            resultado["erros"][nome] = f"{type(e).__name__}: {e}"
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(resultado, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return resultado


# ----------------------------------------------------------------------------- motor cabe_core
def lado_motor(fonte, cid: str, anomes: int, taxas: dict) -> dict:
    m = capacidade.motor(fonte, cid, anomes, taxas)
    o = ofertas.montar(m, m["grupo"], taxas, policy.liberacao_para(cid, taxas))
    hist_ano = fatura.historico(fonte, cid, 202512, 12)
    fechados = m["janela"]["meses_fechados"]
    ext = fonte.extrato(cid, fechados[0], anomes)
    entradas = sum(l["valor"] for l in ext if l["tipo"] == "E")
    saidas = sum(l["valor"] for l in ext if l["tipo"] == "S")
    compras_ant = sum(l["valor"] for l in fonte.extrato(cid, calendario.anomes_soma(anomes, -1), calendario.anomes_soma(anomes, -1)) if l["cartao"])
    return {
        "anomes": anomes,
        "janela": f"{fechados[0]}–{anomes}",
        "grupo": m["grupo"]["grupo"],
        "roladas_12m_anteriores": m["grupo"]["roladas_12m"],
        "roladas_com_atual": m["grupo"]["roladas_com_atual"],
        "roladas_ano_inteiro": sum(1 for f in hist_ano if f["rolada"]),
        "fatura_exata": m["fatura"]["valor"],
        "modo": m["fatura"]["modo_na_base"],
        "fatura_por_133": int(round(1.33 * compras_ant)),
        "compras_mes_anterior": compras_ant,
        "renda_recorrente": m["renda_recorrente"],
        "pix_mensal_mediana": m["flags"]["pix_mensal_mediana"],
        "pix_regular": m["flags"]["pix_regular"],
        "entradas_janela_div_meses": int(round(entradas / len(fechados + [anomes]))),
        "entradas_janela": entradas,
        "saidas_janela": saidas,
        "dia_recebimento": m["dia_recebimento"],
        "fixos": m["fixos"],
        "essenciais": m["essenciais"],
        "folga": m["folga"],
        "falta": m["falta"],
        "cabe": m["cabe"],
        "tipo_falta": m["tipo_falta"],
        "dias_ate_recebimento": m["dias_ate_recebimento"],
        "caminho": o["caminho"],
        "recomendada": (o["opcoes"][o["recomendada"]]["produto"] if o["recomendada"] is not None else None),
        "encaminhar_humano": o["encaminhar_humano"],
    }


def _linha_gold(gold: dict, cid: str) -> dict | None:
    for r in gold.get("tabelas", {}).get("gold_contexto_agente", []):
        if r.get("id_usuario") == cid:
            return r
    return None


def _linha(tabela: list[dict], cid: str, **filtro) -> dict | None:
    for r in tabela:
        if r.get("id_usuario") == cid and all(str(r.get(k)) == str(v) for k, v in filtro.items()):
            return r
    return None


def reais(v) -> str:
    """Valor da gold (float em reais) -> 'R$ x,yy'."""
    if v is None or v == "—":
        return "—"
    try:
        return brl(centavos(v))
    except Exception:
        return str(v)


def cent(v) -> str:
    """Valor do motor (int em centavos) -> 'R$ x,yy'."""
    if v is None or v == "—":
        return "—"
    try:
        return brl(int(v))
    except Exception:
        return str(v)


def imprimir(gold: dict | None, fonte, taxas: dict) -> None:
    print(f"# Gold do Lucas x motor cabe_core · {datetime.now():%d/%m/%Y %H:%M}\n")
    if gold:
        print(f"Consultas ao BigQuery: {gold.get('consultas')} em {gold.get('consultado_em')} (cache em `{CACHE.relative_to(RAIZ)}`).")
        for nome, erro in (gold.get("erros") or {}).items():
            print(f"- ERRO em `{nome}`: {erro}")
        print()
    for apelido, cid, anomes_demo in PERSONAS:
        g = _linha_gold(gold, cid) if gold else None
        h = _linha(gold["tabelas"].get("silver_historico_fatura_12m", []), cid) if gold else None
        c90 = _linha(gold["tabelas"].get("cash90_hackathon", []), cid) if gold else None
        cart = gold["tabelas"].get("silver_cartao", []) if gold else []
        anomes_c90 = int(c90["anomes_max"]) if c90 and c90.get("anomes_max") else None
        demo = lado_motor(fonte, cid, anomes_demo, taxas)
        mesma = lado_motor(fonte, cid, anomes_c90, taxas) if anomes_c90 else None
        print(f"## {apelido} · `{cid[:8]}` · demo em {anomes_demo}\n")
        if c90:
            print(f"cash90_hackathon: {c90['dt_min']} a {c90['dt_max']} (anomes {c90['anomes_min']}–{c90['anomes_max']}, {c90['n']} lançamentos). "
                  f"Motor na mesma janela: mês de referência {anomes_c90}, janela {mesma['janela'] if mesma else '—'}.\n")
        cab = f"| Campo | Gold/Silver (Lucas, janela cash90) | Motor, mesma janela (ref {anomes_c90 or '?'}) | Motor, mês da demo ({anomes_demo}) |"
        print(cab)
        print("|---|---|---|---|")

        def row(campo, gv, mv, dv):
            print(f"| {campo} | {gv} | {mv} | {dv} |")

        gq = (lambda k: (g or {}).get(k, "—"))
        hq = (lambda k: (h or {}).get(k, "—"))
        mq = (lambda k: (mesma or {}).get(k, "—"))
        row("grupo", gq("grupo_cliente"), mq("grupo"), demo["grupo"])
        row("faturas roladas contadas", f"{hq('qtd_pedaladas_12m')} (ano inteiro, jan–dez)",
            f"{mq('roladas_12m_anteriores')} nos 12 anteriores (+1 do mês = {mq('roladas_com_atual')})",
            f"{demo['roladas_12m_anteriores']} nos 12 anteriores (+1 do mês = {demo['roladas_com_atual']}); ano inteiro = {demo['roladas_ano_inteiro']}")
        row("última rolada", hq("ultima_pedalada"), "—", "—")
        row("fatura do mês", f"{reais(gq('fatura_estimada'))} (1,33 × compras do mês anterior, último mês da cash90)",
            f"{cent(mq('fatura_exata'))} exata (modo {mq('modo')}); 1,33× daria {cent(mq('fatura_por_133'))}",
            f"{cent(demo['fatura_exata'])} exata (modo {demo['modo']}); 1,33× daria {cent(demo['fatura_por_133'])}")
        for r in cart:
            if r.get("id_usuario") == cid and str(r.get("anomes")) in (str(anomes_demo), str(anomes_c90)):
                row(f"silver_cartao {r['anomes']}", f"compras {reais(r.get('compras_cartao_mes'))}; anterior {reais(r.get('compras_mes_anterior'))}; fatura_estimada {reais(r.get('fatura_estimada'))}",
                    "—", "—")
        row("renda mensal", f"{reais(gq('renda_mensal_estimada'))} (entradas_90d ÷ 3 = {reais(gq('entradas_90d'))} ÷ 3; inclui PIX, 13º, PLR)",
            f"{cent(mq('renda_recorrente'))} mediana de salário/INSS; PIX mediana {cent(mq('pix_mensal_mediana'))} (regular: {mq('pix_regular')}); entradas ÷ meses = {cent(mq('entradas_janela_div_meses'))}",
            f"{cent(demo['renda_recorrente'])} mediana de salário/INSS; PIX mediana {cent(demo['pix_mensal_mediana'])} (regular: {demo['pix_regular']}); entradas ÷ meses = {cent(demo['entradas_janela_div_meses'])}")
        row("dia do recebimento", gq("dia_recebimento_estimado"), mq("dia_recebimento"), demo["dia_recebimento"])
        row("compromissos do mês", f"fixos {reais(gq('gasto_recorrente_mensal_estimado'))} (macros casa/educação/empréstimo + micros condomínio/mensalidade/financiamento/assinatura ÷ 3); parcelas futuras {reais(gq('parcelas_futuras_estimadas'))}",
            f"fixos {cent(mq('fixos'))} + essenciais {cent(mq('essenciais'))} (medianas; lista de taxas.yaml; compras no cartão fora)",
            f"fixos {cent(demo['fixos'])} + essenciais {cent(demo['essenciais'])}")
        row("folga do mês", reais(gq("folga_mensal_estimada")), cent(mq("folga")), cent(demo["folga"]))
        row("saldo no vencimento", f"saldo_atual {reais(gq('saldo_atual'))} (último saldo_apos da cash90)", "não usa saldo_apos (data/README: não fecha)", "idem")
        row("falta / cabe", f"{reais(gq('valor_faltante'))} (fatura_estimada − saldo_atual) · cabe: {gq('fatura_cabe')}",
            f"{cent(mq('falta'))} (fatura exata − folga) · cabe: {mq('cabe')}", f"{cent(demo['falta'])} · cabe: {demo['cabe']}")
        row("tipo da falta", f"{gq('tipo_falta')} (PONTUAL se folga ≥ falta)",
            f"{mq('tipo_falta')} (pontual se o recebimento cobre em ≤ 25 dias: {mq('dias_ate_recebimento')} dias)",
            f"{demo['tipo_falta']} ({demo['dias_ate_recebimento']} dias até o recebimento)")
        row("elegibilidade / caminho", f"cobertura {gq('elegivel_cobertura_curta')} · parcelamento {gq('elegivel_parcelamento')} · {gq('acao_motor')} → {gq('recomendacao_motor')}",
            f"{mq('caminho')} → {mq('recomendada')} (humano: {mq('encaminhar_humano')})",
            f"{demo['caminho']} → {demo['recomendada']} (humano: {demo['encaminhar_humano']})")
        print()


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--atualizar", action="store_true", help="ignora o cache e consulta o BigQuery de novo (4 consultas)")
    p.add_argument("--sem-bigquery", action="store_true", help="não consulta; imprime só o lado do motor (e o cache, se existir)")
    p.add_argument("--via-bq", action="store_true", help="usa o `bq` do gcloud em vez de google-cloud-bigquery")
    a = p.parse_args()
    taxas = config.carregar_taxas()
    fonte = dados.FonteCsv()
    gold = None
    if a.sem_bigquery:
        gold = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else None
    else:
        try:
            gold = carregar_gold(a.atualizar, a.via_bq)
        except Exception as e:
            print(f"BigQuery indisponível ({type(e).__name__}: {e}); imprimindo só o lado do motor.\n", file=sys.stderr)
    imprimir(gold, fonte, taxas)
    return 0


if __name__ == "__main__":
    sys.exit(main())
