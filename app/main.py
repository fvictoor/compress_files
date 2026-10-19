"""Servidor web de compressão de arquivos.

Executar:  uvicorn app.main:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

from .compressor import FORMATS, MAX_LEVEL, MIN_LEVEL, compress
from .optimizers import MEDIA_TYPES, can_optimize, optimize
from .upload_log import FileTracker, human_size, inspect_file, log_file, log_request, request_context

# Configuração via variáveis de ambiente
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "1024"))
MAX_FILES = int(os.getenv("MAX_FILES", "100"))
# Otimização de PDF e XZ nível 9 consomem bastante CPU/RAM; limita tarefas simultâneas.
MAX_CONCURRENT_JOBS = int(os.getenv("MAX_CONCURRENT_JOBS", "2"))

MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024
CHUNK_SIZE = 1024 * 1024
STATIC_DIR = Path(__file__).parent / "static"

# "auto": um único PDF/imagem volta no mesmo formato, otimizado; demais casos viram ZIP.
AUTO_FORMAT = "auto"
AUTO_FALLBACK = "zip"

app = FastAPI(title="Compressor de Arquivos")
_job_slots = asyncio.Semaphore(MAX_CONCURRENT_JOBS)


@app.middleware("http")
async def reject_oversized_uploads(request: Request, call_next):
    """Marca o horário de chegada e rejeita cedo uploads acima do limite."""
    request.state.received_at = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    request.state.received_perf = time.perf_counter()
    length = request.headers.get("content-length")
    if length and length.isdigit() and int(length) > MAX_UPLOAD_BYTES:
        error = f"Upload excede o limite de {MAX_UPLOAD_MB} MB."
        if request.url.path == "/api/compress":
            log_request(request_context(request), {}, {
                "status": "rejected", "http_status": 413, "error": error,
                "received_at": request.state.received_at,
            })
        return JSONResponse({"detail": error}, status_code=413)
    return await call_next(request)


@app.exception_handler(RequestValidationError)
async def log_validation_error(request: Request, exc: RequestValidationError):
    """Registra envios com campos inválidos (ex.: nível não numérico, sem arquivos)."""
    if request.url.path == "/api/compress":
        log_request(request_context(request), {}, {
            "status": "rejected",
            "http_status": 422,
            "error": "dados do formulário inválidos",
            "validation_errors": [
                {"loc": list(e.get("loc", [])), "msg": e.get("msg"), "type": e.get("type")}
                for e in exc.errors()
            ],
            "received_at": getattr(request.state, "received_at", None),
        })
    return await request_validation_exception_handler(request, exc)


def _safe_name(raw: str | None, used: set[str]) -> str:
    """Remove diretórios do nome enviado (evita path traversal) e evita duplicatas."""
    name = Path((raw or "").replace("\\", "/")).name.strip() or "arquivo"
    stem, suffix = Path(name).stem, Path(name).suffix
    candidate, n = name, 1
    while candidate.lower() in used:
        candidate = f"{stem} ({n}){suffix}"
        n += 1
    used.add(candidate.lower())
    return candidate


def _process_entries(
    entries: list[tuple[Path, str]], records: list[dict], level: int, do_optimize: bool
) -> list[tuple[Path, str]]:
    """Inspeciona cada arquivo e, se pedido, troca PDFs/imagens pela versão otimizada.

    Preenche `records` (um por arquivo) com o que foi encontrado e feito.
    """
    result = []
    for (path, arcname), rec in zip(entries, records):
        rec["inspection"] = inspect_file(path, arcname)

        if not can_optimize(arcname):
            rec["optimization"] = {"attempted": False, "reason": "tipo sem otimização com perdas"}
        elif not do_optimize:
            rec["optimization"] = {"attempted": False, "reason": "desativada pelo usuário"}
        else:
            optimized = path.with_name(f"{path.name}.opt")
            t = time.perf_counter()
            res = optimize(path, optimized, arcname, level)
            size_after = optimized.stat().st_size if optimized.exists() else None
            rec["optimization"] = {
                "attempted": True,
                "applied": res["applied"],
                "reason": res["reason"],
                "size_after_bytes": size_after,
                "size_after_human": human_size(size_after),
                "reduction_percent": round((1 - size_after / rec["size_bytes"]) * 100, 2)
                if size_after is not None and rec["size_bytes"] else None,
                "duration_ms": round((time.perf_counter() - t) * 1000, 1),
                "details": res["details"],
            }
            if res["applied"]:
                result.append((optimized, arcname))
                continue
        result.append((path, arcname))
    return result


def _fill_archive_sizes(output: Path, fmt_key: str, records: list[dict]) -> None:
    """No ZIP é possível saber o tamanho comprimido de cada arquivo."""
    if fmt_key != "zip":
        for rec in records:
            rec["size_in_output_bytes"] = None  # TAR comprime o pacote como um todo
        return
    with zipfile.ZipFile(output) as zf:
        infos = {i.filename: i for i in zf.infolist()}
    for rec in records:
        info = infos.get(rec["stored_name"])
        rec["size_in_output_bytes"] = info.compress_size if info else None
        rec["zip_crc32"] = f"{info.CRC:08x}" if info else None


@app.get("/api/config")
async def get_config():
    formats = [
        {
            "key": AUTO_FORMAT,
            "label": "Automático (recomendado)",
            "description": "PDF e imagens voltam no mesmo formato, otimizados. "
            "Outros arquivos ou vários arquivos são entregues em ZIP.",
        }
    ]
    formats += [
        {"key": f.key, "label": f.label, "description": f.description}
        for f in FORMATS.values()
    ]
    return {
        "formats": formats,
        "minLevel": MIN_LEVEL,
        "maxLevel": MAX_LEVEL,
        "maxUploadMb": MAX_UPLOAD_MB,
        "maxFiles": MAX_FILES,
    }


def _ms(since: float) -> float:
    return round((time.perf_counter() - since) * 1000, 1)


@app.post("/api/compress")
async def compress_files(
    request: Request,
    files: list[UploadFile] = File(...),
    format: str = Form(AUTO_FORMAT),
    level: int = Form(7),
    # str em vez de bool: com bool o FastAPI trata campo ausente como False.
    optimize_media_raw: str = Form("true", alias="optimize_media"),
):
    started = time.perf_counter()
    ctx = request_context(request)
    received_perf = getattr(request.state, "received_perf", None)
    optimize_media = optimize_media_raw.strip().lower() not in ("false", "0", "off", "no", "")
    options = {"format": format, "level": level, "optimize_media": optimize_media}
    summary: dict = {
        "received_at": getattr(request.state, "received_at", None),
        # Tempo para o servidor receber e decodificar o corpo multipart.
        "body_receive_ms": round((started - received_perf) * 1000, 1) if received_perf else None,
        "file_count": len(files),
        # Lista resumida: garante o registro dos arquivos mesmo se a validação falhar.
        "files_received": [
            {"filename": f.filename, "content_type": f.content_type, "size_bytes": f.size}
            for f in files
        ],
    }
    records: list[dict] = []
    workdir: Path | None = None
    total = 0

    try:
        if format != AUTO_FORMAT and format not in FORMATS:
            raise HTTPException(400, f"Formato inválido. Use: {AUTO_FORMAT}, {', '.join(FORMATS)}")
        if not MIN_LEVEL <= level <= MAX_LEVEL:
            raise HTTPException(400, f"Nível deve estar entre {MIN_LEVEL} e {MAX_LEVEL}.")
        if not files:
            raise HTTPException(400, "Envie ao menos um arquivo.")
        if len(files) > MAX_FILES:
            raise HTTPException(400, f"Máximo de {MAX_FILES} arquivos por envio.")

        workdir = Path(tempfile.mkdtemp(prefix="compress_"))
        input_dir = workdir / "in"
        input_dir.mkdir()
        used: set[str] = set()
        entries: list[tuple[Path, str]] = []

        # Copia os uploads para disco em blocos, calculando hashes e validando o tamanho.
        t = time.perf_counter()
        for index, upload in enumerate(files):
            arcname = _safe_name(upload.filename, used)
            tracker = FileTracker(index, upload, arcname)
            records.append(tracker.record)
            dest = input_dir / str(index)
            with dest.open("wb") as out:
                while chunk := await upload.read(CHUNK_SIZE):
                    total += len(chunk)
                    tracker.update(chunk)
                    if total > MAX_UPLOAD_BYTES:
                        tracker.finish()
                        raise HTTPException(413, f"Upload excede o limite de {MAX_UPLOAD_MB} MB.")
                    out.write(chunk)
            await upload.close()
            tracker.finish()
            entries.append((dest, arcname))
        summary["disk_write_ms"] = _ms(t)

        single_media = len(entries) == 1 and can_optimize(entries[0][1])
        do_optimize = optimize_media or (format == AUTO_FORMAT and single_media)

        # Trabalho pesado (CPU-bound) roda em thread, limitado pelo semáforo.
        t = time.perf_counter()
        async with _job_slots:
            summary["queue_wait_ms"] = _ms(t)

            t = time.perf_counter()
            entries = await run_in_threadpool(_process_entries, entries, records, level, do_optimize)
            summary["inspection_and_optimization_ms"] = _ms(t)

            t = time.perf_counter()
            if format == AUTO_FORMAT and single_media:
                # Devolve o próprio arquivo otimizado, sem empacotar.
                output, arcname = entries[0]
                suffix = Path(arcname).suffix
                download_name = f"{Path(arcname).stem}_comprimido{suffix}"
                media_type = MEDIA_TYPES.get(suffix.lower(), "application/octet-stream")
                output_format = f"{suffix.lower().lstrip('.')} (otimizado)"
                records[0]["size_in_output_bytes"] = output.stat().st_size
            else:
                fmt = FORMATS[AUTO_FALLBACK if format == AUTO_FORMAT else format]
                base = Path(entries[0][1]).stem if len(entries) == 1 else "arquivos_comprimidos"
                download_name = f"{base}{fmt.extension}"
                media_type = fmt.media_type
                output_format = fmt.key
                output = workdir / f"output{fmt.extension}"
                await run_in_threadpool(compress, entries, output, fmt.key, level)
                _fill_archive_sizes(output, fmt.key, records)
            summary["archive_ms"] = _ms(t)

        compressed = output.stat().st_size
        for rec in records:
            rec["status"] = "ok"
            log_file(ctx, options, rec)
        summary.update({
            "status": "ok",
            "http_status": 200,
            "original_total_bytes": total,
            "original_total_human": human_size(total),
            "output_bytes": compressed,
            "output_human": human_size(compressed),
            "reduction_percent": round((1 - compressed / total) * 100, 2) if total else None,
            "output_format": output_format,
            "download_name": download_name,
            "total_duration_ms": _ms(started),
        })
        log_request(ctx, options, summary)

        return FileResponse(
            output,
            media_type=media_type,
            filename=download_name,
            headers={
                "X-Original-Size": str(total),
                "X-Compressed-Size": str(compressed),
                "X-Request-Id": ctx["request_id"],
            },
            background=BackgroundTask(shutil.rmtree, workdir, ignore_errors=True),
        )
    except BaseException as exc:
        if workdir:
            shutil.rmtree(workdir, ignore_errors=True)
        if isinstance(exc, HTTPException):
            http_status, error = exc.status_code, str(exc.detail)
        elif isinstance(exc, asyncio.CancelledError):
            http_status, error = 499, "requisição cancelada (cliente desconectou)"
        else:
            http_status, error = 500, f"{type(exc).__name__}: {exc}"
        for rec in records:
            rec.setdefault("status", "error")
            log_file(ctx, options, rec)
        summary.update({
            "status": "error",
            "http_status": http_status,
            "error": error,
            "original_total_bytes": total,
            "total_duration_ms": _ms(started),
        })
        log_request(ctx, options, summary)
        raise


# Frontend estático (montado por último para não sobrepor as rotas /api).
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
