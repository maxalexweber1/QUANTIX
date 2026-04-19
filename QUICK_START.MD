## Quick start

### Prerequisites

- Node 20+, pnpm 9, Python 3.11, Aiken 1.1+, Docker Desktop
- Blockfrost `preprod` project ID
- A wallet extension (Eternl / Nami / Lace) set to the `preprod` network

### Setup

```bash
cp .env.example .env
# Fill in BLOCKFROST_PROJECT_ID and, if running the full oracle stack,
# the 9 node mnemonics + supplier payment addresses (see .env.example).

pnpm install
```

### Running the full stack (local dev)

```bash
# Option 1 — everything in Docker (oracle nodes + ERPs + infra)
docker compose up

# Option 2 — oracle stack in Docker, CAP + Bridge on host (faster iteration)
docker compose up          # boots 9 nodes + 3 ERP mocks
bash scripts/start-all.sh  # starts bridge + CAP on host
```

Endpoints:

- Webapp: `http://localhost:4004/quantix/webapp/` (Suppliers + My Buys tabs)
- CAP OData: `http://localhost:4004/odata/v4/orders-service/`
- Bridge: `http://localhost:8000`
- ERPs: `http://localhost:8001` (A), `http://localhost:8002` (B), `http://localhost:8003` (C)
- Node containers (per supplier `X` = a/b/c, `N` = 1..3):
  - `http://localhost:8X01` — inventory coordinator (node-X-1)
  - `http://localhost:8X02` — price coordinator (node-X-2)
  - `http://localhost:8X03` — peer signer (node-X-3)