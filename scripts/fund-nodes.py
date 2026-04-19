"""Fund or top up the 9 oracle-node coordinator-payment addresses.

Default mode: TOP UP — only send to addresses whose biggest pure-ADA UTxO
is below the `MIN_LARGEST_UTXO` threshold. Plutus-script submits need a
single UTxO >= the collateral threshold (~4.5 tADA on Preprod), so a
wallet fragmented into many small UTxOs will fail even if the total
balance is fine.

Flags:
    --initial       send to every address regardless of balance
                    (coordinators 30 tADA, peers 10 tADA).
    --threshold N   override MIN_LARGEST_UTXO (default 10 tADA).
    --topup N       lovelace (or N tADA when given as a float)
                    to send when a wallet needs topping up (default 30 tADA).

Reads the funder mnemonic from secrets/funder/funder.mnemonic, derives
the base address, picks UTxOs, builds + signs + submits via Blockfrost.

Usage:
    python scripts/fund-nodes.py                    # smart topup
    python scripts/fund-nodes.py --initial          # first-time full fund
    python scripts/fund-nodes.py --threshold 5      # only if largest UTxO < 5 tADA
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from pycardano import (
    Address,
    BlockFrostChainContext,
    ExtendedSigningKey,
    HDWallet,
    Network,
    PaymentVerificationKey,
    TransactionBuilder,
    TransactionOutput,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
NETWORK = Network.TESTNET
BLOCKFROST_BASE_URL = "https://cardano-preprod.blockfrost.io/api"

INITIAL_COORDINATOR_TADA = 1000
INITIAL_PEER_TADA = 100
DEFAULT_TOPUP_TADA = 100
DEFAULT_THRESHOLD_TADA = 20


def _largest_utxo_lovelace(context: BlockFrostChainContext, addr: str) -> int:
    try:
        utxos = context.utxos(addr)
    except Exception:
        return 0
    if not utxos:
        return 0
    return max(u.output.amount.coin for u in utxos)


def main() -> int:
    parser = argparse.ArgumentParser(description="Fund / top up oracle node wallets")
    parser.add_argument("--initial", action="store_true",
                        help="Send to every node (ignores current balance)")
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD_TADA,
                        help="Top up if largest UTxO < this many tADA")
    parser.add_argument("--topup", type=float, default=DEFAULT_TOPUP_TADA,
                        help="tADA to send per top-up")
    args = parser.parse_args()

    project_id = os.environ.get("BLOCKFROST_PROJECT_ID") or os.environ.get(
        "BLOCKFROST_API_KEY"
    )
    if not project_id:
        print("error: BLOCKFROST_PROJECT_ID not set", file=sys.stderr)
        return 1

    funder_mnemonic = (REPO_ROOT / "secrets/funder/funder.mnemonic").read_text().strip()
    hdw = HDWallet.from_mnemonic(funder_mnemonic)
    pay_hd = hdw.derive_from_path("m/1852'/1815'/0'/0/0")
    stake_hd = hdw.derive_from_path("m/1852'/1815'/0'/2/0")
    pay_sk = ExtendedSigningKey.from_hdwallet(pay_hd)
    pay_vk = PaymentVerificationKey.from_primitive(pay_hd.public_key)
    stake_vk = PaymentVerificationKey.from_primitive(stake_hd.public_key)
    funder_addr = Address(
        payment_part=pay_vk.hash(),
        staking_part=stake_vk.hash(),
        network=NETWORK,
    )
    print(f"Funder: {funder_addr}")

    context = BlockFrostChainContext(project_id=project_id, base_url=BLOCKFROST_BASE_URL)
    utxos = context.utxos(str(funder_addr))
    bal = sum(u.output.amount.coin for u in utxos)
    print(f"Funder balance: {bal / 1_000_000:_.2f} tADA ({len(utxos)} UTxOs)")

    threshold_lov = int(args.threshold * 1_000_000)
    topup_lov = int(args.topup * 1_000_000)

    recipients: list[tuple[str, int, str]] = []  # (addr, lovelace, label)
    for supplier in ("a", "b", "c"):
        for n in (1, 2, 3):
            addr_path = REPO_ROOT / f"secrets/supplier-{supplier}/node-{n}.payment.addr"
            addr_str = addr_path.read_text().strip()
            label = f"supplier-{supplier} node-{n}"
            # Dual-coordinator setup: node-1 = inventory coord, node-2 = price coord.
            # Both need coordinator-grade funding; only node-3 stays peer-only.
            is_coordinator = n in (1, 2)
            if args.initial:
                lov = (INITIAL_COORDINATOR_TADA if is_coordinator else INITIAL_PEER_TADA) * 1_000_000
                recipients.append((addr_str, lov, label))
                print(f"  {label}: initial {lov / 1_000_000:.0f} tADA -> {addr_str[:20]}...")
                continue
            largest = _largest_utxo_lovelace(context, addr_str)
            if largest < threshold_lov:
                recipients.append((addr_str, topup_lov, label))
                print(f"  {label}: largest UTxO {largest / 1_000_000:.2f} < {args.threshold} "
                      f"-> topup {args.topup} tADA")
            else:
                print(f"  {label}: OK (largest UTxO {largest / 1_000_000:.2f} tADA)")

    if not recipients:
        print("\nNothing to do -- all wallets above threshold.")
        return 0

    total_lovelace = sum(r[1] for r in recipients)
    print(f"\nTotal output: {total_lovelace / 1_000_000} tADA across {len(recipients)} addr(s)")
    if bal < total_lovelace + 2_000_000:
        print("error: insufficient funder balance", file=sys.stderr)
        return 1

    builder = TransactionBuilder(context)
    builder.add_input_address(funder_addr)
    for addr_str, lov, _label in recipients:
        builder.add_output(TransactionOutput(Address.from_primitive(addr_str), lov))

    signed = builder.build_and_sign([pay_sk], change_address=funder_addr)
    print(f"\nBuilt tx: {signed.id}")
    print(f"Fee: {signed.transaction_body.fee / 1_000_000:.6f} tADA")

    context.submit_tx(signed.to_cbor())
    print(f"Submitted: https://preprod.cardanoscan.io/transaction/{signed.id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
