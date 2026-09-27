"""Datas como anomes (AAAAMM). Convenção de mês de 30 dias para contar dias entre vencimento e recebimento."""

MESES_ABREV = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]


def valido(anomes: int) -> bool:
    ano, mes = divmod(int(anomes), 100)
    return 1900 <= ano <= 2999 and 1 <= mes <= 12


def anomes_soma(anomes: int, n: int) -> int:
    """Soma n meses (n pode ser negativo) a um anomes."""
    ano, mes = divmod(int(anomes), 100)
    idx = ano * 12 + (mes - 1) + int(n)
    return (idx // 12) * 100 + (idx % 12) + 1


def janela(anomes: int, n: int = 3) -> list[int]:
    """Os n meses fechados imediatamente antes de anomes, em ordem crescente."""
    return [anomes_soma(anomes, -k) for k in range(n, 0, -1)]


def meses_entre(anomes_ini: int, anomes_fim: int) -> list[int]:
    out, a = [], int(anomes_ini)
    while a <= int(anomes_fim):
        out.append(a)
        a = anomes_soma(a, 1)
    return out


def dias_ate_recebimento(dia_vencimento: int, dia_recebimento: int, dias_mes: int = 30) -> int:
    """Dias do vencimento da fatura até o próximo recebimento, no mesmo mês se ele ainda não caiu, senão no seguinte."""
    dv, dr = int(dia_vencimento), int(dia_recebimento)
    if dr > dv:
        return dr - dv
    return dias_mes - dv + dr


def rotulo(anomes: int) -> str:
    """202509 -> 'set/2025' (para 'termina em <mês/ano>')."""
    ano, mes = divmod(int(anomes), 100)
    return f"{MESES_ABREV[mes - 1]}/{ano}"
