# Charli3 Bridge

FastAPI service sitting between the CAP app and the on-chain / oracle
world. Two jobs:

1. **On-chain read.** Fetch a supplier's AggState UTxO via Blockfrost,
   decode the inline datum, and return `{ value, timestampMs,
   validThroughMs, refTxHash, refOutputIndex }` — the coordinates CAP
   needs to plug the feed in as a **reference input** on a mint tx.
2. **ODV orchestration.** On `/odv-update`, pick the right coordinator
   node for the supplier and feed, fan out to peers, drive one
   aggregation round, return the new Tx hash.

Runs on the host (not in `docker-compose.yml`) — started via
`scripts/start-all.sh` together with CAP. The node containers are the
ones that live in compose; the bridge talks to them over
`localhost:810x`.

## Endpoints

```
GET  /feeds/{policy_id}/{asset_name}   → current feed (uses Blockfrost)
POST /odv-update                        → trigger an ODV aggregation round
GET  /health                            → per-oracle status
```

`asset_name` is the multi-feed AggState token name, e.g.
`C3AS_inventory` or `C3AS_price`. A legacy single-feed form
`GET /feeds/{policy_id}` (assumes `C3AS`) is kept for backward
compatibility but hidden from the schema.

## Config (env)

| Var | Required | Purpose |
| --- | --- | --- |
| `BLOCKFROST_PROJECT_ID` | yes | Blockfrost read key (network-scoped) |
| `NETWORK` | no | `preprod` / `preview` / `mainnet` (default `preprod`) |
| `ORACLE_ADDRESSES` | yes | `<policy>:<bech32>,...` — maps supplier policy → oracle script address |
| `SUPPLIER_NODES` | yes (submit mode) | `<policy>:<coord_url>\|<peer_url>\|...,...` — host-reachable node URLs |
| `SUPPLIER_NODES_DOCKER` | no | same shape, docker-network URLs for coord→peer calls; defaults to `SUPPLIER_NODES` |
| `ORCHESTRATOR_VALIDITY_WINDOW_MS` | no | tx-validity upper-bound offset, default 120000 |

Legacy single-supplier fallback env: `COORDINATOR_URL`, `NODE_URLS`,
`ORCHESTRATOR_THRESHOLD`. Only used when no `SUPPLIER_NODES` entry
matches the request's policy.

## Dual-coordinator routing

For each supplier's 3-node trio, `/odv-update` routes:

- feed `inventory` → `nodes[0]` (Warehouse/Inventory coordinator)
- feed `price`     → `nodes[1]` (Pricing-desk coordinator)
- other            → `nodes[0]`

Rationale: each coordinator's own wallet funds tx fees for its round.
Routing the two feeds to different wallets avoids the back-to-back
`BadInputsUTxO` race we hit when a single coordinator fired two rounds
before Blockfrost's UTxO index caught up.

## Tx-chaining

Both AggState UTxOs of one oracle co-spend the same `C3RA`
RewardAccount UTxO. Back-to-back inventory and price rounds therefore
race on that single input. The bridge serializes rounds **per policy**
(a per-policy `asyncio.Lock`) and, on success, caches the coordinator's
newly-emitted `C3RA` UTxO so the next round consumes it directly —
no ledger-confirmation wait between the two rounds. Cross-supplier
calls stay fully parallel. The cache has a 180 s TTL and is
invalidated on any error or `409 not_yet_expired`.

## Running locally

```bash
cd services/charli3-bridge
pip install -e .
uvicorn charli3_bridge.main:app --host 0.0.0.0 --port 8000
```

The `.env` at repo root supplies all required env vars —
`scripts/start-all.sh` loads it before boot.
