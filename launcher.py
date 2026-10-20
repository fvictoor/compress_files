"""Executável local do Compressor de Arquivos.

Sobe o servidor apenas em 127.0.0.1, abre o navegador e encerra sozinho quando
a última aba do aplicativo é fechada. Fechar a janela do prompt (ou Ctrl+C)
também encerra.

Executar:  python launcher.py
Gerar o instalador:  .\\build-exe.ps1
"""

from __future__ import annotations

import os
import shutil
import socket
import sys
import tempfile
import threading
import time
import webbrowser
from pathlib import Path

APP_NAME = "Compressor de Arquivos"
DATA_DIR = Path(os.getenv("LOCALAPPDATA") or Path.home()) / "CompressorArquivos"

# Precisa vir antes de importar o app: upload_log lê LOG_DIR na importação.
# Dentro do .exe a pasta do código é temporária, então o log vai para o perfil do usuário.
os.environ.setdefault("LOG_DIR", str(DATA_DIR / "logs"))
os.environ.setdefault("LOG_RETENTION_DAYS", "30")

import uvicorn  # noqa: E402

from app.main import app, sessions  # noqa: E402

HOST = "127.0.0.1"
# Tolerância sem nenhuma aba aberta antes de encerrar (cobre recarregar a página).
GRACE_SECONDS = 8
# Pastas de trabalho mais antigas que isso são restos de execuções interrompidas.
STALE_WORKDIR_SECONDS = 6 * 3600


def clean_stale_workdirs() -> None:
    """Apaga pastas temporárias deixadas quando o prompt foi fechado no meio de uma compressão."""
    cutoff = time.time() - STALE_WORKDIR_SECONDS
    for path in Path(tempfile.gettempdir()).glob("compress_*"):
        try:
            if path.is_dir() and path.stat().st_mtime < cutoff:
                shutil.rmtree(path, ignore_errors=True)
        except OSError:
            pass


def open_browser_when_ready(server: uvicorn.Server, url: str) -> None:
    while not server.started:
        if server.should_exit:
            return
        time.sleep(0.1)
    print(f"Aplicativo aberto em {url}")
    print("Feche a aba do navegador ou esta janela para encerrar.\n")
    webbrowser.open(url)


def shutdown_when_idle(server: uvicorn.Server) -> None:
    while not server.should_exit:
        time.sleep(1)
        if sessions.seen and sessions.idle_seconds() >= GRACE_SECONDS:
            print("Página fechada. Encerrando...")
            server.should_exit = True


def main() -> int:
    print(f"{APP_NAME}\nIniciando...\n")
    clean_stale_workdirs()

    # Porta livre escolhida pelo sistema; o socket já aberto é entregue ao uvicorn.
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind((HOST, 0))
    url = f"http://{HOST}:{sock.getsockname()[1]}"

    config = uvicorn.Config(
        app,
        log_level="warning",
        timeout_graceful_shutdown=3,
    )
    server = uvicorn.Server(config)
    threading.Thread(target=open_browser_when_ready, args=(server, url), daemon=True).start()
    threading.Thread(target=shutdown_when_idle, args=(server,), daemon=True).start()
    server.run(sockets=[sock])
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        # Mantém a janela aberta para o usuário conseguir ler o erro.
        print(f"\nErro ao iniciar: {type(exc).__name__}: {exc}")
        input("Pressione Enter para fechar...")
        sys.exit(1)
