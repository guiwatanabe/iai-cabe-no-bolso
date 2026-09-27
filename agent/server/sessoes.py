"""Sessões em memória: {sessao_id: estado} com TTL por inatividade e teto de sessões.

Um worker de uvicorn e --session-affinity no Cloud Run (docs/decisoes): o estado vive no processo. O estado é um dict
serializável (o runtime do agente pode copiá-lo para o session.state do ADK). Fora do dict só ficam os locks.

Estado da sessão (chaves estáveis para server, conversa e cabe_no_bolso.runtime):
  sessao_id, cliente_id, anomes, persona, apelido, perfil_texto, consentimento (bool|None), registro_consentimento,
  mes_simulado, contar_pix (bool|None), motor, ofertas, plano, ciclos, escolha ({acao, valor}), oferta_recusada,
  encerrado (bool), historico_contratacoes, numeros_validados [{valor, origem}], trace [...], finops {...},
  modo ('sem_llm'|'llm'), criado_em, ultimo_acesso.
"""
from __future__ import annotations

import asyncio
import secrets
import time
from datetime import datetime, timezone


def agora_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def novo_estado(cliente_id: str, anomes: int, persona: str | None, apelido: str, perfil_texto: str, modo: str) -> dict:
    return {
        "sessao_id": None,
        "cliente_id": cliente_id,
        "anomes": int(anomes),
        "persona": persona,
        "apelido": apelido,
        "perfil_texto": perfil_texto,
        "consentimento": None,
        "registro_consentimento": None,
        "mes_simulado": int(anomes),
        "contar_pix": None,
        "motor": None,
        "ofertas": None,
        "plano": None,
        "ciclos": [],
        "escolha": None,
        "oferta_recusada": False,
        "encerrado": False,
        "historico_contratacoes": [],
        "numeros_validados": [],
        "_numeros_vistos": [],
        "trace": [],
        "finops": {"chamadas_llm": 0, "tokens_entrada": 0, "tokens_saida": 0, "latencias_ms": []},
        "modo": modo,
        "criado_em": agora_iso(),
        "ultimo_acesso": time.monotonic(),
    }


def adicionar_numeros(estado: dict, lista: list[dict] | None) -> None:
    """Acumula numeros_validados (dedup por valor+origem), como o state do agente."""
    vistos = estado.setdefault("_numeros_vistos", [])
    conjunto = set(map(tuple, vistos))
    for n in lista or []:
        if not isinstance(n, dict) or "valor" not in n or not n.get("origem"):
            continue
        v = n["valor"]
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            continue
        chave = (v, n["origem"])
        if chave in conjunto:
            continue
        conjunto.add(chave)
        vistos.append(list(chave))
        estado["numeros_validados"].append({"valor": v, "origem": n["origem"]})


def registrar_trace(estado: dict, ferramenta: str, argumentos: dict | None, resumo: str, numeros: list[dict] | None = None,
                    duracao_ms: float | None = None, llm: bool = False, etapa: str | None = None) -> dict:
    """Um item do painel "como cheguei aqui" (e do Cloud Logging). Sem PII: cliente_id só abreviado."""
    args = dict(argumentos or {})
    if isinstance(args.get("cliente_id"), str) and len(args["cliente_id"]) > 8:
        args["cliente_id"] = args["cliente_id"][:8] + "…"
    item = {
        "ordem": len(estado["trace"]) + 1,
        "etapa": etapa,
        "ferramenta": ferramenta,
        "argumentos": args,
        "resumo": resumo,
        "numeros": [dict(n) for n in (numeros or [])][:40],
        "duracao_ms": None if duracao_ms is None else round(float(duracao_ms), 1),
        "ts": agora_iso(),
        "llm": bool(llm),
    }
    estado["trace"].append(item)
    return item


def estado_publico(estado: dict) -> dict:
    """Cópia sem as chaves internas (prefixo _), para o runtime do agente e para depuração."""
    return {k: v for k, v in estado.items() if not str(k).startswith("_")}


class Sessoes:
    def __init__(self, ttl_s: int = 2 * 60 * 60, maximo: int = 500):
        self.ttl_s = int(ttl_s)
        self.maximo = int(maximo)
        self._dados: dict[str, dict] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def __len__(self) -> int:
        return len(self._dados)

    def criar(self, estado: dict) -> str:
        self.varrer()
        if len(self._dados) >= self.maximo:  # descarta a sessão mais antiga
            mais_antiga = min(self._dados, key=lambda k: self._dados[k]["ultimo_acesso"])
            self.remover(mais_antiga)
        sessao_id = secrets.token_urlsafe(12)
        estado["sessao_id"] = sessao_id
        estado["ultimo_acesso"] = time.monotonic()
        self._dados[sessao_id] = estado
        self._locks[sessao_id] = asyncio.Lock()
        return sessao_id

    def obter(self, sessao_id: str) -> dict | None:
        e = self._dados.get(sessao_id)
        if e is None:
            return None
        if time.monotonic() - e["ultimo_acesso"] > self.ttl_s:
            self.remover(sessao_id)
            return None
        e["ultimo_acesso"] = time.monotonic()
        return e

    def lock(self, sessao_id: str) -> asyncio.Lock:
        return self._locks.setdefault(sessao_id, asyncio.Lock())

    def remover(self, sessao_id: str) -> None:
        self._dados.pop(sessao_id, None)
        self._locks.pop(sessao_id, None)

    def varrer(self) -> int:
        agora = time.monotonic()
        vencidas = [k for k, e in self._dados.items() if agora - e["ultimo_acesso"] > self.ttl_s]
        for k in vencidas:
            self.remover(k)
        return len(vencidas)
