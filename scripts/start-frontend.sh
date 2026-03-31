#!/usr/bin/env bash
# Next.js 开发模式；日志写入 logs/frontend-*.log
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
mkdir -p "$ROOT/logs"
TS="$(date +%Y%m%d-%H%M%S)"
LOG="$ROOT/logs/frontend-${TS}.log"
cd "$ROOT/frontend"
echo "frontend npm run dev | log: $LOG"
npm run dev 2>&1 | tee -a "$LOG"
