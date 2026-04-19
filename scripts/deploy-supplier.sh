#!/usr/bin/env bash
# Generic per-supplier oracle deploy wrapper.
#
# Usage:
#   scripts/deploy-supplier.sh <a|b|c> <platform|ref-script|oracle|help>
#
# Examples:
#   scripts/deploy-supplier.sh b platform
#   scripts/deploy-supplier.sh b ref-script
#   scripts/deploy-supplier.sh b oracle
#   scripts/deploy-supplier.sh c platform
#
# Reads funder mnemonic from secrets/funder/funder.mnemonic, Blockfrost
# project id from $BLOCKFROST_PROJECT_ID (or BLOCKFROST_API_KEY, or .env).
# The per-supplier config at services/charli3-fork/configs/deploy-supplier-<letter>.yaml
# is edited in-place between steps to carry platform_auth_policy and
# reference_script details.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SDK_DIR="${REPO_ROOT}/services/charli3-fork/charli3-pull-oracle-sdk"
CHARLI3="${SDK_DIR}/.venv/Scripts/charli3"

if [ $# -lt 1 ]; then
  echo "usage: $0 <a|b|c> <platform|ref-script|oracle|help>" >&2
  exit 2
fi
supplier="$1"
cmd="${2:-help}"
shift 2 || shift 1 || true

case "${supplier}" in
  a|b|c) ;;
  *)
    echo "error: unknown supplier '${supplier}' (expected a, b, or c)" >&2
    exit 2
    ;;
esac

CONFIG="${REPO_ROOT}/services/charli3-fork/configs/deploy-supplier-${supplier}.yaml"
if [ ! -f "${CONFIG}" ]; then
  echo "error: config not found: ${CONFIG}" >&2
  exit 2
fi

# ── Resolve env ────────────────────────────────────────────────────
if [ -z "${BLOCKFROST_PROJECT_ID:-}" ] && [ -n "${BLOCKFROST_API_KEY:-}" ]; then
  BLOCKFROST_PROJECT_ID="${BLOCKFROST_API_KEY}"
fi
if [ -z "${BLOCKFROST_PROJECT_ID:-}" ] && [ -f "${REPO_ROOT}/.env" ]; then
  BLOCKFROST_PROJECT_ID="$(
    grep -E '^BLOCKFROST_(PROJECT_ID|API_KEY)=' "${REPO_ROOT}/.env" \
      | head -1 | cut -d= -f2- | tr -d '"'
  )"
fi
if [ -z "${BLOCKFROST_PROJECT_ID:-}" ]; then
  echo "error: BLOCKFROST_PROJECT_ID not set" >&2
  exit 1
fi
export BLOCKFROST_PROJECT_ID

FUNDER_FILE="${REPO_ROOT}/secrets/funder/funder.mnemonic"
FUNDER_MNEMONIC="$(tr -d '\n\r' < "${FUNDER_FILE}")"
export FUNDER_MNEMONIC

export PYTHONIOENCODING=utf-8
export PYTHONUTF8=1

# ── Dispatch ───────────────────────────────────────────────────────
case "${cmd}" in
  platform)
    echo "=== Supplier-${supplier} Step 1: mint platform auth NFT ==="
    "${CHARLI3}" platform token mint --config "${CONFIG}" "$@"
    echo ""
    echo "=> paste platform_auth_policy + platform_addr into ${CONFIG}"
    ;;
  ref-script|reference-script|ref)
    echo "=== Supplier-${supplier} Step 2: create reference script ==="
    "${CHARLI3}" reference-script create --config "${CONFIG}" "$@"
    echo ""
    echo "=> paste reference_script.{address,utxo_reference} into ${CONFIG}"
    ;;
  oracle|deploy)
    echo "=== Supplier-${supplier} Step 3: deploy oracle (2 distinct AggStates) ==="
    "${CHARLI3}" oracle deploy --config "${CONFIG}" "$@"
    echo ""
    echo "=> record policy_id + oracle_address in .env (ORACLE_ADDRESSES)"
    echo "   and apps/cap/db/data/c3.supply-Suppliers.csv (supplier ${supplier})"
    ;;
  help|--help|-h|"")
    echo "Supplier-${supplier} deploy subcommands:"
    echo "  $0 ${supplier} platform     # Step 1: mint platform NFT"
    echo "  $0 ${supplier} ref-script   # Step 2: deploy reference script"
    echo "  $0 ${supplier} oracle       # Step 3: deploy oracle + AggStates"
    ;;
  *)
    echo "error: unknown sub-command '${cmd}'" >&2
    exit 2
    ;;
esac
