#!/usr/bin/env bash
# 默认小生产：127.0.0.1、无 --reload；传 --dev 为开发（0.0.0.0 + --reload）
# 参数由 scripts/run_api.py + backend_uvicorn_spec.py 统一生成
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PYTHONPATH="$ROOT"
mkdir -p "$ROOT/logs"
TS="$(date +%Y%m%d-%H%M%S)"
LOG="$ROOT/logs/api-${TS}.log"
cd "$ROOT"
PORT="${PORT:-8000}"
RUN=(python "$ROOT/scripts/run_api.py" --port "$PORT")
if [[ "${1:-}" == "--dev" ]]; then
  RUN+=(--dev)
  echo "Dev | log: $LOG"
else
  echo "Small prod | log: $LOG"
fi
"${RUN[@]}" 2>&1 | tee -a "$LOG"
