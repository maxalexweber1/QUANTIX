"""Deploy the parametrised order_mint.ak minting policy as a reference script on Preprod.

Usage:
    python scripts/deploy-order-mint-refscript.py <supplier_letter>
    # e.g. python scripts/deploy-order-mint-refscript.py a

Reads the unapplied blueprint at contracts/aiken/plutus.json (validator
"order_mint.order_mint.mint"), applies 5 params in sequence via `aiken
blueprint apply`:
    1) inventory_policy_id (ByteArray 28)
    2) inventory_asset_name (ByteArray var)
    3) price_policy_id (ByteArray 28)
    4) price_asset_name (ByteArray var)
    5) supplier_payment_hash (ByteArray 28)

Then wraps the resulting CBOR as PlutusV3Script, deploys as a reference
script on a UTxO at the funder's base address, and prints:
    - applied policy-id (= script hash)
    - ref-script tx hash + output index

Params come from apps/cap/db/data/c3.supply-Suppliers.csv (per supplier row).
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
from pathlib import Path

from pycardano import (
    Address,
    BlockFrostChainContext,
    ExtendedSigningKey,
    ExtendedVerificationKey,
    HDWallet,
    Network,
    PlutusV3Script,
    TransactionBuilder,
    TransactionOutput,
    Value,
    min_lovelace,
    plutus_script_hash,
)

ROOT = Path(__file__).resolve().parents[1]
AIKEN_DIR = ROOT / "contracts" / "aiken"
UNAPPLIED_BLUEPRINT = AIKEN_DIR / "plutus.json"
TMP_DIR = AIKEN_DIR / "build" / "params-mint"
SUPPLIERS_CSV = ROOT / "apps" / "cap" / "db" / "data" / "c3.supply-Suppliers.csv"
FUNDER_MNEMONIC_PATH = ROOT / "secrets" / "funder" / "funder.mnemonic"
VALIDATOR_MODULE = "order_mint"
VALIDATOR_NAME = "order_mint"

BLOCKFROST_KEY = os.environ.get("BLOCKFROST_API_KEY") or os.environ.get("BLOCKFROST_PROJECT_ID")
if not BLOCKFROST_KEY:
    raise SystemExit(
        "BLOCKFROST_API_KEY (or BLOCKFROST_PROJECT_ID) env var is required — "
        "source .env or export it before running."
    )

SUPPLIER_IDS = {
    "a": "a0000000-0000-4000-8000-000000000001",
    "b": "a0000000-0000-4000-8000-000000000002",
    "c": "a0000000-0000-4000-8000-000000000003",
}


def read_supplier_row(letter: str) -> dict[str, str]:
    supplier_id = SUPPLIER_IDS[letter]
    with SUPPLIERS_CSV.open(encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter=";")
        for row in reader:
            if row["ID"] == supplier_id:
                return row
    raise SystemExit(f"error: supplier '{letter}' not found in {SUPPLIERS_CSV}")


def payment_vkh_from_bech32(bech32: str) -> str:
    """Extract the 28-byte payment key hash (hex) from a Shelley enterprise
    or base bech32 address."""
    addr = Address.from_primitive(bech32)
    return bytes(addr.payment_part).hex()


def encode_bytes_as_plutus_data_cbor(b: bytes) -> str:
    """Encode a ByteArray as PlutusData CBOR hex (CBOR major type 2)."""
    n = len(b)
    if n <= 23:
        header = bytes([0x40 | n])
    elif n <= 0xFF:
        header = bytes([0x58, n])
    elif n <= 0xFFFF:
        header = bytes([0x59, n >> 8, n & 0xFF])
    else:
        raise ValueError(f"ByteArray too long: {n} bytes")
    return (header + b).hex()


def run_aiken_apply(in_blueprint: Path, out_blueprint: Path, param_cbor_hex: str) -> None:
    cmd = [
        "aiken", "blueprint", "apply",
        "-i", str(in_blueprint),
        "-o", str(out_blueprint),
        "-m", VALIDATOR_MODULE,
        "-v", VALIDATOR_NAME,
        param_cbor_hex,
    ]
    res = subprocess.run(cmd, cwd=AIKEN_DIR, capture_output=True, text=True)
    if res.returncode != 0:
        print(res.stdout, file=sys.stderr)
        print(res.stderr, file=sys.stderr)
        raise SystemExit(f"aiken blueprint apply failed for param {param_cbor_hex[:20]}…")


def apply_all_params(params: list[bytes]) -> str:
    """Chain 5 aiken blueprint apply calls, return final compiledCode hex."""
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    current_in = UNAPPLIED_BLUEPRINT
    for i, param_bytes in enumerate(params, start=1):
        current_out = TMP_DIR / f"step{i}.json"
        run_aiken_apply(current_in, current_out, encode_bytes_as_plutus_data_cbor(param_bytes))
        current_in = current_out

    bp = json.loads(current_in.read_text())
    mint = next(v for v in bp["validators"] if v["title"].endswith(".mint"))
    return mint["compiledCode"]


def derive_funder_base() -> tuple[Address, ExtendedSigningKey]:
    mnemonic = FUNDER_MNEMONIC_PATH.read_text().strip()
    hd = HDWallet.from_mnemonic(mnemonic)
    pay_hd = hd.derive_from_path("m/1852'/1815'/0'/0/0")
    stk_hd = hd.derive_from_path("m/1852'/1815'/0'/2/0")
    pay_sk = ExtendedSigningKey.from_hdwallet(pay_hd)
    pay_vkh = ExtendedVerificationKey.from_primitive(
        pay_hd.public_key + pay_hd.chain_code
    ).to_non_extended().hash()
    stk_vkh = ExtendedVerificationKey.from_primitive(
        stk_hd.public_key + stk_hd.chain_code
    ).to_non_extended().hash()
    return Address(pay_vkh, stk_vkh, network=Network.TESTNET), pay_sk


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("supplier", choices=["a", "b", "c"], help="Supplier letter")
    args = ap.parse_args()

    row = read_supplier_row(args.supplier)
    inv_policy = bytes.fromhex(row["inventoryOraclePolicyId"])
    inv_asset = row["inventoryOracleAssetName"].encode("utf-8")
    price_policy = bytes.fromhex(row["priceOraclePolicyId"])
    price_asset = row["priceOracleAssetName"].encode("utf-8")
    supplier_vkh = bytes.fromhex(payment_vkh_from_bech32(row["paymentAddress"]))

    print(f"supplier     : {args.supplier.upper()} — {row['name']}")
    print(f"inv_policy   : {inv_policy.hex()}")
    print(f"inv_asset    : {inv_asset.decode()!r}")
    print(f"price_policy : {price_policy.hex()}")
    print(f"price_asset  : {price_asset.decode()!r}")
    print(f"supplier_vkh : {supplier_vkh.hex()}")
    print()

    print("Applying 5 params via `aiken blueprint apply`…")
    cbor_hex = apply_all_params([inv_policy, inv_asset, price_policy, price_asset, supplier_vkh])

    script = PlutusV3Script(bytes.fromhex(cbor_hex))
    policy_id = plutus_script_hash(script)
    print(f"applied mint policy-id: {policy_id}")
    print(f"cbor size             : {len(cbor_hex) // 2} bytes")
    print()

    funder_addr, pay_sk = derive_funder_base()
    print(f"funder        : {funder_addr}")

    ctx = BlockFrostChainContext(BLOCKFROST_KEY, base_url="https://cardano-preprod.blockfrost.io/api")

    builder = TransactionBuilder(ctx)
    builder.add_input_address(funder_addr)
    out = TransactionOutput(address=funder_addr, amount=Value(1), script=script)
    raw_min = min_lovelace(ctx, out)
    out.amount = Value(raw_min + 500_000)
    print(f"min-ada       : {out.amount.coin / 1_000_000} tADA")
    builder.add_output(out)

    signed = builder.build_and_sign([pay_sk], change_address=funder_addr)
    tx_id = signed.transaction_body.hash().hex()
    print(f"\nsubmitting tx : {tx_id}")
    ctx.submit_tx(signed)
    print(f"submitted     : https://preprod.cardanoscan.io/transaction/{tx_id}")
    print()
    print("=== .env entries (paste these) ===")
    sl = args.supplier.upper()
    print(f"ORDER_MINT_POLICY_SUPPLIER_{sl}={policy_id}")
    print(f"ORDER_MINT_REFSCRIPT_TX_HASH_SUPPLIER_{sl}={tx_id}")
    print(f"ORDER_MINT_REFSCRIPT_OUTPUT_INDEX_SUPPLIER_{sl}=0")


if __name__ == "__main__":
    main()
