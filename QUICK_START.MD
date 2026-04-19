## Quick start

### Prerequisites

- Node 20+, pnpm 9, Python 3.11, Aiken 1.1+, Docker Desktop
- Blockfrost `preprod` project ID
- A wallet extension (Eternl / Nami / Lace) set to the `preprod` network

### Setup

```bash
cp .env.example .env
pnpm install
```

[`.env.example`](./.env.example) is the authoritative template — **nothing
works without it filled in**. At minimum you need:

- `BLOCKFROST_PROJECT_ID` — `preprod` project ID
- `OGMIOS_URL` reachable Ogmios v6 JSON-RPC endpoint. **Required** for
  ODV rounds: every forked oracle node routes `evaluate_tx_cbor` through
  Ogmios (Blockfrost's evaluate-tx swallows empty ScriptFailures, and
  tx-chaining needs `additionalUtxo` forwarding for not-yet-confirmed
  C3RA outputs). `scripts/odv-first-round.py` uses the same endpoint
- `ORACLE_ADDRESSES`, `SUPPLIER_NODES`, `SUPPLIER_NODES_DOCKER` per-supplier
  policy → oracle-address and policy → node-URL maps (bridge reads these to
  locate AggState UTxOs and fan out ODV rounds)
- `INVENTORY_POLICY_ID_SUPPLIER_{A,B,C}` / `PRICE_POLICY_ID_SUPPLIER_{A,B,C}`
  and matching `*_ORACLE_ADDR_*` consumed by the mint-policy parameters
- `REF_SCRIPT_ADDR_SUPPLIER_*` + `REF_SCRIPT_TX_HASH_SUPPLIER_*` — Charli3
  ref-script UTxOs (one per supplier, populated after `deploy_settings`)
- `ORDER_MINT_POLICY_SUPPLIER_*` + `ORDER_MINT_REFSCRIPT_TX_HASH_SUPPLIER_*`
  + `*_OUTPUT_INDEX_*` per-supplier order-mint ref-scripts
- `MNEMONIC_{A,B,C}_{1,2,3}` 9 node mnemonics (24 words each), required for
  the full oracle stack to sign ODV rounds
- `SUPPLIER_{A,B,C}_PAYMENT_ADDR` bech32 payment address per supplier (the
  order-mint policy checks an output pays this hash)
- `ERP_{A,B,C}_URL` ERP mock endpoints the nodes poll

See the header comments in `.env.example` for each variable's purpose.

### ODATANO Blockfrost key (CAP side)

ODATANO runs as a CAP plugin and does **not** read `BLOCKFROST_API_KEY` from
`.env` in this project. The Blockfrost credential must be set as a literal
string in [`apps/cap/package.json`](./apps/cap/package.json) under
`cds.requires.odatano-core.blockfrostApiKey`. `@sap/cds` does not expand
`${...}` placeholders in custom config fields, so writing the raw value there
is the most reliable path:

```json
{
  "cds": {
    "requires": {
      "odatano-core": {
        "network": "preprod",
        "backends": ["blockfrost"],
        "blockfrostApiKey": "preprod...your_key_here...",
        "txBuilders": ["buildooor"],
        "primaryTimeoutMs": 30000,
        "fallbackTimeoutMs": 60000,
        "indexTtlMs": 3600000
      }
    }
  }
}
```

Replace the `"your_key_here"` placeholder with your real
`preprod` project ID. CAP will not start correctly until this is set.

The Python side (bridge, ERP mocks, docker nodes, deploy scripts) reads the
same key from `BLOCKFROST_PROJECT_ID` in `.env` — that one is separate and
still required.


### Running the full stack (local dev)

```bash
# Option 1 — everything in Docker (oracle nodes + ERPs + infra)
docker compose up

# Option 2 — oracle stack in Docker, CAP + Bridge on host (faster iteration)
docker compose up          # boots 9 nodes + 3 ERP mocks
bash scripts/start-all.sh  # starts bridge + CAP on host
```

Endpoints:

- Webapp: `http://localhost:4004/quantix/webapp/` (Suppliers + My Buys tabs + Orcale Feeds)
- CAP OData: `http://localhost:4004/odata/v4/orders-service/`
- Bridge: `http://localhost:8000`
- ERPs: `http://localhost:8001` (A), `http://localhost:8002` (B), `http://localhost:8003` (C)
- Node containers (per supplier `X` = a/b/c, `N` = 1..3):
  - `http://localhost:8X01` — inventory coordinator (node-X-1)
  - `http://localhost:8X02` — price coordinator (node-X-2)
  - `http://localhost:8X03` — peer signer (node-X-3)