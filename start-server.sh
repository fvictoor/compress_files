#!/usr/bin/env bash
# Sobe o servidor do Compressor de Arquivos (Linux/macOS).
#
# Uso:
#   ./start-server.sh                 # 0.0.0.0:8000
#   PORT=9000 MAX_UPLOAD_MB=2048 ./start-server.sh
#   FORWARDED_ALLOW_IPS=10.0.0.5 ./start-server.sh
#   ./start-server.sh --dev           # recarrega ao salvar arquivos
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$ROOT/.venv"
PY="$VENV/bin/python"
REQ="$ROOT/requirements.txt"
STAMP="$VENV/.requirements.sha256"

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"
FORWARDED_ALLOW_IPS="${FORWARDED_ALLOW_IPS:-127.0.0.1}"
export MAX_UPLOAD_MB="${MAX_UPLOAD_MB:-1024}"
export MAX_FILES="${MAX_FILES:-100}"
export MAX_CONCURRENT_JOBS="${MAX_CONCURRENT_JOBS:-2}"
export LOG_RETENTION_DAYS="${LOG_RETENTION_DAYS:-90}"
[[ -n "${LOG_DIR:-}" ]] && export LOG_DIR

# 1. Ambiente virtual
if [[ ! -x "$PY" ]]; then
  command -v python3 >/dev/null || { echo "python3 não encontrado." >&2; exit 1; }
  python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' \
    || { echo "É necessário Python 3.10 ou superior." >&2; exit 1; }
  echo "Criando ambiente virtual em .venv..."
  python3 -m venv "$VENV"
fi

# 2. Dependências (só reinstala quando o requirements.txt muda)
HASH="$(sha256sum "$REQ" 2>/dev/null || shasum -a 256 "$REQ")"
HASH="${HASH%% *}"
if [[ ! -f "$STAMP" || "$(cat "$STAMP")" != "$HASH" ]]; then
  echo "Instalando dependências..."
  "$PY" -m pip install --disable-pip-version-check -q -r "$REQ"
  printf '%s' "$HASH" > "$STAMP"
fi

# 3. Servidor (um processo: limite de tarefas e rotação do log são por processo)
ARGS=(-m uvicorn app.main:app --host "$HOST" --port "$PORT"
      --proxy-headers --forwarded-allow-ips "$FORWARDED_ALLOW_IPS")
[[ "${1:-}" == "--dev" ]] && ARGS+=(--reload --reload-dir "$ROOT/app")

echo
echo "Compressor de Arquivos"
echo "  Endereço:      http://$([[ $HOST == 0.0.0.0 ]] && echo localhost || echo "$HOST"):$PORT"
echo "  Limite upload: $MAX_UPLOAD_MB MB / $MAX_FILES arquivos"
echo "  Log:           ${LOG_DIR:-$ROOT/logs}/uploads.jsonl"
echo "  Parar:         Ctrl+C"
echo

cd "$ROOT"
exec "$PY" "${ARGS[@]}"
