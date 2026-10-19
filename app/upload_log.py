"""Log de auditoria dos arquivos enviados.

Grava em `logs/uploads.jsonl` (JSON Lines, uma linha por evento), com rotação
diária. Cada envio gera:
  - um evento "file" por arquivo, com o máximo de detalhes;
  - um evento "request" com o resumo da requisição.

O conteúdo dos arquivos nunca é gravado, apenas metadados e hashes.

Ler o log no PowerShell:
  Get-Content logs\\uploads.jsonl | ConvertFrom-Json | Format-List
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import mimetypes
import os
import platform
import socket
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Any

from fastapi import Request

LOG_DIR = Path(os.getenv("LOG_DIR", Path(__file__).resolve().parent.parent / "logs"))
LOG_RETENTION_DAYS = int(os.getenv("LOG_RETENTION_DAYS", "90"))

SAMPLE_BYTES = 64 * 1024  # amostra do início do arquivo para detectar tipo e entropia
SERVER_INFO = {
    "hostname": socket.gethostname(),
    "pid": os.getpid(),
    "python": platform.python_version(),
    "platform": platform.platform(),
}

# Extensões que são ZIP por dentro (não contam como divergência de tipo).
ZIP_CONTAINERS = {
    ".docx", ".xlsx", ".pptx", ".odt", ".ods", ".odp", ".jar", ".apk", ".epub", ".xpi", ".vsix",
}
# Formatos antigos do Office (contêiner OLE).
OLE_CONTAINERS = {".doc", ".xls", ".ppt", ".msg", ".msi"}

_SIGNATURES: list[tuple[bytes, str]] = [
    (b"%PDF-", "application/pdf"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"II*\x00", "image/tiff"),
    (b"MM\x00*", "image/tiff"),
    (b"\x00\x00\x01\x00", "image/x-icon"),
    (b"PK\x03\x04", "application/zip"),
    (b"PK\x05\x06", "application/zip"),
    (b"\x1f\x8b", "application/gzip"),
    (b"BZh", "application/x-bzip2"),
    (b"\xfd7zXZ\x00", "application/x-xz"),
    (b"\x28\xb5\x2f\xfd", "application/zstd"),
    (b"7z\xbc\xaf\x27\x1c", "application/x-7z-compressed"),
    (b"Rar!\x1a\x07", "application/vnd.rar"),
    (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", "application/x-ole-storage"),
    (b"SQLite format 3\x00", "application/vnd.sqlite3"),
    (b"MZ", "application/x-msdownload"),
    (b"\x7fELF", "application/x-executable"),
    (b"\xca\xfe\xba\xbe", "application/java-vm"),
    (b"ID3", "audio/mpeg"),
    (b"OggS", "audio/ogg"),
    (b"fLaC", "audio/flac"),
    (b"\x1aE\xdf\xa3", "video/webm"),
    (b"{\\rtf", "application/rtf"),
    (b"BM", "image/bmp"),
]


# ------------------------------------------------------------------ infraestrutura


def _build_logger() -> logging.Logger:
    logger = logging.getLogger("compress.uploads")
    if not logger.handlers:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        handler = TimedRotatingFileHandler(
            LOG_DIR / "uploads.jsonl",
            when="midnight",
            backupCount=LOG_RETENTION_DAYS,
            encoding="utf-8",
            utc=True,
        )
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger


_file_logger = _build_logger()
_console = logging.getLogger("uvicorn.error")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def human_size(n: int | None) -> str | None:
    if n is None:
        return None
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.2f} {unit}"
        size /= 1024
    return None


def _write(event: dict[str, Any]) -> None:
    try:
        _file_logger.info(json.dumps(event, ensure_ascii=False, default=str))
    except Exception:  # o log nunca pode derrubar a requisição
        _console.exception("Falha ao gravar log de upload")


def request_context(request: Request) -> dict[str, Any]:
    """Dados do cliente e da conexão HTTP."""
    h = request.headers
    return {
        "request_id": uuid.uuid4().hex,
        "client_ip": request.client.host if request.client else None,
        "client_port": request.client.port if request.client else None,
        "x_forwarded_for": h.get("x-forwarded-for"),
        "x_real_ip": h.get("x-real-ip"),
        "user_agent": h.get("user-agent"),
        "referer": h.get("referer"),
        "origin": h.get("origin"),
        "accept_language": h.get("accept-language"),
        "host": h.get("host"),
        "scheme": request.url.scheme,
        "http_version": request.scope.get("http_version"),
        "method": request.method,
        "path": request.url.path,
        "content_length_header": h.get("content-length"),
        "content_type_header": h.get("content-type", "").split(";")[0] or None,
    }


def log_file(ctx: dict, options: dict, record: dict) -> None:
    _write({"event": "file", "ts": now_iso(), **ctx, "options": options, **record})


def log_request(ctx: dict, options: dict, summary: dict) -> None:
    _write({"event": "request", "ts": now_iso(), **ctx, "options": options,
            "server": SERVER_INFO, **summary})
    parts = [
        f"[upload {ctx['request_id'][:8]}]",
        str(ctx.get("client_ip") or "-"),
        str(summary.get("status") or "-"),
    ]
    if summary.get("file_count") is not None:
        parts.append(f"{summary['file_count']} arquivo(s)")
    if summary.get("output_bytes") is not None:
        parts.append(f"{human_size(summary.get('original_total_bytes'))} -> {human_size(summary['output_bytes'])}")
    if summary.get("total_duration_ms") is not None:
        parts.append(f"{summary['total_duration_ms']} ms")
    if summary.get("error"):
        parts.append(f"erro: {summary['error']}")
    _console.info(" | ".join(parts))


# --------------------------------------------------------------- dados por arquivo


def detect_type(head: bytes) -> str | None:
    """Tipo real do arquivo pelos primeiros bytes ("magic numbers")."""
    if len(head) >= 12 and head[:4] == b"RIFF":
        return {b"WEBP": "image/webp", b"WAVE": "audio/wav", b"AVI ": "video/x-msvideo"}.get(
            head[8:12], "application/x-riff"
        )
    if len(head) >= 12 and head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand in (b"heic", b"heix", b"mif1", b"msf1"):
            return "image/heic"
        if brand == b"avif":
            return "image/avif"
        if brand.startswith(b"M4A"):
            return "audio/mp4"
        if brand == b"qt  ":
            return "video/quicktime"
        return "video/mp4"
    for sig, mime in _SIGNATURES:
        if head.startswith(sig):
            return mime
    if head and _looks_like_text(head):
        stripped = head.lstrip()[:100].lower()
        if stripped.startswith((b"<?xml", b"<svg")):
            return "image/svg+xml" if b"<svg" in head[:2048].lower() else "application/xml"
        if stripped.startswith((b"<!doctype html", b"<html")):
            return "text/html"
        if stripped.startswith((b"{", b"[")):
            return "application/json"
        return "text/plain"
    return None


def _looks_like_text(head: bytes) -> bool:
    sample = head[:8192]
    if b"\x00" in sample:
        return False
    try:
        sample.decode("utf-8")
        return True
    except UnicodeDecodeError as exc:
        # tolera um caractere multibyte cortado no fim da amostra
        if exc.start >= len(sample) - 3:
            return True
        try:
            sample.decode("cp1252")
            return True
        except UnicodeDecodeError:
            return False


def _text_encoding(head: bytes) -> str | None:
    if head.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    if head.startswith((b"\xff\xfe", b"\xfe\xff")):
        return "utf-16"
    try:
        head[:8192].decode("utf-8")
        return "utf-8" if any(b > 127 for b in head[:8192]) else "ascii"
    except UnicodeDecodeError as exc:
        if exc.start >= len(head[:8192]) - 3:
            return "utf-8"
        return "cp1252/latin-1"


def _entropy(data: bytes) -> float | None:
    """Entropia de Shannon (0 a 8 bits/byte). Próximo de 8 = já comprimido/criptografado."""
    if not data:
        return None
    n = len(data)
    return round(-sum(c / n * math.log2(c / n) for c in Counter(data).values()), 3)


class FileTracker:
    """Acumula hashes e informações enquanto o upload é gravado em disco."""

    def __init__(self, index: int, upload, stored_name: str) -> None:
        self._sha256 = hashlib.sha256()
        self._sha1 = hashlib.sha1(usedforsecurity=False)
        self._md5 = hashlib.md5(usedforsecurity=False)
        self._head = bytearray()
        self._started = time.perf_counter()
        self.size = 0
        ext = Path(stored_name).suffix.lower()
        self.record: dict[str, Any] = {
            "file_index": index,
            "original_filename": upload.filename,
            "stored_name": stored_name,
            "renamed": upload.filename != stored_name,
            "extension": ext or None,
            "declared_content_type": upload.content_type,
            "type_from_extension": mimetypes.guess_type(stored_name)[0],
            "part_headers": {k: v for k, v in upload.headers.items()},
            "upload_started_at": now_iso(),
        }

    def update(self, chunk: bytes) -> None:
        self.size += len(chunk)
        self._sha256.update(chunk)
        self._sha1.update(chunk)
        self._md5.update(chunk)
        if len(self._head) < SAMPLE_BYTES:
            self._head += chunk[: SAMPLE_BYTES - len(self._head)]

    def finish(self) -> None:
        head = bytes(self._head)
        detected = detect_type(head)
        ext = self.record["extension"]
        from_ext = self.record["type_from_extension"]
        self.record.update(
            {
                "size_bytes": self.size,
                "size_human": human_size(self.size),
                "empty_file": self.size == 0,
                "sha256": self._sha256.hexdigest(),
                "sha1": self._sha1.hexdigest(),
                "md5": self._md5.hexdigest(),
                "magic_bytes_hex": head[:16].hex(" "),
                "detected_type": detected,
                "type_mismatch": _is_mismatch(detected, from_ext, ext),
                "text_encoding": _text_encoding(head) if detected and (
                    detected.startswith("text/") or detected in ("application/json", "application/xml")
                ) else None,
                "entropy_bits_per_byte_sample": _entropy(head),
                "disk_write_ms": round((time.perf_counter() - self._started) * 1000, 1),
            }
        )


def _is_mismatch(detected: str | None, from_ext: str | None, ext: str | None) -> bool | None:
    """True quando o conteúdo não bate com a extensão (ex.: .pdf que na verdade é .exe)."""
    if not detected or not from_ext:
        return None
    if detected == from_ext:
        return False
    if detected == "application/zip" and ext in ZIP_CONTAINERS:
        return False
    if detected == "application/x-ole-storage" and ext in OLE_CONTAINERS:
        return False
    if detected.startswith("text/") or detected in ("application/json", "application/xml"):
        # arquivos de texto (csv, js, py, md...) só divergem se a extensão indicar binário
        return not (from_ext.startswith("text/") or from_ext.startswith("application/"))
    if detected.split("/")[0] == from_ext.split("/")[0] and detected.split("/")[0] in ("image", "audio", "video"):
        return detected != from_ext
    if detected == "application/x-msdownload" and ext in (".exe", ".dll", ".sys", ".scr", ".com"):
        return False
    return True


# ------------------------------------------------------------- inspeção de conteúdo


def inspect_file(path: Path, filename: str) -> dict[str, Any]:
    """Metadados internos de PDFs e imagens. Nunca lança exceção."""
    ext = Path(filename).suffix.lower()
    started = time.perf_counter()
    try:
        if ext == ".pdf":
            info = _inspect_pdf(path)
        elif ext in (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff"):
            info = _inspect_image(path)
        else:
            return {}
    except Exception as exc:
        info = {"error": f"{type(exc).__name__}: {exc}"}
    info["inspection_ms"] = round((time.perf_counter() - started) * 1000, 1)
    return info


def _inspect_pdf(path: Path) -> dict[str, Any]:
    import pymupdf

    with pymupdf.open(path) as doc:
        info: dict[str, Any] = {
            "kind": "pdf",
            "page_count": doc.page_count,
            "encrypted": doc.is_encrypted,
            "needs_password": bool(doc.needs_pass),
            "repaired": doc.is_repaired,
        }
        if doc.needs_pass:
            return info
        info["metadata"] = {k: v for k, v in (doc.metadata or {}).items() if v}
        info["form_fields"] = bool(doc.is_form_pdf)
        info["embedded_files"] = doc.embfile_count()
        info["has_toc"] = bool(doc.get_toc())
        info["permissions"] = doc.permissions

        pages, images, fonts = [], {}, set()
        text_chars = 0
        links = annots = 0
        for page in doc:
            if len(pages) < 20:
                pages.append({
                    "n": page.number + 1,
                    "width_pt": round(page.rect.width, 1),
                    "height_pt": round(page.rect.height, 1),
                    "rotation": page.rotation,
                })
            for img in page.get_images(full=True):
                xref, _, w, h, bpc, cs, _, _, filt = img[:9]
                images.setdefault(xref, {"width": w, "height": h, "bpc": bpc,
                                         "colorspace": cs, "filter": filt})
            fonts.update(f[3] for f in page.get_fonts(full=True))
            if page.number < 10:
                text_chars += len(page.get_text("text").strip())
            links += len(page.get_links())
            annots += sum(1 for _ in page.annots())

        image_bytes = 0
        for xref, meta in images.items():
            try:
                meta["stream_bytes"] = len(doc.xref_stream_raw(xref) or b"")
                image_bytes += meta["stream_bytes"]
            except Exception:
                pass
        biggest = sorted(images.values(), key=lambda m: m.get("stream_bytes", 0), reverse=True)[:5]

        info.update({
            "pages": pages,
            "image_count": len(images),
            "image_stream_total_bytes": image_bytes,
            "largest_images": biggest,
            "font_count": len(fonts),
            "fonts": sorted(fonts)[:50],
            "has_text_layer": text_chars > 0,
            "text_chars_first_10_pages": text_chars,
            "link_count": links,
            "annotation_count": annots,
        })
        return info


def _inspect_image(path: Path) -> dict[str, Any]:
    from PIL import ExifTags, Image

    with Image.open(path) as im:
        info: dict[str, Any] = {
            "kind": "image",
            "format": im.format,
            "format_description": im.format_description,
            "width": im.width,
            "height": im.height,
            "megapixels": round(im.width * im.height / 1e6, 2),
            "mode": im.mode,
            "frames": getattr(im, "n_frames", 1),
            "animated": getattr(im, "is_animated", False),
            "has_icc_profile": "icc_profile" in im.info,
            "has_transparency": im.mode in ("RGBA", "LA", "PA") or "transparency" in im.info,
        }
        if dpi := im.info.get("dpi"):
            info["dpi"] = [round(float(d), 1) for d in dpi]
        if im.format == "JPEG":
            info["progressive"] = bool(im.info.get("progressive") or im.info.get("progression"))

        exif = im.getexif()
        if exif:
            wanted = {"Make", "Model", "Software", "DateTime", "Orientation", "Artist",
                      "Copyright", "ImageDescription", "XResolution", "YResolution"}
            tags = {ExifTags.TAGS.get(k, str(k)): v for k, v in exif.items()}
            info["exif"] = {k: v for k, v in tags.items() if k in wanted}
            # Só indicamos a presença de GPS; coordenadas são dado pessoal sensível.
            info["has_gps"] = ExifTags.IFD.GPSInfo in exif
            info["exif_tag_count"] = len(exif)
        return info
