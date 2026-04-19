"""
Mock ERP returning inventory + unit price for a single product code.

One instance per supplier -- configured via env:
  ERP_PRODUCT_CODE   : the SKU this instance serves (default: LI-CARB-01)
  ERP_INITIAL_G      : starting inventory in grams (default: 10000)
  ERP_UNIT_PRICE     : lovelace per gram (default: 1000)

Optional simulation loops (both run as FastAPI lifespan background tasks):

  ERP_JITTER=true     enables price random-walk + slow inventory drift.
    ERP_JITTER_INTERVAL_S   = 60
    ERP_PRICE_FLOOR         = unit_price // 2
    ERP_PRICE_CEIL          = unit_price * 2
    ERP_PRICE_AMPLITUDE     = 0.05   (= +/-5% per tick)
    ERP_INVENTORY_DRIFT     = 0.02   (= up to 2% consumed per tick)
    ERP_RESTOCK_PROBABILITY = 0.1    (= 10% of ticks restock +5..15%)

  ERP_CHAIN_WATCH=true enables Blockfrost polling for buy-Txs that paid
  this supplier. For every new Tx it finds, it decrements inventory by
  the purchased grams (exact if metadata label 674 `c3supply.grams` is
  present, otherwise approximated from lovelace_paid / current_price).
    ERP_CHAIN_WATCH_INTERVAL_S  = 10
    ERP_SUPPLIER_PAYMENT_ADDR   = addr_test1... (required)
    ERP_MINT_POLICY_ID          = hex (required -- filters to buy-txs
                                  vs unrelated payments)
    BLOCKFROST_PROJECT_ID       = preprod... (required)

Endpoints:
  GET  /inventory?product_code=X  -> current quantity + price + timestamp
  POST /admin/set_inventory       -> overwrite current_g (demo control)
  POST /admin/consume             -> decrement current_g by {grams}
  GET  /health                    -> liveness + sim status
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
import time
from contextlib import asynccontextmanager
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

logger = logging.getLogger("erp-mock")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")

PRODUCT_CODE = os.environ.get("ERP_PRODUCT_CODE", "LI-CARB-01")
INITIAL_G = int(os.environ.get("ERP_INITIAL_G", "10000"))
UNIT_PRICE = int(os.environ.get("ERP_UNIT_PRICE", "1000"))

# Jitter config
JITTER_ENABLED = os.environ.get("ERP_JITTER", "").lower() in ("true", "1", "yes")
JITTER_INTERVAL_S = int(os.environ.get("ERP_JITTER_INTERVAL_S", "60"))
PRICE_FLOOR = int(os.environ.get("ERP_PRICE_FLOOR", str(UNIT_PRICE // 2)))
PRICE_CEIL = int(os.environ.get("ERP_PRICE_CEIL", str(UNIT_PRICE * 2)))
PRICE_AMPLITUDE = float(os.environ.get("ERP_PRICE_AMPLITUDE", "0.05"))
INVENTORY_DRIFT = float(os.environ.get("ERP_INVENTORY_DRIFT", "0.02"))
RESTOCK_PROBABILITY = float(os.environ.get("ERP_RESTOCK_PROBABILITY", "0.1"))

# Chain-watch config
CHAIN_WATCH_ENABLED = os.environ.get("ERP_CHAIN_WATCH", "").lower() in ("true", "1", "yes")
CHAIN_WATCH_INTERVAL_S = int(os.environ.get("ERP_CHAIN_WATCH_INTERVAL_S", "10"))
SUPPLIER_PAYMENT_ADDR = os.environ.get("ERP_SUPPLIER_PAYMENT_ADDR", "").strip()
MINT_POLICY_ID = os.environ.get("ERP_MINT_POLICY_ID", "").strip().lower()
BLOCKFROST_PROJECT_ID = os.environ.get("BLOCKFROST_PROJECT_ID", "").strip()

_BLOCKFROST_URLS = {
    "mainnet": "https://cardano-mainnet.blockfrost.io/api",
    "preprod": "https://cardano-preprod.blockfrost.io/api",
    "preview": "https://cardano-preview.blockfrost.io/api",
}
_BF_BASE = _BLOCKFROST_URLS.get(os.environ.get("NETWORK", "preprod").lower(),
                                 _BLOCKFROST_URLS["preprod"])


class InventoryResponse(BaseModel):
    product_code: str
    available_g: int = Field(..., ge=0)
    unit_price_lovelace: int = Field(..., ge=0)
    timestamp_ms: int


class SetInventoryRequest(BaseModel):
    available_g: int = Field(..., ge=0)
    unit_price_lovelace: Optional[int] = Field(None, ge=0)


class ConsumeRequest(BaseModel):
    grams: int = Field(..., gt=0)


class State:
    available_g: int = INITIAL_G
    unit_price_lovelace: int = UNIT_PRICE
    # Chain-watch state:
    seen_tx_hashes: set[str] = set()
    last_observed_block_height: int = 0


def _now_ms() -> int:
    return int(time.time() * 1000)


# ---- Jitter loop ---------------------------------------------------------

async def jitter_loop() -> None:
    logger.info(
        "jitter: enabled, interval=%ss, price [%d, %d] +/-%.0f%%, "
        "inventory drift <=%.0f%%, restock prob=%.0f%%",
        JITTER_INTERVAL_S, PRICE_FLOOR, PRICE_CEIL, PRICE_AMPLITUDE * 100,
        INVENTORY_DRIFT * 100, RESTOCK_PROBABILITY * 100,
    )
    while True:
        try:
            await asyncio.sleep(JITTER_INTERVAL_S)
            # Price random walk bounded to [floor, ceil]
            delta = random.uniform(-PRICE_AMPLITUDE, PRICE_AMPLITUDE)
            new_price = max(PRICE_FLOOR,
                            min(PRICE_CEIL,
                                int(State.unit_price_lovelace * (1 + delta))))
            # Inventory: chance of restock, otherwise slow drift-down
            if random.random() < RESTOCK_PROBABILITY:
                restock = int(INITIAL_G * random.uniform(0.05, 0.15))
                State.available_g += restock
                action = f"restock +{restock}g"
            else:
                consumed = int(State.available_g * random.uniform(0, INVENTORY_DRIFT))
                State.available_g = max(0, State.available_g - consumed)
                action = f"drift -{consumed}g" if consumed else "drift 0g"
            State.unit_price_lovelace = new_price
            logger.info(
                "jitter: price=%d inv=%d (%s)",
                new_price, State.available_g, action,
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception("jitter: loop error: %s", e)


# ---- Chain-watch loop ----------------------------------------------------

async def _poll_address_txs(
    client: httpx.AsyncClient, from_height: int
) -> list[dict]:
    """List txs at SUPPLIER_PAYMENT_ADDR paginated, youngest first."""
    params = {"order": "desc"}
    url = f"{_BF_BASE}/v0/addresses/{SUPPLIER_PAYMENT_ADDR}/transactions"
    resp = await client.get(url, params=params, headers={"project_id": BLOCKFROST_PROJECT_ID})
    if resp.status_code == 404:
        # Address has no txs yet -- expected until first buy lands.
        return []
    if resp.status_code != 200:
        logger.warning("chain-watch: addresses/txs %s %s", resp.status_code, resp.text[:200])
        return []
    rows = resp.json()
    return [r for r in rows if int(r.get("block_height", 0)) > from_height]


async def _tx_is_buy_for_us(client: httpx.AsyncClient, tx_hash: str) -> Optional[dict]:
    """Returns the tx detail dict if it's a buy-Tx for this supplier
    (mints the supplier's order-mint policy), else None."""
    if not MINT_POLICY_ID:
        # No filter: treat every payment as potential buy. Returns the utxos call.
        resp = await client.get(
            f"{_BF_BASE}/v0/txs/{tx_hash}/utxos",
            headers={"project_id": BLOCKFROST_PROJECT_ID},
        )
        return resp.json() if resp.status_code == 200 else None

    # Check if tx minted anything under our policy
    resp = await client.get(
        f"{_BF_BASE}/v0/txs/{tx_hash}",
        headers={"project_id": BLOCKFROST_PROJECT_ID},
    )
    if resp.status_code != 200:
        return None
    tx_data = resp.json()
    if int(tx_data.get("asset_mint_or_burn_count", 0)) == 0:
        return None

    # Confirm the mint involves our policy via /txs/{hash}/mir (actually
    # use /txs/{hash}/utxos + check output assets)
    utxos_resp = await client.get(
        f"{_BF_BASE}/v0/txs/{tx_hash}/utxos",
        headers={"project_id": BLOCKFROST_PROJECT_ID},
    )
    if utxos_resp.status_code != 200:
        return None
    utxos = utxos_resp.json()
    for out in utxos.get("outputs", []):
        for amt in out.get("amount", []):
            unit = amt.get("unit", "")
            if unit.lower().startswith(MINT_POLICY_ID):
                return utxos
    return None


async def _read_tx_metadata_grams(
    client: httpx.AsyncClient, tx_hash: str
) -> Optional[int]:
    """Return exact grams from metadata label 674 c3supply.grams, or None."""
    resp = await client.get(
        f"{_BF_BASE}/v0/txs/{tx_hash}/metadata",
        headers={"project_id": BLOCKFROST_PROJECT_ID},
    )
    if resp.status_code != 200:
        return None
    for entry in resp.json():
        if str(entry.get("label")) != "674":
            continue
        json_meta = entry.get("json_metadata") or {}
        c3 = json_meta.get("c3supply") or {}
        g = c3.get("grams")
        if g is not None:
            try:
                return int(g)
            except (TypeError, ValueError):
                return None
    return None


def _lovelace_to_this_address(utxos: dict) -> int:
    total = 0
    for out in utxos.get("outputs", []):
        if out.get("address") != SUPPLIER_PAYMENT_ADDR:
            continue
        for amt in out.get("amount", []):
            if amt.get("unit") == "lovelace":
                total += int(amt.get("quantity", 0))
    return total


async def chain_watch_loop() -> None:
    if not (SUPPLIER_PAYMENT_ADDR and BLOCKFROST_PROJECT_ID):
        logger.warning("chain-watch: enabled but missing ERP_SUPPLIER_PAYMENT_ADDR"
                       " or BLOCKFROST_PROJECT_ID — skipping")
        return

    logger.info(
        "chain-watch: enabled, interval=%ss, addr=%s..., policy=%s...",
        CHAIN_WATCH_INTERVAL_S, SUPPLIER_PAYMENT_ADDR[:20],
        MINT_POLICY_ID[:12] if MINT_POLICY_ID else "(no filter)",
    )

    # Bootstrap: skip txs that exist at startup (don't retroactively consume).
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.get(
                f"{_BF_BASE}/v0/addresses/{SUPPLIER_PAYMENT_ADDR}/transactions",
                params={"order": "desc"},
                headers={"project_id": BLOCKFROST_PROJECT_ID},
            )
            if resp.status_code == 200:
                rows = resp.json()
                if rows:
                    State.last_observed_block_height = max(
                        int(r.get("block_height", 0)) for r in rows
                    )
                    State.seen_tx_hashes = {r["tx_hash"] for r in rows}
                    logger.info("chain-watch: bootstrap -- %d existing txs ignored "
                                "(start watching from block %d+)",
                                len(rows), State.last_observed_block_height)
        except Exception as e:
            logger.warning("chain-watch: bootstrap failed: %s", e)

    while True:
        try:
            await asyncio.sleep(CHAIN_WATCH_INTERVAL_S)
            async with httpx.AsyncClient(timeout=30.0) as client:
                new_rows = await _poll_address_txs(
                    client, State.last_observed_block_height
                )
                for row in reversed(new_rows):  # oldest first
                    tx_hash = row["tx_hash"]
                    if tx_hash in State.seen_tx_hashes:
                        continue
                    utxos = await _tx_is_buy_for_us(client, tx_hash)
                    if not utxos:
                        State.seen_tx_hashes.add(tx_hash)
                        continue
                    # Try exact grams from metadata first, fall back to approx
                    grams = await _read_tx_metadata_grams(client, tx_hash)
                    source = "metadata"
                    if grams is None and State.unit_price_lovelace > 0:
                        lov = _lovelace_to_this_address(utxos)
                        grams = lov // State.unit_price_lovelace
                        source = f"approx ({lov} lov / {State.unit_price_lovelace} lov-per-g)"
                    if grams and grams > 0:
                        before = State.available_g
                        State.available_g = max(0, State.available_g - grams)
                        logger.info(
                            "chain-watch: tx %s -- consumed %dg (%s). inv %d -> %d",
                            tx_hash[:16], grams, source, before, State.available_g,
                        )
                    else:
                        logger.info("chain-watch: tx %s -- no grams extractable", tx_hash[:16])
                    State.seen_tx_hashes.add(tx_hash)
                    State.last_observed_block_height = max(
                        State.last_observed_block_height,
                        int(row.get("block_height", 0)),
                    )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception("chain-watch: loop error: %s", e)


# ---- FastAPI lifecycle ---------------------------------------------------

@asynccontextmanager
async def lifespan(_app: FastAPI):
    tasks: list[asyncio.Task] = []
    if JITTER_ENABLED:
        tasks.append(asyncio.create_task(jitter_loop(), name="jitter"))
    if CHAIN_WATCH_ENABLED:
        tasks.append(asyncio.create_task(chain_watch_loop(), name="chain-watch"))
    try:
        yield
    finally:
        for t in tasks:
            t.cancel()
        for t in tasks:
            try:
                await t
            except asyncio.CancelledError:
                pass


app = FastAPI(title=f"ERP Mock -- {PRODUCT_CODE}", version="0.2.0", lifespan=lifespan)


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "product_code": PRODUCT_CODE,
        "available_g": State.available_g,
        "unit_price_lovelace": State.unit_price_lovelace,
        "jitter": JITTER_ENABLED,
        "chain_watch": CHAIN_WATCH_ENABLED,
        "seen_txs": len(State.seen_tx_hashes),
    }


@app.get("/inventory", response_model=InventoryResponse)
def inventory(product_code: str = Query(...)) -> InventoryResponse:
    if product_code != PRODUCT_CODE:
        raise HTTPException(404, f"Unknown product_code {product_code}")
    return InventoryResponse(
        product_code=PRODUCT_CODE,
        available_g=State.available_g,
        unit_price_lovelace=State.unit_price_lovelace,
        timestamp_ms=_now_ms(),
    )


@app.post("/admin/set_inventory", response_model=InventoryResponse)
def set_inventory(body: SetInventoryRequest) -> InventoryResponse:
    State.available_g = body.available_g
    if body.unit_price_lovelace is not None:
        State.unit_price_lovelace = body.unit_price_lovelace
    return inventory(product_code=PRODUCT_CODE)


@app.post("/admin/consume", response_model=InventoryResponse)
def consume(body: ConsumeRequest) -> InventoryResponse:
    if body.grams > State.available_g:
        raise HTTPException(400, f"Only {State.available_g} g available")
    State.available_g -= body.grams
    return inventory(product_code=PRODUCT_CODE)
