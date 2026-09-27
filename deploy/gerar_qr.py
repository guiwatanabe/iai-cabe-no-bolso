"""Gera o QR code da URL da demo (slide do pitch e cartão impresso na mesa da banca).

Uso (da raiz do repo):
    uv run --project agent python deploy/gerar_qr.py https://cabe-no-bolso-xxxx-uc.a.run.app
    python3 deploy/gerar_qr.py URL --saida deploy/qr-cabe-no-bolso.png      # precisa de `pip install qrcode[pil]`

Imprime o QR em ASCII no terminal (conferência rápida) e grava o PNG. Se o Cloud Run falhar, use a URL local
com ?mock=1 (respostas gravadas): python3 -m http.server 8765 --directory demo.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from urllib.parse import urlsplit


def main() -> int:
    p = argparse.ArgumentParser(description="QR code da URL da demo do Cabe no Bolso")
    p.add_argument("url", help="URL final (https://...)")
    p.add_argument("--saida", default=str(Path(__file__).resolve().parent / "qr-cabe-no-bolso.png"), help="caminho do PNG")
    p.add_argument("--persona", choices=["ana", "bruno"], help="abre a demo já nessa persona (?persona=...)")
    p.add_argument("--sem-terminal", action="store_true", help="não imprime o QR em ASCII")
    a = p.parse_args()

    partes = urlsplit(a.url)
    if partes.scheme not in ("http", "https") or not partes.netloc:
        print(f"URL inválida: {a.url!r}", file=sys.stderr)
        return 2
    url = a.url.rstrip("/") + "/"
    if a.persona:
        url += f"?persona={a.persona}"

    try:
        import qrcode
    except ImportError:
        print("falta a lib qrcode: rode com `uv run --project agent python deploy/gerar_qr.py ...` ou `pip install 'qrcode[pil]'`", file=sys.stderr)
        return 1

    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=10, border=4)
    qr.add_data(url)
    qr.make(fit=True)
    if not a.sem_terminal:
        qr.print_ascii(invert=True)
    saida = Path(a.saida)
    saida.parent.mkdir(parents=True, exist_ok=True)
    qr.make_image(fill_color="#16201F", back_color="#FFFFFF").save(saida)
    print(f"QR de {url} gravado em {saida}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
