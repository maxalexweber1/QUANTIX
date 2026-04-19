#!/usr/bin/env bash
# Supplier-A oracle deploy wrapper.
#
# Handles env-var setup + venv activation so you can focus on the actual
# three deploy steps. Reads funder mnemonic from secrets/funder/funder.mnemonic,
# Blockfrost project id from the BLOCKFROST_PROJECT_ID env var (or passed
# via --blockfrost=<id>).
#
# Usage:
#   scripts/deploy-supplier-a.sh platform     # Step 1: mint platform NFT
#   scripts/deploy-supplier-a.sh ref-script   # Step 2: deploy reference script
#   scripts/deploy-supplier-a.sh oracle       # Step 3: mint oracle NFTs + AggStates
#   scripts/deploy-supplier-a.sh help         # charli3 --help
#   scripts/deploy-supplier-a.sh raw <cmd...> # any other charli3 command
#
# If BLOCKFROST_PROJECT_ID isn't exported in the shell, the script will
# try to read it from .env in the repo root.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SDK_DIR="${REPO_ROOT}/services/charli3-fork/charli3-pull-oracle-sdk"
CHARLI3="${SDK_DIR}/.venv/Scripts/charli3"
CONFIG="${REPO_ROOT}/services/charli3-fork/configs/deploy-supplier-a.yaml"

# ── Resolve BLOCKFROST_PROJECT_ID ─────────────────────────────────
# Accept either BLOCKFROST_PROJECT_ID (SDK's expected name) or
# BLOCKFROST_API_KEY (existing .env convention in this repo). Falls back
# to grepping .env for either.
if [ -z "${BLOCKFROST_PROJECT_ID:-}" ] && [ -n "${BLOCKFROST_API_KEY:-}" ]; then
  BLOCKFROST_PROJECT_ID="${BLOCKFROST_API_KEY}"
fi
if [ -z "${BLOCKFROST_PROJECT_ID:-}" ] && [ -f "${REPO_ROOT}/.env" ]; then
  BLOCKFROST_PROJECT_ID="$(
    grep -E '^BLOCKFROST_(PROJECT_ID|API_KEY)=' "${REPO_ROOT}/.env" \
      | head -1 | cut -d= -f2- | tr -d '"'
  )"
fi

if [ -z "${BLOCKFROST_PROJECT_ID:-}" ] || [ "${BLOCKFROST_PROJECT_ID}" = "preprod_REPLACE_ME" ]; then
  echo "error: BLOCKFROST_PROJECT_ID is not set (checked shell env and .env)." >&2
  echo "       export it or add to .env, e.g." >&2
  echo "       export BLOCKFROST_PROJECT_ID=preprod..." >&2
  exit 1
fi
export BLOCKFROST_PROJECT_ID

# ── Resolve FUNDER_MNEMONIC ────────────────────────────────────────
FUNDER_FILE="${REPO_ROOT}/secrets/funder/funder.mnemonic"
if [ ! -f "${FUNDER_FILE}" ]; then
  echo "error: funder mnemonic not found at ${FUNDER_FILE}" >&2
  echo "       run scripts/gen-wallets.py first" >&2
  exit 1
fi
FUNDER_MNEMONIC="$(tr -d '\n\r' < "${FUNDER_FILE}")"
export FUNDER_MNEMONIC

# Windows default console codec (cp1252) chokes on the unicode chars
# the SDK uses for pretty-printing (⟳ etc). Force UTF-8.
export PYTHONIOENCODING=utf-8
export PYTHONUTF8=1

# ── Sanity-check the venv ──────────────────────────────────────────
if [ ! -x "${CHARLI3}" ]; then
  echo "error: charli3 CLI not installed at ${CHARLI3}" >&2
  echo "       cd ${SDK_DIR} && py -3.11 -m venv .venv && .venv/Scripts/pip install -e ." >&2
  exit 1
fi

# ── Dispatch ───────────────────────────────────────────────────────
cmd="${1:-help}"
shift || true

case "${cmd}" in
  platform)
    echo "=== Step 1: mint platform auth NFT ==="
    "${CHARLI3}" platform token mint --config "${CONFIG}" "$@"
    echo ""
    echo "=> paste the minted platform_auth_policy into"
    echo "   ${CONFIG}"
    echo "   under tokens.platform_auth_policy, then run:"
    echo "   $0 ref-script"
    ;;
  ref-script|reference-script|ref)
    echo "=== Step 2: create reference script ==="
    "${CHARLI3}" reference-script create --config "${CONFIG}" "$@"
    echo ""
    echo "=> paste reference_script.address + utxo_reference into ${CONFIG}, then:"
    echo "   $0 oracle"
    ;;
  oracle|deploy)
    echo "=== Step 3: deploy oracle (mint C3CS + C3RA + 2 distinct C3AS_*) ==="
    "${CHARLI3}" oracle deploy --config "${CONFIG}" "$@"
    echo ""
    echo "=> record resulting policy_id + oracle_address in:"
    echo "   - .env  (ORACLE_ADDRESSES)"
    echo "   - apps/cap/db/data/c3.supply-Suppliers.csv  (inventoryOraclePolicyId / priceOraclePolicyId)"
    ;;
  help|--help|-h|"")
    "${CHARLI3}" --help
    echo ""
    echo "Wrapper sub-commands:"
    echo "  $0 platform      # Step 1: mint platform auth NFT"
    echo "  $0 ref-script    # Step 2: deploy reference script"
    echo "  $0 oracle        # Step 3: deploy oracle + AggStates"
    echo "  $0 raw <args>    # forward any charli3 command verbatim"
    ;;
  raw)
    "${CHARLI3}" "$@"
    ;;
  *)
    echo "error: unknown sub-command '${cmd}'" >&2
    echo "try: $0 help" >&2
    exit 2
    ;;
esac
