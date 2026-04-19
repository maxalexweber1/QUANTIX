"""Charli3 Bridge — FastAPI wrapper around:
  (a) Blockfrost reads of the on-chain C3AS (Aggregation State) UTxO for
      each supplier's oracle feed.
  (b) An orchestrator that drives an ODV update cycle across N forked
      pull-oracle nodes (each running our multi-feed fork).

Endpoints:
  GET  /feeds/{policy_id}/{asset_name}   → current feed + ref input coords
  POST /odv-update                       → body { policy_id, asset_name } → trigger
  GET  /health                           → per-oracle status

Multi-feed (D-05): oracle deployments carry N AggState UTxOs under one
policy, distinguished by distinct asset names (e.g. `C3AS_inventory`,
`C3AS_price`). `/feeds` takes both `policy_id` and `asset_name` to pick
the right UTxO.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Optional

import cbor2
import httpx
from blockfrost import ApiError, BlockFrostApi
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ── Config ───────────────────────────────────────────────────────────

NETWORK = os.environ.get("NETWORK", "preprod").lower()
BLOCKFROST_PROJECT_ID = os.environ.get("BLOCKFROST_PROJECT_ID", "")

# Map supplier oracle policy → script address. Populated from env so CAP
# can query `/feeds/{policy}/{asset}` with just the policy id.
# Format: "<policy_id>:<bech32_address>,<policy_id>:<bech32_address>,..."
_ORACLE_ADDR_MAP_RAW = os.environ.get("ORACLE_ADDRESSES", "")


def _parse_oracle_addresses(raw: str) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for entry in (raw or "").split(","):
        entry = entry.strip()
        if not entry:
            continue
        if ":" not in entry:
            logger.warning(f"Ignoring malformed ORACLE_ADDRESSES entry: {entry!r}")
            continue
        policy, addr = entry.split(":", 1)
        mapping[policy.strip().lower()] = addr.strip()
    return mapping


ORACLE_ADDRESSES = _parse_oracle_addresses(_ORACLE_ADDR_MAP_RAW)

# Per-supplier node topology. One bridge instance can route /odv-update
# requests to the right supplier by inspecting the request's policy_id.
#
# Two env vars because bridge runs on host, nodes run in docker:
#
#   SUPPLIER_NODES        — host-reachable URLs (bridge -> coordinator)
#   SUPPLIER_NODES_DOCKER — coord-reachable URLs (coordinator -> peers)
#
# Format for both:
#   "<policy_a>:url1|url2|url3,<policy_b>:url1|url2|url3,..."
#
# First URL per supplier is the COORDINATOR; remaining URLs are peers.
# Policy IDs are case-insensitive hex.
#
# When running fully inside one network (no host/container split), set
# only SUPPLIER_NODES; SUPPLIER_NODES_DOCKER falls back to that value.
def _parse_supplier_nodes(raw: str) -> dict[str, list[str]]:
    mapping: dict[str, list[str]] = {}
    for entry in (raw or "").split(","):
        entry = entry.strip()
        if not entry or ":" not in entry:
            continue
        policy, urls_raw = entry.split(":", 1)
        urls = [u.strip().rstrip("/") for u in urls_raw.split("|") if u.strip()]
        if urls:
            mapping[policy.strip().lower()] = urls
    return mapping


SUPPLIER_NODES = _parse_supplier_nodes(os.environ.get("SUPPLIER_NODES", ""))
SUPPLIER_NODES_DOCKER = _parse_supplier_nodes(
    os.environ.get("SUPPLIER_NODES_DOCKER", "")
) or SUPPLIER_NODES

# Per-policy serialization. Both AggStates of a Charli3 oracle co-spend the
# same RewardAccount UTxO (C3RA token) — inventory and price ODV rounds
# therefore conflict on that single input. We serialize rounds of the same
# supplier at the bridge, and chain them at the tx level: Tx A emits a new
# C3RA UTxO, Tx B consumes it directly. No Blockfrost-index wait needed.
# Cross-supplier refreshes (different policies) stay fully parallel.
_POLICY_LOCKS: dict[str, asyncio.Lock] = {}

# Per-policy cache of the latest new-RewardAccount UTxO emitted by the most
# recent aggregation. Value shape: {"input_cbor", "output_cbor", "ts"}. TTL
# of ~180s — after that the chain state is reliable and we drop back to
# on-chain lookup. Invalidated on error or 409 not_yet_expired.
_POLICY_CHAINED_UTXOS: dict[str, dict] = {}
_CHAINED_UTXO_TTL_S = 180


def _lock_for_policy(policy_id: str) -> asyncio.Lock:
    key = policy_id.lower()
    lock = _POLICY_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _POLICY_LOCKS[key] = lock
    return lock


def _get_chained_utxo(policy_id: str) -> dict | None:
    key = policy_id.lower()
    entry = _POLICY_CHAINED_UTXOS.get(key)
    if entry is None:
        return None
    if time.time() - entry["ts"] > _CHAINED_UTXO_TTL_S:
        _POLICY_CHAINED_UTXOS.pop(key, None)
        return None
    return {"input_cbor": entry["input_cbor"], "output_cbor": entry["output_cbor"]}


def _store_chained_utxo(policy_id: str, utxo: dict | None) -> None:
    key = policy_id.lower()
    if not utxo or not utxo.get("input_cbor") or not utxo.get("output_cbor"):
        return
    _POLICY_CHAINED_UTXOS[key] = {
        "input_cbor": utxo["input_cbor"],
        "output_cbor": utxo["output_cbor"],
        "ts": time.time(),
    }


def _invalidate_chained_utxo(policy_id: str) -> None:
    _POLICY_CHAINED_UTXOS.pop(policy_id.lower(), None)

# Legacy single-supplier env vars — still honored as a fallback when the
# request's policy isn't found in SUPPLIER_NODES.
NODE_URLS: list[str] = [
    u.strip().rstrip("/")
    for u in os.environ.get("NODE_URLS", "").split(",")
    if u.strip()
]
COORDINATOR_URL: str = os.environ.get("COORDINATOR_URL", "").rstrip("/")

# Validity window (ms) around current time for the /feed request sent to nodes.
ORCHESTRATOR_VALIDITY_WINDOW_MS = int(
    os.environ.get("ORCHESTRATOR_VALIDITY_WINDOW_MS", "120000")
)

# Minimum number of node responses required to proceed to submit. Falls back
# to NODE_URLS length if unset.
ORCHESTRATOR_THRESHOLD = int(
    os.environ.get("ORCHESTRATOR_THRESHOLD", str(max(1, len(NODE_URLS))))
)


def _nodes_for_policy(policy_id: str) -> list[str]:
    """Return [coordinator, *peers] for the given supplier policy.

    Falls back to legacy COORDINATOR_URL + NODE_URLS env when the policy
    is not registered in SUPPLIER_NODES, so single-supplier deployments
    keep working without the new env var.
    """
    nodes = SUPPLIER_NODES.get(policy_id.lower())
    if nodes:
        return nodes
    if COORDINATOR_URL or NODE_URLS:
        peers = [u for u in NODE_URLS if u != COORDINATOR_URL]
        return [COORDINATOR_URL, *peers] if COORDINATOR_URL else NODE_URLS
    return []


_BLOCKFROST_URLS = {
    "mainnet": "https://cardano-mainnet.blockfrost.io/api",
    "preprod": "https://cardano-preprod.blockfrost.io/api",
    "preview": "https://cardano-preview.blockfrost.io/api",
}


def _api() -> BlockFrostApi:
    base_url = _BLOCKFROST_URLS.get(NETWORK)
    if not base_url:
        raise HTTPException(500, f"Unknown NETWORK '{NETWORK}'")
    if not BLOCKFROST_PROJECT_ID:
        raise HTTPException(
            500,
            "BLOCKFROST_PROJECT_ID env var is not set — cannot read from chain",
        )
    return BlockFrostApi(project_id=BLOCKFROST_PROJECT_ID, base_url=base_url)


# ── Response models ──────────────────────────────────────────────────


class SingleFeed(BaseModel):
    """One Charli3 feed read = one AggState UTxO snapshot.

    `value` semantics depend on which feed this is (inventory → grams,
    price → lovelace-per-unit). The validator doesn't know either; the
    consumer (CAP handler + Aiken order validator) interprets.
    """

    value: int
    timestampMs: str
    validThroughMs: str
    refTxHash: str
    refOutputIndex: int


class OdvUpdateRequest(BaseModel):
    policy_id: str = Field(..., min_length=56, max_length=56)
    asset_name: str = Field(
        ..., description="AggState asset name (e.g. C3AS_inventory)"
    )


class OdvUpdateResponse(BaseModel):
    newC3asTxHash: str
    value: int
    timestampMs: str
    nodesContacted: int = 0
    nodesResponded: int = 0
    status: str = "pending"
    note: Optional[str] = None


class CollectedNodeMessage(BaseModel):
    """Raw response from a single node's /feed/{feed_id} endpoint."""

    node_url: str
    message: str
    signature: str
    verification_key: str


class FeedStatus(BaseModel):
    policyId: str
    assetName: str
    address: Optional[str]
    lastUpdateMs: Optional[str]
    value: Optional[int]


class HealthResponse(BaseModel):
    status: str
    network: str
    feeds: list[FeedStatus]


# ── AggState datum decoding ──────────────────────────────────────────


def _decode_aggstate_datum(cbor_hex: str) -> dict[str, int]:
    """Decode a Charli3 AggState inline datum to its price_map entries.

    The on-chain shape (per `validators/oracle.ak` + `core/datum.ak`):
      AggState(GenericData { price_map: [
        (0, price), (1, time_creation), (2, time_expiration), ...
      ] })

    In Plutus-Data CBOR:
      Constr <aggstate_tag> [
        Constr 0 [ Map<Int, Int> ]
      ]

    cbor2 decodes Plutus Constr tags 121..127 (== constructor 0..6) as
    `CBORTag(tag, value)`. Higher constructors use tag 102 + a wrapped
    list. For the AggState datum we only care about the innermost map.
    """
    raw = bytes.fromhex(cbor_hex)
    decoded = cbor2.loads(raw)
    price_map = _find_price_map(decoded)
    if price_map is None:
        raise ValueError(
            f"Could not locate price_map in AggState datum (decoded: {decoded!r})"
        )
    # Normalize to plain {int: int}. Map keys/values may be plain ints.
    out: dict[int, int] = {}
    if isinstance(price_map, dict):
        out = {int(k): int(v) for k, v in price_map.items()}
    elif isinstance(price_map, list):
        # Pairs<Int,Int> can serialize as a list of 2-element lists.
        for pair in price_map:
            if isinstance(pair, (list, tuple)) and len(pair) == 2:
                out[int(pair[0])] = int(pair[1])
    else:
        raise ValueError(f"Unexpected price_map shape: {type(price_map).__name__}")
    return out


def _find_price_map(node: Any) -> Optional[Any]:
    """Recursively hunt for the first dict/list-of-pairs with keys {0,1,2}.

    Defensive: the exact structure depends on how Aiken emits `Pairs<Int,Int>`
    and which constructor tag the AggState variant uses. Rather than pin that
    (which would break on a recompile), we scan.
    """
    # A dict with at least keys 0 and 1 as ints → looks like price_map.
    if isinstance(node, dict):
        int_keys = [k for k in node.keys() if isinstance(k, int)]
        if 0 in int_keys and 1 in int_keys:
            return node
        for v in node.values():
            found = _find_price_map(v)
            if found is not None:
                return found
    elif isinstance(node, list):
        # List of 2-element pairs with int keys?
        if all(
            isinstance(p, (list, tuple))
            and len(p) == 2
            and isinstance(p[0], int)
            for p in node
        ) and any(p[0] == 0 for p in node):
            return node
        for v in node:
            found = _find_price_map(v)
            if found is not None:
                return found
    elif hasattr(node, "value"):  # cbor2.CBORTag
        return _find_price_map(node.value)
    return None


# ── Blockfrost read ──────────────────────────────────────────────────


def _find_aggstate_utxo(
    api: BlockFrostApi,
    oracle_address: str,
    policy_id: str,
    asset_name: str,
) -> Any:
    """Find the UTxO at oracle_address carrying (policy_id, asset_name) qty=1.

    Blockfrost unit format: policy_id (hex, 56 chars) + asset_name (hex).
    """
    # Blockfrost expects the asset name as hex; ASCII names need encoding.
    asset_hex = asset_name.encode("ascii").hex()
    unit = f"{policy_id.lower()}{asset_hex}"

    utxos = api.address_utxos(oracle_address)
    for utxo in utxos:
        for amount in utxo.amount:
            if amount.unit == unit and int(amount.quantity) >= 1:
                return utxo
    raise HTTPException(
        404,
        f"No UTxO at {oracle_address} carrying ({policy_id}, {asset_name})",
    )


def _read_feed_from_chain(policy_id: str, asset_name: str) -> SingleFeed:
    oracle_address = ORACLE_ADDRESSES.get(policy_id.lower())
    if not oracle_address:
        raise HTTPException(
            404,
            f"Unknown policy_id '{policy_id}'. Configured oracles: "
            f"{sorted(ORACLE_ADDRESSES.keys())}",
        )

    api = _api()
    try:
        utxo = _find_aggstate_utxo(api, oracle_address, policy_id, asset_name)
    except ApiError as e:
        raise HTTPException(502, f"Blockfrost error: {e}") from e

    if not utxo.inline_datum:
        raise HTTPException(
            500,
            f"UTxO at {utxo.tx_hash}#{utxo.output_index} has no inline datum",
        )

    try:
        price_map = _decode_aggstate_datum(utxo.inline_datum)
    except Exception as e:
        raise HTTPException(
            500, f"Failed to decode AggState datum: {e}"
        ) from e

    if 0 not in price_map:
        # Empty or uninitialised AggState (no aggregation yet).
        raise HTTPException(
            503,
            f"AggState for ({policy_id}, {asset_name}) carries no value yet",
        )

    return SingleFeed(
        value=price_map[0],
        timestampMs=str(price_map.get(1, 0)),
        validThroughMs=str(price_map.get(2, 0)),
        refTxHash=utxo.tx_hash,
        refOutputIndex=int(utxo.output_index),
    )


# ── ODV orchestrator ─────────────────────────────────────────────────


def _feed_id_from_asset_name(asset_name: str) -> str:
    """Derive the node feed_id from an AggState asset name.

    Convention: asset names follow `C3AS_<feed_id>` (D-05 fork). If the
    name has no underscore suffix (vanilla single-feed `C3AS`) we map to
    `default` — matches the legacy single-feed migration in the node
    fork's config layer.
    """
    if "_" in asset_name:
        return asset_name.split("_", 1)[1]
    return "default"


def _extract_feed_from_message_cbor(cbor_hex: str) -> Optional[int]:
    """Best-effort extraction of the `feed` int from an OracleNodeMessage.

    Shape: Constr 0 [feed, timestamp, oracle_nft_policy_id]. We only need
    the first int for diagnostic purposes; the rest is validated on-chain.
    """
    try:
        decoded = cbor2.loads(bytes.fromhex(cbor_hex))
    except Exception:
        return None
    if hasattr(decoded, "value"):
        inner = decoded.value
        if isinstance(inner, list) and len(inner) >= 1 and isinstance(inner[0], int):
            return inner[0]
    return None


async def _request_signed_feed(
    client: httpx.AsyncClient,
    node_url: str,
    feed_id: str,
    policy_id: str,
    validity_start_ms: int,
    validity_end_ms: int,
) -> Optional[CollectedNodeMessage]:
    """Call a single node's /feed/{feed_id} endpoint. Return None on failure."""
    url = f"{node_url}/odv/feed/{feed_id}"
    payload = {
        "oracle_nft_policy_id": policy_id,
        "tx_validity_interval": {
            "start": validity_start_ms,
            "end": validity_end_ms,
        },
    }
    try:
        resp = await client.post(url, json=payload, timeout=30.0)
    except httpx.HTTPError as e:
        logger.warning("Node %s unreachable: %s", node_url, e)
        return None
    if resp.status_code != 200:
        logger.warning(
            "Node %s /feed/%s returned %s: %s",
            node_url, feed_id, resp.status_code, resp.text[:200],
        )
        return None
    data = resp.json()
    try:
        return CollectedNodeMessage(
            node_url=node_url,
            message=data["message"],
            signature=data["signature"],
            verification_key=data["verification_key"],
        )
    except (KeyError, TypeError, ValueError) as e:
        logger.warning("Node %s returned malformed payload: %s", node_url, e)
        return None


async def _collect_node_messages(
    feed_id: str,
    policy_id: str,
) -> tuple[list[CollectedNodeMessage], int]:
    """Fan out /feed/{feed_id} to the supplier's nodes in parallel.

    Returns (successes, total_contacted).
    """
    nodes = _nodes_for_policy(policy_id)
    if not nodes:
        raise HTTPException(
            500,
            f"No nodes configured for policy {policy_id} — set SUPPLIER_NODES env var",
        )

    # Wide validity window: pre-roll start by 60s to absorb container clock
    # drift (Docker Desktop on Windows commonly drifts 10-30s after sleep/wake).
    now_ms = int(time.time() * 1000)
    validity_start = now_ms - 180_000
    validity_end = now_ms + ORCHESTRATOR_VALIDITY_WINDOW_MS

    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(
            *[
                _request_signed_feed(
                    client, node_url, feed_id, policy_id,
                    validity_start, validity_end,
                )
                for node_url in nodes
            ],
            return_exceptions=False,
        )

    successes = [r for r in results if r is not None]
    return successes, len(nodes)


# ── API ──────────────────────────────────────────────────────────────


app = FastAPI(title="Charli3 Bridge", version="0.2.0")


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    status = "ok" if BLOCKFROST_PROJECT_ID and ORACLE_ADDRESSES else "degraded"
    feeds = [
        FeedStatus(
            policyId=pid,
            assetName="(any)",
            address=addr,
            lastUpdateMs=None,
            value=None,
        )
        for pid, addr in ORACLE_ADDRESSES.items()
    ]
    return HealthResponse(status=status, network=NETWORK, feeds=feeds)


@app.get("/feeds/{policy_id}/{asset_name}", response_model=SingleFeed)
def get_feed(policy_id: str, asset_name: str) -> SingleFeed:
    if len(policy_id) != 56:
        raise HTTPException(400, f"policy_id must be 56 hex chars, got {len(policy_id)}")
    if not asset_name:
        raise HTTPException(400, "asset_name must not be empty")
    return _read_feed_from_chain(policy_id, asset_name)


# Legacy single-feed endpoint — kept so existing CAP handlers keep working
# until they migrate to the /feeds/{policy}/{asset} form. Assumes vanilla
# Charli3 with a single "C3AS" aggstate per oracle.
@app.get("/feeds/{policy_id}", response_model=SingleFeed, include_in_schema=False)
def get_feed_legacy(policy_id: str) -> SingleFeed:
    if len(policy_id) != 56:
        raise HTTPException(400, f"policy_id must be 56 hex chars, got {len(policy_id)}")
    return _read_feed_from_chain(policy_id, "C3AS")


async def _proxy_to_coordinator(
    feed_id: str, policy_id: str
) -> OdvUpdateResponse:
    """Proxy the aggregate round to the supplier's coordinator node.

    Coordinator does: fan out /odv/feed to peers, build Tx with the
    patched `aggstate_asset_name` lookup, sign with collected keys,
    submit. Returns the Tx hash.

    Dual-coordinator routing (narrative: separate data authorities):
    each feed is routed to a different node in the trio so the fee
    inputs come from different wallets — eliminates the back-to-back
    BadInputsUTxO race we hit with a single coordinator hammering the
    same UTxO set before Blockfrost's index caught up. Coordinator
    assignment: inventory -> nodes[0], price -> nodes[1]. Anything
    else falls back to nodes[0].
    """
    nodes = _nodes_for_policy(policy_id)
    if not nodes:
        raise HTTPException(
            500,
            f"No coordinator configured for policy {policy_id} — set SUPPLIER_NODES env var",
        )
    # Bridge calls coordinator at the host-reachable URL; coordinator must
    # in turn call its peers at docker-network-reachable URLs (different
    # hostnames when bridge is on host and nodes are in docker).
    feed_to_coord_idx = {"inventory": 0, "price": 1}
    coord_idx = feed_to_coord_idx.get(feed_id, 0)
    if coord_idx >= len(nodes):
        coord_idx = 0
    coord_url = nodes[coord_idx]
    docker_nodes = SUPPLIER_NODES_DOCKER.get(policy_id.lower()) or nodes
    peer_urls = [u for i, u in enumerate(docker_nodes) if i != coord_idx]

    lock = _lock_for_policy(policy_id)
    async with lock:
        now_ms = int(time.time() * 1000)
        payload: dict[str, Any] = {
            "oracle_nft_policy_id": policy_id,
            "peer_urls": peer_urls,
            "tx_validity_interval": {
                "start": now_ms - 180_000,
                "end": now_ms + ORCHESTRATOR_VALIDITY_WINDOW_MS,
            },
        }
        # Tx-chaining: if a previous aggregation for this policy produced a
        # new C3RA UTxO (cached under this lock), pass it along. The next
        # coordinator consumes it directly — no ledger-confirmation wait.
        chained = _get_chained_utxo(policy_id)
        if chained is not None:
            payload["reward_account_utxo_override"] = chained

        url = f"{coord_url}/odv/aggregate/{feed_id}"

        async with httpx.AsyncClient(timeout=120.0) as client:
            try:
                resp = await client.post(url, json=payload)
            except httpx.HTTPError as e:
                raise HTTPException(502, f"Coordinator unreachable: {e}") from e

        if resp.status_code != 200:
            body = resp.text[:500]
            try:
                parsed = resp.json()
            except Exception:
                parsed = {}
            # Any failure voids the chained UTxO cache — next call resyncs
            # from chain state.
            _invalidate_chained_utxo(policy_id)
            if resp.status_code == 409 and parsed.get("not_yet_expired"):
                return OdvUpdateResponse(
                    newC3asTxHash="",
                    value=0,
                    timestampMs=str(now_ms),
                    nodesContacted=len(nodes),
                    nodesResponded=0,
                    status="not_yet_expired",
                    note=(
                        f"Feed '{feed_id}' was updated recently and is still within "
                        "its on-chain validity window. Wait ~2 minutes before "
                        "triggering a new round."
                    ),
                )
            raise HTTPException(
                resp.status_code,
                f"Coordinator returned {resp.status_code}: {body[:200]}",
            )
        data = resp.json()
        _store_chained_utxo(policy_id, data.get("new_reward_account_utxo"))
        return OdvUpdateResponse(
            newC3asTxHash=data.get("tx_hash", ""),
            value=int(data.get("feed_value", 0)),
            timestampMs=str(data.get("timestamp_ms", now_ms)),
            nodesContacted=len(nodes),
            nodesResponded=1 + int(data.get("peers_responded", 0)),
            status=data.get("status", "submitted"),
            note=f"Coordinator {coord_url} aggregated feed '{feed_id}'",
        )


@app.post("/odv-update", response_model=OdvUpdateResponse)
async def odv_update(req: OdvUpdateRequest) -> OdvUpdateResponse:
    """Orchestrate an ODV aggregation cycle for a specific feed.

    Two modes:
      - **Full** (COORDINATOR_URL set): bridge proxies the whole round to
        a designated coordinator node that fans out to peers, builds,
        signs, and submits the Tx. Response carries the real tx hash.
      - **Collector-only** (COORDINATOR_URL unset): bridge gathers signed
        feed messages from NODE_URLS and returns them without submitting.
        Useful for local dev or when submit is handled out-of-band.
    """
    feed_id = _feed_id_from_asset_name(req.asset_name)

    # Submit-mode whenever there's a known coordinator (either per-supplier
    # via SUPPLIER_NODES, or the legacy single-supplier COORDINATOR_URL).
    if SUPPLIER_NODES.get(req.policy_id.lower()) or COORDINATOR_URL:
        return await _proxy_to_coordinator(feed_id, req.policy_id)

    try:
        successes, total = await _collect_node_messages(feed_id, req.policy_id)
    except HTTPException:
        raise
    except Exception as e:  # pragma: no cover
        logger.error("Unexpected orchestrator failure: %s", e, exc_info=True)
        raise HTTPException(500, f"Orchestrator failed: {e}") from e

    now_ms = int(time.time() * 1000)

    if len(successes) < ORCHESTRATOR_THRESHOLD:
        return OdvUpdateResponse(
            newC3asTxHash="",
            value=0,
            timestampMs=str(now_ms),
            nodesContacted=total,
            nodesResponded=len(successes),
            status="insufficient_responses",
            note=(
                f"Only {len(successes)}/{total} nodes responded; "
                f"threshold is {ORCHESTRATOR_THRESHOLD}. Check node health."
            ),
        )

    feed_values = [
        v for v in (_extract_feed_from_message_cbor(s.message) for s in successes)
        if v is not None
    ]
    median_feed = (
        sorted(feed_values)[len(feed_values) // 2] if feed_values else 0
    )

    return OdvUpdateResponse(
        newC3asTxHash="",
        value=median_feed,
        timestampMs=str(now_ms),
        nodesContacted=total,
        nodesResponded=len(successes),
        status="collected",
        note=(
            f"{len(successes)} signed messages gathered (median feed={median_feed}). "
            "Set COORDINATOR_URL to enable automatic submit."
        ),
    )
