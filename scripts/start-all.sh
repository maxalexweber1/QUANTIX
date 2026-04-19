#!/usr/bin/env bash
# Boot QUANTIX local stack for the Buy-flow demo:
#   1) charli3-bridge  (FastAPI on :8000) — reads AggState UTxOs via Blockfrost
#   2) CAP app         (cds watch on :4004) — serves /quantix/webapp + OrdersService
#
# Oracle nodes (node-a-1/2/3) are NOT started here — they're only needed for
# the "Refresh Feed" button which pushes a new on-chain ODV round. Without
# them the UI still reads the existing feeds just fine. For the full stack
# see `docker compose up` (requires MNEMONIC_A_1/2/3 in .env).
#
# Stop with Ctrl+C — both processes are cleanly terminated.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="${REPO_ROOT}/logs"
mkdir -p "${LOG_DIR}"

# ── Resolve env ────────────────────────────────────────────────────
if [ -f "${REPO_ROOT}/.env" ]; then
  set -a; . "${REPO_ROOT}/.env"; set +a
fi

if [ -z "${BLOCKFROST_API_KEY:-}" ] && [ -n "${BLOCKFROST_PROJECT_ID:-}" ]; then
  export BLOCKFROST_API_KEY="${BLOCKFROST_PROJECT_ID}"
fi
if [ -z "${BLOCKFROST_API_KEY:-}" ]; then
  echo "error: BLOCKFROST_API_KEY (or BLOCKFROST_PROJECT_ID) not set in .env" >&2
  exit 1
fi
export BLOCKFROST_API_KEY
export BLOCKFROST_PROJECT_ID="${BLOCKFROST_API_KEY}"
export NETWORK="${NETWORK:-preprod}"
export BRIDGE_URL="${BRIDGE_URL:-http://localhost:8000}"

echo "═══════════════════════════════════════════════════════════════"
echo " QUANTIX local stack"
echo "   network   : ${NETWORK}"
echo "   bridge    : ${BRIDGE_URL}"
echo "   cap       : http://localhost:4004"
echo "   webapp    : http://localhost:4004/quantix/webapp/"
echo "   logs      : ${LOG_DIR}/{bridge,cap}.log"
echo "═══════════════════════════════════════════════════════════════"

# ── Child process lifecycle ────────────────────────────────────────
PIDS=()

cleanup() {
  echo ""
  echo "→ stopping children…"
  for pid in "${PIDS[@]}"; do
    if kill -0 "${pid}" 2>/dev/null; then
      kill "${pid}" 2>/dev/null || true
    fi
  done
  wait 2>/dev/null || true
  echo "  done."
}
trap cleanup EXIT INT TERM

# ── 1. Bridge ──────────────────────────────────────────────────────
BRIDGE_DIR="${REPO_ROOT}/services/charli3-bridge"
BRIDGE_UVICORN="${BRIDGE_DIR}/.venv/Scripts/uvicorn.exe"
if [ ! -x "${BRIDGE_UVICORN}" ]; then
  BRIDGE_UVICORN="${BRIDGE_DIR}/.venv/bin/uvicorn"
fi
if [ ! -x "${BRIDGE_UVICORN}" ]; then
  echo "error: bridge venv not found. Run 'python -m venv services/charli3-bridge/.venv && pip install -e services/charli3-bridge'" >&2
  exit 1
fi

echo "→ starting bridge…"
(
  cd "${BRIDGE_DIR}"
  "${BRIDGE_UVICORN}" charli3_bridge.main:app \
    --host 0.0.0.0 --port "${BRIDGE_PORT:-8000}" --reload \
    >"${LOG_DIR}/bridge.log" 2>&1
) &
PIDS+=($!)

# Wait a bit for bridge to be reachable.
# Wait for "Application startup complete" in the log. curl checks to
# localhost from WSL are unreliable (Windows-side service, loopback gap).
for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
  if grep -q "Application startup complete" "${LOG_DIR}/bridge.log" 2>/dev/null; then
    echo "  bridge up"
    break
  fi
  sleep 1
  if [ "${i}" = "15" ]; then
    echo "  warn: bridge didn't reach startup-complete in 15s — check ${LOG_DIR}/bridge.log"
  fi
done

# ── 2. CAP — ensure db.sqlite exists (re-deploy only when missing or --reset-db) ─
DB_FILE="${REPO_ROOT}/apps/cap/db.sqlite"
if [ ! -f "${DB_FILE}" ] || [ "${1:-}" = "--reset-db" ]; then
  echo "→ deploying CAP db schema…"
  (
    cd "${REPO_ROOT}/apps/cap"
    node scripts/deploy-db.js >"${LOG_DIR}/deploy-db.log" 2>&1
  ) && echo "  db ready" || echo "  warn: deploy-db failed — check ${LOG_DIR}/deploy-db.log"
else
  echo "→ db.sqlite present — skipping deploy (pass --reset-db to force)"
fi

echo "→ starting CAP (cds serve)…"
CDS_BIN="$(command -v cds || true)"
if [ -z "${CDS_BIN}" ]; then
  echo "error: 'cds' not on PATH. Install with: npm i -g @sap/cds-dk" >&2
  exit 1
fi
(
  cd "${REPO_ROOT}/apps/cap"
  "${CDS_BIN}" serve >"${LOG_DIR}/cap.log" 2>&1
) &
PIDS+=($!)

# CAP prints "server listening on" once bound. ODATANO plugin init
# (Blockfrost protocol params + indexer warmup) can take 30-60s on first
# run, so we wait up to 90.
for i in $(seq 1 90); do
  if grep -qE "server listening on|Listening on" "${LOG_DIR}/cap.log" 2>/dev/null; then
    echo "  cap up (boot took ${i}s)"
    break
  fi
  sleep 1
  if [ "${i}" = "90" ]; then
    echo "  warn: CAP didn't reach 'server listening' in 90s — check ${LOG_DIR}/cap.log"
  fi
done

echo ""
echo "→ all up. Open http://localhost:4004/quantix/webapp/"
echo "  Press Ctrl+C to stop."
echo ""

# Keep the script alive until one of the children dies (or Ctrl+C).
wait -n 2>/dev/null || wait
