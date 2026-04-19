"""Run the first ODV aggregation round for Supplier-A (D-05 smoke test).

Bypasses the node HTTP layer: loads 3 Supplier-A node feed skeys locally,
constructs an AggregateMessage with a test value, and submits via the
forked OracleTransactionBuilder pointed at C3AS_inventory or C3AS_price.

The validator enforces:
  - Threshold (2-of-3) node signatures via extra_signatories
  - Platform NFT input
  - Aggregation-state datum conservation

Usage:
  scripts/odv-first-round.py inventory 10000   # grams
  scripts/odv-first-round.py price 1200        # lovelace per gram

Requires:
  - BLOCKFROST_PROJECT_ID (or BLOCKFROST_API_KEY) in env
  - secrets/funder/funder.mnemonic   (pays tx fees)
  - f"secrets/supplier-{supplier}/"[1:-1]/...node-{1,2,3}.feed.skey
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

from pycardano import (
    Address,
    BlockFrostChainContext,
    ExtendedSigningKey,
    HDWallet,
    Network,
    PaymentVerificationKey,
    ScriptHash,
    VerificationKey,
)

import charli3_offchain_core.oracle.aggregate.builder as odv_builder
from charli3_offchain_core.blockchain.chain_query import ChainQuery
from charli3_offchain_core.blockchain.transactions import TransactionManager

# Monkey-patch pycardano's evaluate_tx_cbor to dump the RAW Blockfrost
# response before raising. pycardano's parser collapses empty
# ScriptFailures into `Namespace()` which hides the real reason.
import json as _json
from pycardano.backend import blockfrost as _pbf

_orig_evaluate = _pbf.BlockFrostChainContext.evaluate_tx_cbor


# Optional: route evaluate_tx_cbor through an Ogmios v6 JSON-RPC endpoint
# to surface real ScriptFailure reasons (Blockfrost's evaluate-tx collapses
# empty-detail failures into Namespace()). Set OGMIOS_URL in the env to
# enable; leave unset to use Blockfrost's evaluate as usual.
OGMIOS_URL = os.environ.get("OGMIOS_URL", "").strip()


def _ogmios_evaluate(self, cbor):
    """Route evaluate_tx_cbor through Ogmios v6 (HTTP JSON-RPC) to bypass
    Blockfrost's lossy empty-ScriptFailures error. Returns the dict shape
    pycardano expects downstream.
    """
    import urllib.request
    from pycardano import ExecutionUnits

    if isinstance(cbor, bytes):
        cbor_hex = cbor.hex()
    else:
        cbor_hex = cbor

    payload = _json.dumps({
        "jsonrpc": "2.0",
        "method": "evaluateTransaction",
        "params": {"transaction": {"cbor": cbor_hex}},
        "id": "c3-supply-odv",
    }).encode("utf-8")

    req = urllib.request.Request(
        OGMIOS_URL.rstrip("/"),
        method="POST",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = _json.load(resp)
    except urllib.error.HTTPError as e:
        body = _json.loads(e.read().decode("utf-8", errors="replace"))

    print("\n=== Ogmios evaluate response ===")
    print(_json.dumps(body, indent=2)[:5000])
    print("=== end ogmios response ===\n")

    if "error" in body:
        # Surface the real error from the node.
        raise RuntimeError(f"Ogmios evaluate error: {body['error']}")

    # Convert Ogmios v6 response to pycardano's expected dict shape:
    #   {"spend:0": ExecutionUnits(mem, steps), ...}
    return_val = {}
    for entry in body.get("result", []):
        purpose = entry["validator"]["purpose"]  # "spend" / "mint" / ...
        index = entry["validator"]["index"]
        mem = entry["budget"]["memory"]
        cpu = entry["budget"]["cpu"]
        key = f"{purpose}:{index}"
        return_val[key] = ExecutionUnits(mem, cpu)
    return return_val


if OGMIOS_URL:
    _pbf.BlockFrostChainContext.evaluate_tx_cbor = _ogmios_evaluate
from charli3_offchain_core.cli.config.reference_script import (
    ReferenceScriptConfig,
    UtxoReference,
)
from charli3_offchain_core.models.oracle_redeemers import AggregateMessage

REPO_ROOT = Path(__file__).resolve().parent.parent

SUPPLIERS = {
    "a": {
        "policy_id": "78adae0debdf47708f49b5bae9b3ee5eae053ce7b86874d7947ec622",
        "script_addr": "addr_test1wqww3xgvvt09y826qq5xqj8267yvhaq7xprdeeyrhw7a6dq2fszvh",
        "ref_script_tx": "4a617918d0b468c80bf48aeffc80dce9c88037972462c37d62948d137ab4ec2b",
        "ref_script_idx": 0,
    },
    "b": {
        "policy_id": "7e3cc787cd71c68ecc08274cff9596be9e2951d106c29b901dbd081f",
        "script_addr": "addr_test1wpk8epwpmshm4d4s3p40p9vkjhmu8e549xvwyl2njjrzkjcthysxc",
        "ref_script_tx": "f6ce12386aab028825ab4deb12cc882b6a9bfb9c80da330756189be2c9f630bc",
        "ref_script_idx": 0,
    },
    "c": {
        "policy_id": "dc6ffa6e4ed0ae23697d5d33e4aa0f4ac020b5c78d94209510a37ed9",
        "script_addr": "addr_test1wp7jh3d2drrlexhmckgdjc70spsvuyemt8s77xkk0h59sds9n8kx3",
        "ref_script_tx": "f1488a103393c06bed140edc69c02050cca339cf6f1f12c776ec679f1c497660",
        "ref_script_idx": 0,
    },
}


async def main() -> int:
    # Args: <supplier-letter> <feed-name> <feed-value>
    #   scripts/odv-first-round.py a inventory 10000
    #   scripts/odv-first-round.py b price 850
    supplier = sys.argv[1] if len(sys.argv) > 1 else "a"
    feed_name = sys.argv[2] if len(sys.argv) > 2 else "inventory"
    feed_value = int(sys.argv[3]) if len(sys.argv) > 3 else 10000
    aggstate_asset = f"C3AS_{feed_name}"

    if supplier not in SUPPLIERS:
        print(f"error: unknown supplier '{supplier}' (expected a, b, or c)", file=sys.stderr)
        return 1
    cfg = SUPPLIERS[supplier]
    policy_id = cfg["policy_id"]
    script_addr = cfg["script_addr"]
    ref_script_addr = cfg["script_addr"]
    ref_script_tx = cfg["ref_script_tx"]
    ref_script_idx = cfg["ref_script_idx"]

    project_id = os.environ.get("BLOCKFROST_PROJECT_ID") or os.environ.get(
        "BLOCKFROST_API_KEY"
    )
    if not project_id:
        print("error: BLOCKFROST_PROJECT_ID not set", file=sys.stderr)
        return 1

    context = BlockFrostChainContext(
        project_id=project_id,
        base_url="https://cardano-preprod.blockfrost.io/api",
    )
    chain_query = ChainQuery(blockfrost_context=context)
    tx_manager = TransactionManager(chain_query)

    # Funder — pays tx fees.
    funder_mnemonic = (REPO_ROOT / "secrets/funder/funder.mnemonic").read_text().strip()
    f_hdw = HDWallet.from_mnemonic(funder_mnemonic)
    f_pay_hd = f_hdw.derive_from_path("m/1852'/1815'/0'/0/0")
    f_stake_hd = f_hdw.derive_from_path("m/1852'/1815'/0'/2/0")
    funder_pay_sk = ExtendedSigningKey.from_hdwallet(f_pay_hd)
    funder_pay_vk = PaymentVerificationKey.from_primitive(f_pay_hd.public_key[:32])
    funder_stake_vk = PaymentVerificationKey.from_primitive(f_stake_hd.public_key[:32])
    funder_addr = Address(
        payment_part=funder_pay_vk.hash(),
        staking_part=funder_stake_vk.hash(),
        network=Network.TESTNET,
    )

    # 3 Supplier-A node feed keys + produce signed OracleNodeMessages exactly
    # like the SDK integration test does. This ensures whatever subtle thing
    # the test path sets up (signature verification, canonical ordering) is
    # done the same way here.
    from charli3_offchain_core.models.message import (
        OracleNodeMessage,
        SignedOracleNodeMessage,
    )
    from charli3_offchain_core.oracle.utils import common as core_common

    node_skeys = []
    signed_msgs = []
    current_time_ms = int(time.time() * 1000)
    policy_bytes = bytes.fromhex(policy_id)
    # Slight variance to avoid all-equal feed values (just in case the
    # validator has an edge case with zero variance).
    feed_values = [feed_value - 10, feed_value, feed_value + 10]
    for n, fv in zip((1, 2, 3), feed_values):
        skey = ExtendedSigningKey.load(
            str(REPO_ROOT / f"secrets/supplier-{supplier}/node-{n}.feed.skey")
        )
        vkey = VerificationKey.from_primitive(
            HDWallet.from_mnemonic(
                (REPO_ROOT / f"secrets/supplier-{supplier}/node-{n}.mnemonic").read_text().strip()
            )
            .derive_from_path("m/4343'/1815'/0'/0/0")
            .public_key[:32]
        )
        node_skeys.append(skey)
        msg = OracleNodeMessage(
            feed=fv,
            timestamp=current_time_ms,
            oracle_nft_policy_id=policy_bytes,
        )
        sig = msg.sign(skey)
        signed = SignedOracleNodeMessage(
            message=msg, signature=sig, verification_key=vkey
        )
        signed.validate_signature()
        signed_msgs.append(signed)

    message = core_common.build_aggregate_message(signed_msgs)

    # Note: omit utxo_reference — the SDK's get_utxo_by_ref_kupo path
    # requires Kupo. We're on Blockfrost. Leaving it None triggers the
    # fallback that scans the script address for any UTxO with a script.
    ref_script = ReferenceScriptConfig(
        address=ref_script_addr,
        utxo_reference=None,
    )

    builder = odv_builder.OracleTransactionBuilder(
        tx_manager=tx_manager,
        script_address=Address.from_primitive(script_addr),
        policy_id=ScriptHash(bytes.fromhex(policy_id)),
        ref_script_config=ref_script,
        aggstate_asset_name=aggstate_asset,
    )

    # Pre-check: do the script-input UTxOs carry inline datums?
    # pycardano 0.17 + Blockfrost can silently drop inline datums which
    # breaks script-context construction (the empty ScriptFailures case).
    from charli3_offchain_core.oracle.utils import common, state_checks
    script_addr_obj = Address.from_primitive(script_addr)
    utxos = await common.get_script_utxos(script_addr_obj, tx_manager)
    print(f"\nScript-address UTxOs ({len(utxos)}):")
    for u in utxos:
        d = u.output.datum
        has_inline = d is not None and hasattr(d, 'cbor')
        print(f"  {u.input.transaction_id.payload.hex()[:16]}...#{u.input.index}  "
              f"datum={type(d).__name__ if d else None}  inline={has_inline}")

    print(f"\nBuilding ODV tx: feed='{feed_name}' asset='{aggstate_asset}' "
          f"value={feed_value} nodes={len(signed_msgs)}")
    result = await builder.build_odv_tx(
        message=message,
        signing_key=funder_pay_sk,
        change_address=funder_addr,
    )
    print(f"Built tx: {result.transaction.id}")

    # Sign locally — produces witness set — but DON'T submit yet.
    for key in [funder_pay_sk, *node_skeys]:
        tx_manager.sign_tx(result.transaction, key)

    tx_cbor_hex = result.transaction.to_cbor().hex()
    print(f"Signed tx CBOR length: {len(tx_cbor_hex)//2} bytes")

    # Evaluate via Blockfrost's /utils/txs/evaluate endpoint — returns full
    # Plutus evaluation result including trace strings. This gives us the
    # real error detail that submit_tx hides behind ScriptFailures=Namespace().
    import json
    import urllib.request

    req = urllib.request.Request(
        "https://cardano-preprod.blockfrost.io/api/v0/utils/txs/evaluate",
        method="POST",
        data=bytes.fromhex(tx_cbor_hex),
        headers={
            "project_id": project_id,
            "Content-Type": "application/cbor",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            eval_result = json.load(resp)
        print("Evaluate result:")
        print(json.dumps(eval_result, indent=2))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"Evaluate HTTP {e.code}:")
        print(body)
        return 1

    # If evaluate succeeded with budgets (not an error), try actual submit.
    if "result" in eval_result or "EvaluationResult" in str(eval_result):
        status, _ = await tx_manager.chain_query.submit_tx(
            result.transaction, wait_confirmation=True
        )
        print(f"Submit status: {status}")
        print(f"Tx: {result.transaction.id}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
