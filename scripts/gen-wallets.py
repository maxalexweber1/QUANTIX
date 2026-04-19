"""Generate all wallets we need for the c3-supply Preprod demo.

Output layout under `secrets/`:

  secrets/
    funder/
      funder.mnemonic            # 24 words — fund this address from faucet
      funder.payment.skey        # ExtendedSigningKey (cardano-cli compat)
      funder.payment.vkey
      funder.addr                # bech32 preprod payment address
    supplier-a/
      node-1.mnemonic            # 24 words
      node-1.feed.skey           # derived m/4343'/1815'/0'/0/0
      node-1.feed.vkey
      node-1.feed.vkh            # hex of feed-VKH (for on-chain node registration)
      node-1.payment.skey        # derived m/1852'/1815'/0'/0/0
      node-1.payment.vkey
      node-1.payment.vkh
      node-1.payment.addr
      node-{2,3}.*               # same layout
    supplier-b/   node-{1..3}.*
    supplier-c/   node-{1..3}.*
    buyer/        buyer.*
    supplier-payments/
      supplier-{a,b,c}.addr      # bech32 recipient addresses for buyFromSupplier
      supplier-{a,b,c}.payment.skey
      supplier-{a,b,c}.payment.vkey

Note: the coordinator loads ALL *.skey files under COORDINATOR_KEYS_DIR
and signs with them. For docker-compose we'll mount
`./secrets/supplier-a/` as `/run/peer-skeys` on node-a-1 so all three
feed skeys are available when /odv/aggregate runs.

Re-running is safe: existing wallets are skipped (no overwrite). Delete
the per-entity dir to force re-generation.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from pycardano import (
    Address,
    ExtendedSigningKey,
    HDWallet,
    Network,
    PaymentVerificationKey,
    VerificationKey,
)

ROOT = Path(__file__).resolve().parent.parent
SECRETS = ROOT / "secrets"
NETWORK = Network.TESTNET  # Preprod

# Charli3 feed-key derivation path (see node/config/setup.py:203).
FEED_PATH = "m/4343'/1815'/0'/0/0"
# Standard Cardano payment derivation path (m/1852'/1815'/0'/0/0).
PAYMENT_PATH = "m/1852'/1815'/0'/0/0"


def _write_if_missing(path: Path, write_fn) -> bool:
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    write_fn(path)
    return True


def _save_skey(path: Path, skey: ExtendedSigningKey, description: str) -> None:
    # pycardano's .save() produces the cardano-cli compatible JSON envelope.
    # Delete any pre-existing file since save() refuses to overwrite.
    if path.exists():
        path.unlink()
    skey.save(str(path))
    # Re-open to tweak description — helpful when reading via cardano-cli.
    data = json.loads(path.read_text())
    data["description"] = description
    path.write_text(json.dumps(data, indent=2))


def _save_vkey(path: Path, vkey, description: str) -> None:
    if path.exists():
        path.unlink()
    vkey.save(str(path))
    data = json.loads(path.read_text())
    data["description"] = description
    path.write_text(json.dumps(data, indent=2))


def _derive_node(out_dir: Path, label: str, mnemonic: str) -> None:
    """Save mnemonic + feed/payment keys + address for one node."""
    out_dir.mkdir(parents=True, exist_ok=True)

    mnemo_path = out_dir / f"{label}.mnemonic"
    _write_if_missing(mnemo_path, lambda p: p.write_text(mnemonic + "\n"))

    hdw = HDWallet.from_mnemonic(mnemonic)

    # Feed keys
    feed_hd = hdw.derive_from_path(FEED_PATH)
    feed_sk = ExtendedSigningKey.from_hdwallet(feed_hd)
    feed_vk = VerificationKey.from_primitive(feed_hd.public_key[:32])
    _save_skey(
        out_dir / f"{label}.feed.skey",
        feed_sk,
        "Charli3 node feed signing key (m/4343'/1815'/0'/0/0)",
    )
    _save_vkey(
        out_dir / f"{label}.feed.vkey",
        feed_vk,
        "Charli3 node feed verification key",
    )
    (out_dir / f"{label}.feed.vkh").write_text(feed_vk.hash().payload.hex() + "\n")

    # Payment keys + address
    pay_hd = hdw.derive_from_path(PAYMENT_PATH)
    pay_sk = ExtendedSigningKey.from_hdwallet(pay_hd)
    pay_vk = PaymentVerificationKey.from_primitive(pay_hd.public_key[:32])
    _save_skey(
        out_dir / f"{label}.payment.skey",
        pay_sk,
        "Cardano payment signing key (m/1852'/1815'/0'/0/0)",
    )
    _save_vkey(
        out_dir / f"{label}.payment.vkey",
        pay_vk,
        "Cardano payment verification key",
    )
    (out_dir / f"{label}.payment.vkh").write_text(pay_vk.hash().payload.hex() + "\n")
    addr = Address(payment_part=pay_vk.hash(), network=NETWORK)
    (out_dir / f"{label}.payment.addr").write_text(str(addr) + "\n")


def _derive_simple(out_dir: Path, label: str, mnemonic: str) -> None:
    """Save mnemonic + a single payment keypair + address (no feed key)."""
    out_dir.mkdir(parents=True, exist_ok=True)

    mnemo_path = out_dir / f"{label}.mnemonic"
    _write_if_missing(mnemo_path, lambda p: p.write_text(mnemonic + "\n"))

    hdw = HDWallet.from_mnemonic(mnemonic)
    pay_hd = hdw.derive_from_path(PAYMENT_PATH)
    pay_sk = ExtendedSigningKey.from_hdwallet(pay_hd)
    pay_vk = PaymentVerificationKey.from_primitive(pay_hd.public_key[:32])

    _save_skey(
        out_dir / f"{label}.payment.skey",
        pay_sk,
        f"{label} payment signing key",
    )
    _save_vkey(
        out_dir / f"{label}.payment.vkey",
        pay_vk,
        f"{label} payment verification key",
    )
    (out_dir / f"{label}.payment.vkh").write_text(
        pay_vk.hash().payload.hex() + "\n"
    )
    addr = Address(payment_part=pay_vk.hash(), network=NETWORK)
    (out_dir / f"{label}.addr").write_text(str(addr) + "\n")


def _fresh_mnemonic() -> str:
    # HDWallet.generate() produces a 24-word BIP39 mnemonic (256-bit entropy).
    return HDWallet.generate_mnemonic(strength=256)


def main() -> int:
    SECRETS.mkdir(exist_ok=True)

    wallets_created: list[str] = []
    wallets_kept: list[str] = []

    # Funder — pays tx fees for reference-script + oracle deploys.
    funder_dir = SECRETS / "funder"
    if not (funder_dir / "funder.mnemonic").exists():
        _derive_simple(funder_dir, "funder", _fresh_mnemonic())
        wallets_created.append("funder")
    else:
        wallets_kept.append("funder")

    # 9 node wallets (3 suppliers × 3 nodes).
    for supplier in ("a", "b", "c"):
        sup_dir = SECRETS / f"supplier-{supplier}"
        for node_num in (1, 2, 3):
            label = f"node-{node_num}"
            if (sup_dir / f"{label}.mnemonic").exists():
                wallets_kept.append(f"supplier-{supplier}/{label}")
                continue
            _derive_node(sup_dir, label, _fresh_mnemonic())
            wallets_created.append(f"supplier-{supplier}/{label}")

    # Supplier payment addresses — where buyFromSupplier pays. Using simple
    # payment keypairs (no stake) so the supplier dev team can hold the
    # skey or rotate later.
    pay_dir = SECRETS / "supplier-payments"
    for supplier in ("a", "b", "c"):
        label = f"supplier-{supplier}"
        if (pay_dir / f"{label}.mnemonic").exists():
            wallets_kept.append(f"supplier-payments/{label}")
            continue
        _derive_simple(pay_dir, label, _fresh_mnemonic())
        wallets_created.append(f"supplier-payments/{label}")

    # Buyer — optional. Typically you'd use a browser wallet (Eternl/Nami
    # via CIP-30) for the demo, but generate a local one too for headless
    # testing.
    buyer_dir = SECRETS / "buyer"
    if not (buyer_dir / "buyer.mnemonic").exists():
        _derive_simple(buyer_dir, "buyer", _fresh_mnemonic())
        wallets_created.append("buyer")
    else:
        wallets_kept.append("buyer")

    print()
    print(f"Created {len(wallets_created)} new wallet(s):")
    for w in wallets_created:
        print(f"  + {w}")
    if wallets_kept:
        print(f"Kept {len(wallets_kept)} existing wallet(s) unchanged:")
        for w in wallets_kept:
            print(f"  = {w}")

    # Print the funder address so user knows where to send faucet tADA.
    funder_addr_path = funder_dir / "funder.addr"
    if funder_addr_path.exists():
        print()
        print("Funder preprod address (fund from https://docs.cardano.org/cardano-testnets/tools/faucet):")
        print("  " + funder_addr_path.read_text().strip())

    # Summary: coordinator keys bundle for Supplier-A.
    sa = SECRETS / "supplier-a"
    feed_skeys = sorted(sa.glob("node-*.feed.skey"))
    print()
    print(f"Supplier-A feed skeys for COORDINATOR_KEYS_DIR ({len(feed_skeys)} files):")
    for p in feed_skeys:
        print(f"  {p.relative_to(ROOT)}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
