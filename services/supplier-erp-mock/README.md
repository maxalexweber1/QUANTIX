# Supplier ERP Mock

FastAPI service that impersonates a supplier's ERP. One instance per
supplier (A/B/C), each serving a single SKU. The `generic-api` adapter
in each Charli3 oracle node polls this ERP for both feeds (inventory
and price) from the same endpoint.

## Endpoints

```
GET  /inventory?product_code=X   → { available_g, unit_price_lovelace, timestamp_ms, product_code }
POST /admin/set_inventory         → { available_g, unit_price_lovelace? } — overwrite state
POST /admin/consume               → { grams } — decrement inventory
GET  /health                      → liveness + sim status
```

The node's two feeds both hit `/inventory` and pick different JSON
paths (`available_g` vs. `unit_price_lovelace`) — see
`services/charli3-fork/configs/supplier-X.yml`.

## Config (env)

| Var | Default | Purpose |
| --- | --- | --- |
| `ERP_PRODUCT_CODE` | `LI-CARB-01` | SKU this instance serves |
| `ERP_INITIAL_G` | `10000` | starting inventory in grams |
| `ERP_UNIT_PRICE` | `1000` | lovelace per gram |

### Optional: price + inventory jitter

Enables a random-walk on price and a slow drift on inventory — makes
demo readings move instead of flatlining.

| Var | Default |
| --- | --- |
| `ERP_JITTER` | `false` |
| `ERP_JITTER_INTERVAL_S` | `60` |
| `ERP_PRICE_FLOOR` | `UNIT_PRICE / 2` |
| `ERP_PRICE_CEIL`  | `UNIT_PRICE * 2` |
| `ERP_PRICE_AMPLITUDE` | `0.05` (±5%/tick) |
| `ERP_INVENTORY_DRIFT` | `0.02` (≤2%/tick consumed) |
| `ERP_RESTOCK_PROBABILITY` | `0.1` (10%/tick restock +5–15%) |

### Optional: chain-watch

Polls Blockfrost for buy-Txs paying this supplier's address and
decrements `available_g` accordingly. Exact grams read from CIP-20
metadata label `674` (`c3supply.grams`); falls back to
`lovelace_paid / current_price` if absent.

| Var | Required | Purpose |
| --- | --- | --- |
| `ERP_CHAIN_WATCH` | — | enable loop |
| `ERP_CHAIN_WATCH_INTERVAL_S` | — | default 10 |
| `ERP_SUPPLIER_PAYMENT_ADDR` | yes | supplier's bech32 address |
| `ERP_MINT_POLICY_ID` | yes | order-mint policy for this supplier (filter) |
| `BLOCKFROST_PROJECT_ID` | yes | read key |
| `NETWORK` | no | default `preprod` |

At startup the chain-watcher ignores any txs already present at the
address — it only consumes inventory for txs observed **after** boot,
so re-running the stack doesn't double-decrement.

## Running locally

```bash
cd services/supplier-erp-mock
pip install -e .
uvicorn supplier_erp_mock.main:app --host 0.0.0.0 --port 8001
```

Compose boots three instances with the supplier-specific config —
`erp-a` (port 8001, lithium), `erp-b` (8002, cobalt), `erp-c` (8003,
rare-earth).
