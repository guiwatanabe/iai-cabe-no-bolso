"""cabe_core.dados: FonteBigQuery com consulta injetada (sem rede) dá o MESMO número que FonteCsv; uma leitura por
cliente por sessão; tabela de 90 dias só quando a janela cabe; FonteMemoria; queda para CSV sem credencial."""
from __future__ import annotations

from cabe_core import capacidade, dados
from tests.conftest import ANA, BRUNO


def _linhas_bq(fonte_csv: dados.FonteCsv, cliente_id: str, janela: tuple[int, int] | None = None) -> list[dict]:
    """Simula as linhas que o BigQuery devolveria para SQL_EXTRATO: colunas da base, vlr em reais (float)."""
    out = []
    for l in fonte_csv._todos(cliente_id):
        if janela and not (janela[0] <= l["anomes"] <= janela[1]):
            continue
        out.append({
            "id_usuario": cliente_id, "anomes": l["anomes"], "dia": l["dia"], "tipo": l["tipo"], "descr": l["descr"] or None,
            "vlr": l["valor"] / 100, "nom_cate_macro": l["macro"] or None, "nom_cate_micro": l["micro"] or None,
            "parcela_atual": l["parcela_atual"], "parcela_total": l["parcela_total"],
        })
    return out


class Executor:
    """Executor injetado: registra cada consulta (tabela, cliente) e serve as linhas a partir do CSV."""

    def __init__(self, fonte_csv: dados.FonteCsv, janela_90d: tuple[int, int] | None = None):
        self.fonte_csv = fonte_csv
        self.janela_90d = janela_90d
        self.chamadas: list[tuple[str, str]] = []

    def __call__(self, sql: str, parametros: dict) -> list[dict]:
        tabela = sql.split("`")[1]
        cid = parametros["cliente_id"]
        self.chamadas.append((tabela, cid))
        janela = self.janela_90d if tabela.endswith("cash90_hackathon") else None
        return _linhas_bq(self.fonte_csv, cid, janela)


def test_fonte_bigquery_mesmo_numero_que_csv(fonte, taxas):
    ex = Executor(fonte)
    bq = dados.FonteBigQuery(executor=ex)
    for cid, anomes in ((BRUNO, 202509), (ANA, 202508)):
        assert bq.existe(cid)
        assert bq.extrato(cid, 202506, 202509) == fonte.extrato(cid, 202506, 202509)
        assert bq.faturas(cid, anomes, n=13) == fonte.faturas(cid, anomes, n=13)
        assert bq.perfil(cid) == fonte.perfil(cid)
        assert capacidade.motor(bq, cid, anomes, taxas) == capacidade.motor(fonte, cid, anomes, taxas)
    assert bq.consultas == 2 and [c for _, c in ex.chamadas] == [BRUNO, ANA]     # uma leitura por cliente por sessão
    assert all(t == dados.TABELA_PADRAO for t, _ in ex.chamadas)


def test_fonte_bigquery_uma_consulta_por_cliente_por_sessao(fonte):
    ex = Executor(fonte)
    bq = dados.FonteBigQuery(executor=ex)
    bq.existe(BRUNO)
    for _ in range(3):
        bq.extrato(BRUNO, 202501, 202512)
        bq.faturas(BRUNO, 202512, n=12)
        bq.perfil(BRUNO)
    assert bq.consultas == 1
    assert not bq.existe("00000000-0000-0000-0000-000000000000") and bq.consultas == 2


def test_fonte_bigquery_tabela_90d_so_quando_a_janela_cabe(fonte, monkeypatch):
    monkeypatch.delenv("BIGQUERY_TABELA_90D", raising=False)
    monkeypatch.delenv("BIGQUERY_JANELA_90D", raising=False)
    ex = Executor(fonte, janela_90d=(202510, 202512))
    bq = dados.FonteBigQuery(executor=ex, tabela_90d=dados.TABELA_90D_PADRAO, janela_90d=(202510, 202512))
    # cabe na janela D-90: lê a tabela de 90 dias
    assert bq.extrato(BRUNO, 202510, 202512) == fonte.extrato(BRUNO, 202510, 202512)
    assert ex.chamadas == [(dados.TABELA_90D_PADRAO, BRUNO)]
    assert bq.faturas(BRUNO, 202512, n=3) == fonte.faturas(BRUNO, 202512, n=3) and bq.consultas == 1
    # o histórico de 13 faturas não cabe: lê a tabela completa, que substitui o cache; nada além disso
    assert bq.faturas(BRUNO, 202512, n=13) == fonte.faturas(BRUNO, 202512, n=13)
    assert ex.chamadas[-1] == (dados.TABELA_PADRAO, BRUNO) and bq.consultas == 2
    assert bq.extrato(BRUNO, 202501, 202503) == fonte.extrato(BRUNO, 202501, 202503) and bq.consultas == 2
    assert bq.perfil(BRUNO) == fonte.perfil(BRUNO) and bq.consultas == 2
    # sem tabela de 90 dias configurada: sempre a completa, uma vez
    bq2 = dados.FonteBigQuery(executor=Executor(fonte))
    bq2.extrato(ANA, 202510, 202512)
    bq2.faturas(ANA, 202512, n=13)
    assert bq2.consultas == 1 and bq2.tabela_90d is None


def test_fonte_bigquery_config_por_env(monkeypatch):
    monkeypatch.setenv("BIGQUERY_TABELA", "p.d.extrato")
    monkeypatch.setenv("BIGQUERY_TABELA_90D", "p.d.cash90")
    monkeypatch.setenv("BIGQUERY_JANELA_90D", "202510-202512")
    bq = dados.FonteBigQuery(executor=lambda sql, p: [])
    assert (bq.tabela, bq.tabela_90d, bq.janela_90d) == ("p.d.extrato", "p.d.cash90", (202510, 202512))
    assert dados._janela("") is None and dados._janela("x") is None and dados._janela("202512-202510") is None
    assert dados._janela("202510–202512") == (202510, 202512)


def test_fonte_bigquery_teto_de_bytes_por_consulta_vem_do_finops_yaml(monkeypatch):
    """Trava do BigQuery (docs/11 §3, docs/12 §7): maximum_bytes_billed = travas.bigquery_maximum_bytes_billed (1 GB)."""
    from cabe_core import finops

    bq = dados.FonteBigQuery(executor=lambda sql, p: [])
    assert bq.maximum_bytes_billed == finops.travas()["bigquery_maximum_bytes_billed"] == 1_000_000_000
    assert dados.FonteBigQuery(executor=lambda sql, p: [], maximum_bytes_billed=5).maximum_bytes_billed == 5
    monkeypatch.setattr(finops, "travas", lambda cfg=None: {})          # YAML sem a trava: sem teto, mas sem quebrar
    assert dados.FonteBigQuery(executor=lambda sql, p: []).maximum_bytes_billed is None
    assert "@cliente_id" in dados.FonteBigQuery.SQL_EXTRATO               # só consulta parametrizada; nunca texto no SQL


def test_fonte_memoria_faturas_e_perfil():
    lanc = [
        {"anomes": 202507, "dia": 5, "tipo": "E", "descr": "beneficio", "valor": 300000, "macro": "Salarios e bonificacoes",
         "micro": "Beneficio INSS", "cartao": False, "parcela_atual": None, "parcela_total": None},
        {"anomes": 202507, "dia": 20, "tipo": "S", "descr": "pag fatura cartao minimo", "valor": 15000, "macro": "Cartao",
         "micro": "Pagamento de fatura", "cartao": False, "parcela_atual": None, "parcela_total": None},
        {"anomes": 202507, "dia": 20, "tipo": "S", "descr": "juros", "valor": 1400, "macro": "Cartao",
         "micro": "Juros pagos", "cartao": False, "parcela_atual": None, "parcela_total": None},
        {"anomes": 202508, "dia": 20, "tipo": "S", "descr": "pag fatura cartao parcial", "valor": 50000, "macro": "Cartao",
         "micro": "Pagamento de fatura", "cartao": False, "parcela_atual": None, "parcela_total": None},
        {"anomes": 202508, "dia": 20, "tipo": "S", "descr": "juros", "valor": 7000, "macro": "Cartao",
         "micro": "Juros pagos", "cartao": False, "parcela_atual": None, "parcela_total": None},
    ]
    f = dados.FonteMemoria({"c1": lanc})
    assert f.existe("c1") and not f.existe("c2")
    fats = f.faturas("c1", 202508, n=12)
    assert [x["anomes"] for x in fats] == [202507, 202508]
    assert fats[0]["fatura"] == 100000 and fats[0]["modo"] == "minimo"            # 150,00 / 0,15
    assert fats[1]["fatura"] == 100000 and fats[1]["modo"] == "parcial"           # 500,00 + 70,00 / 0,14
    assert f.faturas("c1", 202507, n=12)[-1]["anomes"] == 202507                    # não olha o futuro
    assert f.perfil("c1")["tipo_renda"] == "inss"
    assert f.extrato("c2", 202501, 202512) == [] and f.faturas("c2", 202512) == []


def test_fonte_padrao_cai_para_csv_sem_bigquery(monkeypatch):
    monkeypatch.setenv("DADOS", "bigquery")

    def sem_credencial(self):
        raise RuntimeError("sem ADC")

    monkeypatch.setattr(dados.FonteBigQuery, "_bq", sem_credencial)
    assert isinstance(dados.fonte_padrao(), dados.FonteCsv)
    monkeypatch.setenv("DADOS", "csv")
    assert dados.fonte_padrao().nome == "csv"
