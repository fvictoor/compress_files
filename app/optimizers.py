"""Otimização com perdas para formatos que já vêm comprimidos (PDF e imagens).

Compressores genéricos (ZIP, XZ...) não reduzem PDFs e fotos porque o conteúdo
já está comprimido. A redução real vem de reamostrar imagens para a resolução
em que são exibidas e recodificá-las em JPEG com qualidade menor, que é o mesmo
princípio usado por serviços como o iLovePDF.
"""

from __future__ import annotations

import io
import zlib
from pathlib import Path

import pymupdf
from PIL import Image, ImageOps

# nível -> (DPI alvo das imagens no PDF, qualidade JPEG)
PDF_PRESETS = {
    1: (300, 90),
    2: (250, 85),
    3: (200, 80),
    4: (170, 76),
    5: (150, 72),
    6: (130, 68),
    7: (110, 62),
    8: (96, 55),
    9: (72, 45),
}

# nível -> (lado máximo em px ou None, qualidade JPEG/WebP, cores PNG ou None)
IMAGE_PRESETS = {
    1: (None, 92, None),
    2: (None, 88, None),
    3: (None, 84, None),
    4: (3840, 80, None),
    5: (3200, 76, None),
    6: (2560, 72, 256),
    7: (2048, 66, 256),
    8: (1920, 58, 192),
    9: (1600, 50, 128),
}

PDF_EXTENSIONS = {".pdf"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
OPTIMIZABLE_EXTENSIONS = PDF_EXTENSIONS | IMAGE_EXTENSIONS

MEDIA_TYPES = {
    ".pdf": "application/pdf",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}


def can_optimize(filename: str) -> bool:
    return Path(filename).suffix.lower() in OPTIMIZABLE_EXTENSIONS


def optimize(src: Path, dst: Path, filename: str, level: int) -> dict:
    """Otimiza `src` em `dst`.

    Retorna um dicionário com `applied` (True se o resultado ficou menor que o
    original), `reason` (motivo quando não aplicado) e `details` (estatísticas).
    """
    ext = Path(filename).suffix.lower()
    try:
        if ext in PDF_EXTENSIONS:
            details = _optimize_pdf(src, dst, level)
        elif ext in IMAGE_EXTENSIONS:
            details = _optimize_image(src, dst, ext, level)
        else:
            return {"applied": False, "reason": "formato sem otimização", "details": {}}
    except Exception as exc:
        # Arquivo corrompido, protegido por senha ou com recurso não suportado.
        return {"applied": False, "reason": f"{type(exc).__name__}: {exc}", "details": {}}

    if not dst.exists():
        return {"applied": False, "reason": "otimizador não gerou saída", "details": details}
    if dst.stat().st_size >= src.stat().st_size:
        return {
            "applied": False,
            "reason": "resultado não ficou menor que o original",
            "details": details,
        }
    return {"applied": True, "reason": None, "details": details}


# --------------------------------------------------------------------------- PDF


def _display_size_pt(doc: pymupdf.Document, xref: int) -> tuple[float, float]:
    """Maior tamanho (em pontos) em que a imagem aparece em qualquer página."""
    w = h = 0.0
    for page in doc:
        try:
            rects = page.get_image_rects(xref)
        except Exception:
            rects = []
        for r in rects:
            w, h = max(w, abs(r.width)), max(h, abs(r.height))
    if w == 0 or h == 0:
        # Imagem usada dentro de padrões/formulários: assume o tamanho da maior página.
        for page in doc:
            w, h = max(w, page.rect.width), max(h, page.rect.height)
    return w, h


def _pixmap_to_pil(pix: pymupdf.Pixmap) -> Image.Image:
    if pix.alpha:
        pix = pymupdf.Pixmap(pix, 0)  # remove alpha; a máscara é tratada à parte
    if pix.colorspace is None or pix.colorspace.n not in (1, 3):
        pix = pymupdf.Pixmap(pymupdf.csRGB, pix)  # CMYK/ICC/Indexed -> RGB
    mode = "L" if pix.n == 1 else "RGB"
    return Image.frombytes(mode, (pix.width, pix.height), pix.samples)


def _set_image_stream(doc, xref, data: bytes, width, height, filter_, colorspace) -> None:
    doc.update_stream(xref, data, compress=False)
    for key in ("DecodeParms", "Decode", "Intent", "Interpolate"):
        doc.xref_set_key(xref, key, "null")
    doc.xref_set_key(xref, "Filter", filter_)
    doc.xref_set_key(xref, "Width", str(width))
    doc.xref_set_key(xref, "Height", str(height))
    doc.xref_set_key(xref, "BitsPerComponent", "8")
    doc.xref_set_key(xref, "ColorSpace", colorspace)
    doc.xref_set_key(xref, "Length", str(len(data)))


def _optimize_pdf(src: Path, dst: Path, level: int) -> dict:
    target_dpi, quality = PDF_PRESETS[level]
    doc = pymupdf.open(src)
    if doc.needs_pass:
        raise ValueError("PDF protegido por senha")

    stats = {
        "preset_target_dpi": target_dpi,
        "preset_jpeg_quality": quality,
        "images_total": 0,
        "images_rewritten": 0,
        "images_kept": 0,
        "images_failed": 0,
        "image_errors": [],
        "fonts_subset": False,
    }
    image_xrefs = {img[0] for page in doc for img in page.get_images(full=True)}
    stats["images_total"] = len(image_xrefs)
    for xref in image_xrefs:
        try:
            if _optimize_pdf_image(doc, xref, target_dpi, quality):
                stats["images_rewritten"] += 1
            else:
                stats["images_kept"] += 1
        except Exception as exc:
            # mantém a imagem original se algo não for suportado
            stats["images_failed"] += 1
            if len(stats["image_errors"]) < 10:
                stats["image_errors"].append(f"xref {xref}: {type(exc).__name__}: {exc}")

    try:
        doc.subset_fonts()
        stats["fonts_subset"] = True
    except Exception as exc:
        stats["fonts_subset_error"] = f"{type(exc).__name__}: {exc}"
    doc.ez_save(dst, garbage=4, deflate=True, deflate_images=True, deflate_fonts=True, clean=True)
    doc.close()
    return stats


def _optimize_pdf_image(doc, xref: int, target_dpi: int, quality: int) -> bool:
    """Recomprime uma imagem do PDF. Retorna True se ela foi substituída."""
    obj = doc.xref_object(xref, compressed=True)
    if "/ImageMask true" in obj or "/BitsPerComponent 1" in obj:
        return False  # máscaras e imagens de 1 bit já são minúsculas

    original_size = len(doc.xref_stream_raw(xref) or b"")
    pix = pymupdf.Pixmap(doc, xref)
    w, h = pix.width, pix.height

    disp_w, disp_h = _display_size_pt(doc, xref)
    scale = max(disp_w / 72 * target_dpi / w, disp_h / 72 * target_dpi / h)
    scale = min(scale, 1.0)
    new_w, new_h = max(1, round(w * scale)), max(1, round(h * scale))

    img = _pixmap_to_pil(pix)
    if (new_w, new_h) != (w, h):
        img = img.resize((new_w, new_h), Image.LANCZOS)

    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality, optimize=True, progressive=True)
    data = buf.getvalue()
    if len(data) >= original_size and (new_w, new_h) == (w, h):
        return False  # sem ganho: mantém o original

    colorspace = "/DeviceGray" if img.mode == "L" else "/DeviceRGB"
    _set_image_stream(doc, xref, data, new_w, new_h, "/DCTDecode", colorspace)

    # A máscara de transparência precisa ter as mesmas dimensões da imagem.
    smask = doc.xref_get_key(xref, "SMask")
    if smask[0] == "xref":
        smask_xref = int(smask[1].split()[0])
        mask_pix = pymupdf.Pixmap(doc, smask_xref)
        mask = Image.frombytes(
            "L", (mask_pix.width, mask_pix.height), pymupdf.Pixmap(pymupdf.csGRAY, mask_pix).samples
        ) if mask_pix.n != 1 else Image.frombytes("L", (mask_pix.width, mask_pix.height), mask_pix.samples)
        if mask.size != (new_w, new_h):
            mask = mask.resize((new_w, new_h), Image.LANCZOS)
        _set_image_stream(
            doc, smask_xref, zlib.compress(mask.tobytes(), 9), new_w, new_h,
            "/FlateDecode", "/DeviceGray",
        )
        for key in ("Matte",):
            doc.xref_set_key(smask_xref, key, "null")
    return True


# ------------------------------------------------------------------------ Imagens


def _optimize_image(src: Path, dst: Path, ext: str, level: int) -> dict:
    max_side, quality, png_colors = IMAGE_PRESETS[level]
    with Image.open(src) as im:
        stats = {
            "preset_max_side_px": max_side,
            "preset_quality": quality,
            "preset_png_colors": png_colors if ext == ".png" else None,
            "original_dimensions": list(im.size),
            "original_mode": im.mode,
        }
        im = ImageOps.exif_transpose(im)  # aplica a rotação do EXIF antes de remover metadados
        if max_side and max(im.size) > max_side:
            im.thumbnail((max_side, max_side), Image.LANCZOS)
        stats["resized"] = list(im.size) != stats["original_dimensions"]
        stats["new_dimensions"] = list(im.size)
        stats["metadata_removed"] = True

        if ext in (".jpg", ".jpeg"):
            if im.mode not in ("RGB", "L"):
                im = im.convert("RGB")
            im.save(dst, "JPEG", quality=quality, optimize=True, progressive=True)
        elif ext == ".webp":
            im.save(dst, "WEBP", quality=quality, method=6)
        else:  # PNG
            if png_colors:
                # Quantização para paleta: grande redução com pouca perda visual.
                rgba = im.convert("RGBA")
                im = rgba.quantize(colors=png_colors, method=Image.Quantize.FASTOCTREE, dither=Image.Dither.FLOYDSTEINBERG)
            im.save(dst, "PNG", optimize=True)
        stats["output_mode"] = im.mode
    return stats
