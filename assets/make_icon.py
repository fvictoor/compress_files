"""Gera o ícone do aplicativo (assets/app.ico e app/static/favicon.png).

Desenho: documento com zíper no meio, apertado por duas setas laterais.
Nos tamanhos pequenos as setas somem e o documento fica mais largo, para continuar legível.
Executar:  python assets/make_icon.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
CANVAS = 1024  # desenha grande e reduz para cada tamanho (antisserrilhado)
ICO_SIZES = [16, 20, 24, 32, 40, 48, 64, 128, 256]
SIMPLE_MAX = 24  # até este tamanho usa a versão sem setas

OUTLINE = "#1E3A5F"
PAPER = "#FFFFFF"
ZIP_TAPE = "#334155"
ZIP_TEETH = "#F59E0B"
ARROW = "#2563EB"


def draw(arrows: bool = True) -> Image.Image:
    img = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # Documento com o canto superior direito dobrado.
    if arrows:
        left, top, right, bottom, fold, line, k = 292, 72, 732, 952, 150, 44, 1.0
    else:
        left, top, right, bottom, fold, line, k = 150, 40, 874, 984, 230, 80, 1.6
    page = [(left, top), (right - fold, top), (right, top + fold), (right, bottom), (left, bottom)]
    d.polygon(page, fill=PAPER)
    d.line(page + [page[0]], fill=OUTLINE, width=line, joint="curve")
    d.polygon([(right - fold, top), (right - fold, top + fold), (right, top + fold)], fill="#CBD5E1")
    d.line([(right - fold, top), (right - fold, top + fold), (right, top + fold)],
           fill=OUTLINE, width=line, joint="curve")

    # Zíper: fita escura com dentes alternados descendo pelo centro.
    cx, tape_w = (left + right) // 2 - round(10 * k), round(92 * k)
    zip_top, zip_bottom = top + round(40 * k), bottom - round(210 * k)
    d.rectangle([cx - tape_w // 2, zip_top, cx + tape_w // 2, zip_bottom], fill=ZIP_TAPE)
    tooth_h, tooth_w = round(54 * k), round(64 * k)
    y, side = zip_top + 14, -1
    while y + tooth_h <= zip_bottom - 6:
        x0 = cx - tooth_w if side < 0 else cx
        d.rounded_rectangle([x0, y, x0 + tooth_w, y + tooth_h - round(14 * k)], radius=10, fill=ZIP_TEETH)
        y += tooth_h
        side = -side

    # Puxador do zíper.
    pull_w, pull_top = round(120 * k), zip_bottom - round(20 * k)
    d.rounded_rectangle([cx - pull_w // 2, pull_top, cx + pull_w // 2, pull_top + round(170 * k)],
                        radius=round(36 * k), fill=ZIP_TEETH, outline=OUTLINE, width=round(28 * k))
    d.rounded_rectangle([cx - round(22 * k), pull_top + round(70 * k), cx + round(22 * k),
                         pull_top + round(130 * k)], radius=14, fill=OUTLINE)
    if not arrows:
        return img

    # Setas apertando o documento pelos lados.
    mid, head, shaft = 512, 150, 64
    for tip, direction in ((left - 24, 1), (right + 24, -1)):
        base = tip - direction * head
        tail = tip - direction * 270
        d.rectangle([min(base, tail), mid - shaft // 2, max(base, tail), mid + shaft // 2], fill=ARROW)
        d.polygon([(tip, mid), (base, mid - head * 0.85), (base, mid + head * 0.85)], fill=ARROW)
    return img


def main() -> None:
    master, simple = draw(), draw(arrows=False)
    frames = [(simple if s <= SIMPLE_MAX else master).resize((s, s), Image.LANCZOS) for s in ICO_SIZES]
    ico = ROOT / "assets" / "app.ico"
    frames[-1].save(ico, format="ICO", sizes=[(s, s) for s in ICO_SIZES], append_images=frames[:-1])
    master.resize((64, 64), Image.LANCZOS).save(ROOT / "app" / "static" / "favicon.png")
    print(f"Gerado: {ico}")


if __name__ == "__main__":
    main()
