# Charli3 Oracle Node Configurations

3 suppliers × 3 nodes = **9 oracle node containers**. Each node runs our
multi-feed fork (`c3-supply/charli3-node:multi-feed`) and signs **both**
the inventory and price feed for its supplier in a single process.

## Per-supplier deployment shape

Per supplier:
- **One** parameterised oracle script → **one** policy ID
- **Two** AggState UTxOs sharing that policy, distinguished by asset name:
  `C3AS_inventory` and `C3AS_price`
- **Three** node feed keys (2-of-3 threshold per aggregation)
- **Two** coordinators per supplier, narratively split as separate data
  authorities to avoid the back-to-back `BadInputsUTxO` race we hit with
  a single coordinator:
  - `node-X-1` = Warehouse/Inventory coordinator (drives `C3AS_inventory`)
  - `node-X-2` = Pricing-desk coordinator (drives `C3AS_price`)
  - `node-X-3` = peer signer only

Both coordinators mount `./secrets/supplier-X/` read-only so each has
all 3 `*.feed.skey` files for the threshold aggregate. Their own
`payment.skey` (derived from the per-coordinator `MNEMONIC`) pays tx
fees, which is why the two coordinators use separate wallet UTxO pools.

## Files

```
supplier-a.yml              Supplier-A node config (both feeds)
supplier-b.yml              Supplier-B node config (both feeds)
supplier-c.yml              Supplier-C node config (both feeds)
deploy-supplier-a.yaml      One-off deploy config for `charli3 oracle deploy` (Supplier A)
deploy-supplier-b.yaml      … Supplier B
deploy-supplier-c.yaml      … Supplier C
```

The `supplier-X.yml` files are mounted into every node container of that
supplier (see `docker-compose.yml`). The `deploy-*.yaml` files are
consumed only once per supplier by the deploy CLI to mint the platform
auth NFT, create the reference script, and deploy the oracle.

## Multi-feed config layout

Each `supplier-X.yml` declares both feeds under `Node.feeds`:

```yaml
Node:
  oracle_currency: "<policy id from deploy>"
  oracle_address:  "<oracle script address from deploy>"
  feeds:
    - feed_id: inventory
      asset_name: "C3AS_inventory"
      rate:
        ...api_sources pointing at http://erp-X:8001/inventory?product_code=...
        json_path: ["available_g"]
    - feed_id: price
      asset_name: "C3AS_price"
      rate:
        ...same URL, different json_path
        json_path: ["unit_price_lovelace"]
```

The ERP responds with
`{ available_g, unit_price_lovelace, timestamp_ms, product_code }`, so a
single endpoint feeds both `json_path`s.

## How the `generic-api` adapter works

The upstream charli3-pull-oracle-node ships a `generic-api` adapter
(`node/services/price_fetcher/generic_api.py`) which does:

```
HTTP GET url → JSON response → extract numeric value at json_path
→ Rate(price = float(value), source = name)
```

That's all that's needed for both inventory (grams available) and price
(lovelace per gram) — same adapter, different `json_path`.

## Deploy sequence (per supplier)

Run from the repo root with Python 3.11 + the c3-supply SDK fork installed:

```bash
S=a   # or b, c

charli3 platform token mint   --config services/charli3-fork/configs/deploy-supplier-$S.yaml
charli3 reference-script create --config services/charli3-fork/configs/deploy-supplier-$S.yaml
charli3 oracle deploy           --config services/charli3-fork/configs/deploy-supplier-$S.yaml
```

After `oracle deploy`: record the resulting `policy_id` + `oracle_address`
into `apps/cap/db/data/c3.supply-Suppliers.csv` and into `.env`'s
`ORACLE_ADDRESSES` map, then update the matching `supplier-$S.yml` so
the running nodes pick up the new oracle.

## SDK / contracts forks required

This deployment shape needs three c3-supply forks (one policy carrying
two AggState UTxOs differentiated by asset-name suffix):

- `charli3-pull-oracle-contracts` branch `c3-supply/asset-prefix` —
  Aiken validator that accepts the `_inventory` / `_price` suffixes
- `charli3-pull-oracle-sdk` branch `c3-supply/multi-aggstate` —
  understands `aggstate_count` + `aggstate_asset_suffixes` in the deploy
  config
- `charli3-pull-oracle-node` (this fork) — runs multiple feeds in one
  process under `Node.feeds`
