"""Lógica de compressão independente do servidor web.

Aceita qualquer tipo de arquivo: os bytes são empacotados em um arquivo
compactado (ZIP ou TAR) e comprimidos com o algoritmo escolhido.
"""

from __future__ import annotations

import lzma
import tarfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

MIN_LEVEL = 1
MAX_LEVEL = 9


@dataclass(frozen=True)
class Format:
    key: str
    extension: str
    media_type: str
    label: str
    description: str


FORMATS: dict[str, Format] = {
    f.key: f
    for f in (
        Format(
            "tar.xz",
            ".tar.xz",
            "application/x-xz",
            "TAR.XZ (LZMA2)",
            "Melhor taxa de compressão. Abre no 7-Zip, WinRAR, Linux e macOS.",
        ),
        Format(
            "zip",
            ".zip",
            "application/zip",
            "ZIP (Deflate)",
            "Máxima compatibilidade. Abre nativamente em qualquer sistema.",
        ),
        Format(
            "tar.bz2",
            ".tar.bz2",
            "application/x-bzip2",
            "TAR.BZ2 (Bzip2)",
            "Boa compressão para textos e logs.",
        ),
        Format(
            "tar.gz",
            ".tar.gz",
            "application/gzip",
            "TAR.GZ (Gzip)",
            "Rápido e muito usado em servidores Linux.",
        ),
    )
}


def compress(files: list[tuple[Path, str]], output: Path, fmt: str, level: int) -> None:
    """Comprime `files` (lista de (caminho_no_disco, nome_no_arquivo)) em `output`.

    `level` vai de 1 (mais rápido) a 9 (máxima compressão).
    """
    if fmt not in FORMATS:
        raise ValueError(f"Formato não suportado: {fmt}")
    if not MIN_LEVEL <= level <= MAX_LEVEL:
        raise ValueError(f"Nível deve estar entre {MIN_LEVEL} e {MAX_LEVEL}")

    if fmt == "zip":
        with zipfile.ZipFile(
            output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=level
        ) as zf:
            for path, arcname in files:
                zf.write(path, arcname)
        return

    if fmt == "tar.xz":
        # No nível 9 ativa o modo "extreme" do LZMA para espremer mais alguns bytes.
        preset = level | lzma.PRESET_EXTREME if level == MAX_LEVEL else level
        tar = tarfile.open(output, "w:xz", preset=preset)
    elif fmt == "tar.bz2":
        tar = tarfile.open(output, "w:bz2", compresslevel=level)
    else:  # tar.gz
        tar = tarfile.open(output, "w:gz", compresslevel=level)

    with tar:
        for path, arcname in files:
            tar.add(path, arcname=arcname, recursive=False)
