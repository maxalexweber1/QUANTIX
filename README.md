# QUANTIX - C3-Supply

**Delivery commitments that enforce themselves on-chain.**

QUANTIX brings the two facts every physical-goods trade actually needs to trust on chain.
 1. **how much is on the inventory** 
 2. **what it costs right now**

This is an RWA primitive for commodities. QUANTIX does not tokenize the goods themselves, it just tokenizes the **facts about them** (inventory, price, timestamp, signer set) that every B2B raw-material transaction already depends on, and replaces the subsequent discussions about quantities or price-fixing afterwards with an attested on-chain reading.

Built for the CHARLI3 Catalyst Hackathon on `Cardano preprod` within 16.04.2026–19.04.2026.

Features:
- Buyer Application with SQL persistence and Fiori/UI5 frontend build with SAP CAP(https://cap.cloud.sap/docs/)
- ODATANO as CAP Plug-in for on-chain I/O (https://github.com/ODATANO/ODATANO) 
- Forked Charli3 pull-oracle stack (Aiken contracts, Python node, off-chain SDK) to support multi-feed supply-chain data (inventory + price per supplier)
- Custom Aiken order validator reading two distinct AggState UTxOs as reference inputs, enforcing inventory, price, payment, and deadline at execution time

---

## Problem & Thesis

In real-world B2B supply chains, the two numbers a buyer most needs to trust — **what is actually in stock** and **what it costs right now** — are often stored in different systems that drift further apart with every passing minute of the workday. The result is a mess of manual processes, stale data, and disputes:

- **ERP vs. warehouse floor.** Accounting shows 12,000 kg of lithium carbonate; the warehouse just shipped 3,000 kg an hour ago and the movement hasn't been booked yet. Buyers see stale figures and commit to orders that can't be filled, or walk away from stock that's actually available.
- **Price lists vs. market reality.** Commodity prices move on energy costs, FX, freight, and spot shortages. A PDF quote from last week is already wrong; a CSV pulled this morning may not reflect the raw-material index spike from two hours ago.
- **No shared source of truth between buyer and supplier.** Both parties reconcile the same facts from their own systems, email attachments, and phone calls. Disputes over "what was agreed at what price for how much" are resolved by whoever has the better paperwork, not by an attested fact.
- **Every pair reinvents the pipe.** Each buyer–supplier relationship rebuilds the same EDI/API bridge from scratch. There is no neutral channel where an inventory reading is published once and consumable by anyone authorized to see it so integration cost scales with the number of counterparties, not the number of facts.

The result: orders that fail at fulfilment, pricing disputes after the fact, and trust that depends on bilateral integrations rather than on chain verifiable data.

### The QUANTIX Bet

A supplier's own ERP reading, signed by a decentralized oracle quorum and posted on-chain, is a stronger foundation for a buyer's purchase commitment than any of the above. The order contract itself enforces the match at execution time and no one gets to "forget" that inventory was short or that the price had moved.

---

## What it does

A buyer picks a supplier and a quantity of a raw material (e.g. lithium
carbonate in grams) and clicks **Buy**. CAP builds a single atomic Cardano
transaction that — in one step, signed once by the buyer's CIP-30 wallet —
mints a per-order receipt NFT under a Plutus V3 minting policy parameterised
for that supplier. The mint is valid only if the same transaction:

- References the supplier's `inventory` and `price` AggState UTxOs as
  reference inputs (both decoded as `FeedDatum(AggStateVariant(GenericData))`)
- Feeds are still inside their validity window (`valid_through_ms ≥ tx lower
  bound`)
- Inventory feed value ≥ requested grams
- Price feed value ≤ redeemer's `max_price_lovelace`
- Transaction upper bound ≤ redeemer's `deadline_ms`
- One output pays the supplier at least `requested_g × price` lovelace
- CIP-20 metadata label 674 carries `{ c3supply: { grams, order_id } }` so
  the supplier's ERP chain-watcher can decrement inventory precisely

The buyer either succeeds atomically or the Tx doesn't settle. Receipt NFT is the
on-chain proof-of-purchase.

Each supplier exposes a REST ERP mock. Charli3 nodes poll the ERP via the
upstream `generic-api` adapter, aggregate N-of-M signatures, and publish the
signed state as a UTxO on `preprod`. The mint validator never trusts a
buyer-supplied quantity or price — everything comes from an attested oracle
round with threshold signatures.

---

## Architecture

![alt text](/assets/flow.png)

**Development tracks:**

- **Oracle track** 3 oracles on `preprod` (one per supplier), each with a
  shared policy + settings + reward account but two distinct aggregation-state
  UTxOs (`C3AS_inventory`, `C3AS_price`). Required a fork of the upstream
  Charli3 Aiken validator (beacon-token prefix match) and of the Python node
  (multi-feed config + per-feed OdvService).
- **App track** SAP CAP service (`Suppliers`, `Orders`, Actions) using
  ODATANO as a CDS plugin. Suppliers UI shows live inventory/price, a
  Refresh-Feed button that triggers an ODV round end-to-end in ~3s, and a
  Buy button that round-trips through a CIP-30 wallet to an atomic mint Tx.
  My-Buys tab keeps the receipt history.
- **Contract track** Custom `order_mint.ak` Plutus V3 minting policy,
  parameterised with the supplier's `(inventory_policy, inventory_asset,
  price_policy, price_asset, supplier_payment_hash)`, reading both AggStates
  as reference inputs. 12 helper unit tests. Deployed per-supplier as a
  reference script.

---

## Repository layout

```
apps/cap/                     SAP CAP App: Suppliers, Orders, OData actions
packages/shared/              Shared TS types (MintRedeemer, feeds, …)
packages/contracts-ts/        Mint-redeemer encoder + blueprint wrapper
contracts/aiken/              order_mint.ak (Plutus V3) + helpers + tests
services/charli3-bridge/      FastAPI: Blockfrost read + ODV coordinator proxy
services/supplier-erp-mock/   FastAPI: per-supplier ERP mock (3 instances)
services/charli3-fork/        Three git submodules (see "Forks" below)
scripts/                      Deployment scripts
docker-compose.yml            Full local stack
secrets/                      Wallets + node keys (gitignored)
```

---

## Documentation map

Start here, then drill into the subcomponent docs as needed.

**This repo:**

- [Quick start](./QUICK_START.MD) prerequisites, `.env`, local dev boot
- [`.env.example`](./.env.example) full template for the root `.env` — Blockfrost key, per-supplier oracle policies/addresses, node URLs, node mnemonics, supplier payment addresses, order-mint ref-script hashes. Copy to `.env` and fill in everything before booting the stack
- [`contracts/aiken/README.md`](./contracts/aiken/README.md) order-mint Plutus V3 validator, build / test / blueprint publish
- [`services/charli3-bridge/README.md`](./services/charli3-bridge/README.md) on-chain feed read + ODV orchestrator (dual-coordinator routing, tx-chaining)
- [`services/supplier-erp-mock/README.md`](./services/supplier-erp-mock/README.md) per-supplier ERP mock with optional jitter + chain-watch
- [`services/charli3-fork/configs/README.md`](./services/charli3-fork/configs/README.md) 9-container multi-feed node layout, per-supplier oracle deploy sequence

**Forked upstream components** (live under `services/charli3-fork/` as submodules):

- [`charli3-pull-oracle-contracts/README.md`](./services/charli3-fork/charli3-pull-oracle-contracts/README.md) Aiken oracle validator (my `c3-supply/asset-prefix` branch)
- [`charli3-pull-oracle-node/README.md`](./services/charli3-fork/charli3-pull-oracle-node/README.md) Python ODV node (my `c3-supply/multi-feed` branch)
- [`charli3-pull-oracle-sdk/README.md`](./services/charli3-fork/charli3-pull-oracle-sdk/README.md) off-chain SDK (my `c3-supply/multi-aggstate` branch)

---

## Live on `preprod`

Three oracle deployments, each with a shared policy and two distinct
aggregation-state UTxOs.

| Supplier       | Oracle Policy ID                                           | Oracle Address                                                    | Order-Mint Policy ID                                       |
| -------------- | ---------------------------------------------------------- | ----------------------------------------------------------------- | ---------------------------------------------------------- |
| A  Lithium     | `78adae0debdf47708f49b5bae9b3ee5eae053ce7b86874d7947ec622` | `addr_test1wqww3xgvvt09y826qq5xqj8267yvhaq7xprdeeyrhw7a6dq2fszvh` | `409e01760341b2554d6ba7d1ec403c7410c48de0f2d1047df1bee41e` |
| B  Cobalt      | `7e3cc787cd71c68ecc08274cff9596be9e2951d106c29b901dbd081f` | `addr_test1wpk8epwpmshm4d4s3p40p9vkjhmu8e549xvwyl2njjrzkjcthysxc` | `5d418b9d2f5801ec76d05a73167abafb925a5ae68fc6f721d73b47f0` |
| C  Rare-Earth  | `dc6ffa6e4ed0ae23697d5d33e4aa0f4ac020b5c78d94209510a37ed9` | `addr_test1wp7jh3d2drrlexhmckgdjc70spsvuyemt8s77xkk0h59sds9n8kx3` | `c8b55b80d1d10df1f8c86b69b6acb3e805419442f114987edfea2cb7` |

Six live feeds (inventory + price per supplier) populated via ODV rounds with
all 3 node keys.

Per-supplier order-mint policies (Plutus V3) deployed as reference scripts.
Policy IDs are loaded from `.env` as `ORDER_MINT_POLICY_SUPPLIER_{A,B,C}` —
each parameterised with that supplier's `(inventory_policy, inventory_asset,
price_policy, price_asset, supplier_payment_hash)`.

---

## Charli3 extension story

The upstream Charli3 stack was designed for single-feed financial oracles (one
price per deployment). Multi-feed supply-chain data required four connected
changes, all implemented as public forks:

### 1. Aiken validator — asset-name prefix matching

Upstream matches the aggregation-state beacon token by **exact** name `C3AS`,
which limits a deployment to one aggregation state. We replaced the exact match
with a prefix match — `C3AS_inventory` and `C3AS_price` both count, letting a
single policy carry N distinct feeds.

- Fork: [`maxalexweber1/charli3-pull-oracle-contracts`](https://github.com/maxalexweber1/charli3-pull-oracle-contracts) `c3-supply/asset-prefix`
- New helper `find_protocol_token_by_prefix` in `lib/services/protocol_token.ak`
- Follow-up fix (v2): the `RewardAccount` spend branch was still referencing
  the global token name via `spends_one_script_utxo_with_nft`. New helper
  `find_cospent_token_by_prefix` resolves the co-spent AggState token correctly.
- **56 / 56 tests green** (54 original + 2 multi-feed)

### 2. Oracle node — multi-feed runtime

Upstream loads one `FeedConfig` per container. We refactored to a list of
feeds, with per-feed `OdvService` instances keyed by `feed_id`, and added
`/feed/{feed_id}` and `/sign/{feed_id}` endpoints.

- Fork: [`maxalexweber1/charli3-pull-oracle-node`](https://github.com/maxalexweber1/charli3-pull-oracle-node) `c3-supply/multi-feed`
- 9 files touched, +269 / -92 LOC
- Background collection iterates all configured feeds per tick

### 3. Off-chain SDK — asset-name plumbing

Upstream's transaction builder hard-codes `C3AS` when finding the AggState
UTxO. We threaded an optional `aggstate_asset_name` through
`OracleTransactionBuilder` and `find_account_pair`, default preserved for
backward compatibility.

- Fork: [`maxalexweber1/charli3-pull-oracle-sdk`](https://github.com/maxalexweber1/charli3-pull-oracle-sdk) `c3-supply/multi-aggstate`
- 2 files touched, +28 / -5 LOC

## Custom order mint validator (`contracts/aiken/validators/order_mint.ak`)

A Plutus V3 **minting policy** parameterised per supplier:

```aiken
validator order_mint(
  inventory_policy_id:  PolicyId,
  inventory_asset_name: AssetName,  // "C3AS_inventory"
  price_policy_id:      PolicyId,
  price_asset_name:     AssetName,  // "C3AS_price"
  supplier_payment_hash: ByteArray,
)

type MintRedeemer {
  requested_g:        Int,
  max_price_lovelace: Int,
  deadline_ms:        Int,
}
```

A mint under this policy is valid only if the same transaction:

- References the supplier's inventory and price AggState UTxOs (matched by
  policy + asset name), both decoded as `FeedDatum(AggStateVariant(GenericData
  price_map))`
- Feeds are still inside their validity window (`valid_through_ms ≥ tx lower
  bound`)
- `inv_feed.value / 1_000_000 ≥ requested_g` — Charli3 stores values scaled
  by 1e6; we descale before comparing against the ERP's semantic units
- `price_feed.value ≤ max_price_lovelace` (scaled value passed through as-is)
- `tx upper_bound ≤ deadline_ms`
- Some output pays `supplier_payment_hash` at least
  `requested_g × price_feed.value / 1_000_000` lovelace

No spending validator, no locked escrow UTxO, no second transaction. The
buyer's own UTxOs drive the witness and either the whole thing settles
atomically or nothing happens.

`order_utils.ak` decodes the real `Charli3PriceData` / `FeedDatum` CBOR shape
seen on-chain — 12 unit tests cover happy path, multi-asset selection, missing
asset, empty reference inputs, and non-AggState datum rejection.


---

## Forks (git submodules under `services/charli3-fork/`)

| Repo | Branch | Purpose |
| --- | --- | --- |
| [`charli3-pull-oracle-contracts`](https://github.com/maxalexweber1/charli3-pull-oracle-contracts) | `c3-supply/asset-prefix` | Prefix-match AggState beacon + cospent-fix |
| [`charli3-pull-oracle-node`](https://github.com/maxalexweber1/charli3-pull-oracle-node) | `c3-supply/multi-feed` | Multi-feed runtime + 5 operational patches |
| [`charli3-pull-oracle-sdk`](https://github.com/maxalexweber1/charli3-pull-oracle-sdk) | `c3-supply/multi-aggstate` | `aggstate_asset_name` kwarg in tx builder |

All three are candidates for upstream PRs to `Charli3-Official/*` after the
hackathon if the maintainers are open to the multi-feed use case.

---

## Tech stack

- **Framework:** SAP CAP (Node.js, CDS), Fiori/UI5 frontend
- **Cardano I/O:** [`@odatano/core`](https://www.npmjs.com/package/@odatano/core)
  CDS plugin (Blockfrost + Buildooor / CSL tx builders)
- **Oracle:** Charli3 pull-oracle stack (Aiken contracts + Python node +
  off-chain SDK), all three repos forked
- **Contract:** Custom Plutus V3 validator in Aiken
- **Data sources:** FastAPI ERP mocks (Python 3.11)
- **Local stack:** Docker Compose (9 oracle nodes + 3 ERPs + bridge + CAP)
- **Wallet interaction:** CIP-30 (Eternl / Lace)
---

## Demo

- Screenshots of the full Buy + Refresh-Feed flow: [DEMO.md](./DEMO.md)
- Buy-flow walkthrough video: [youtu.be/g3_FExeFyH4](https://youtu.be/g3_FExeFyH4)

- Example Oracle Address with 4 Tokens: https://preprod.cardanoscan.io/address/addr_test1wqww3xgvvt09y826qq5xqj8267yvhaq7xprdeeyrhw7a6dq2fszvh

- Example Order Transaction with Mint & Metadata: https://preprod.cardanoscan.io/transaction/b901a4b28b8ec124d6db601891c694c346a5f69371104e09f5e3169105722c21
```

---

## Disclaimer 

1. Tis a hackathon submission, not production quality. Do not reuse without a thorough audit and refactoring. The code in this repo is intended to demonstrate the feasibility and value of the core idea - on-chain attested inventory and price feeds as a primitive for supply-chain commerce rather than to serve as a production-ready implementation. The architecture, code structure, and security assumptions would all need to be revisited for a real deployment. In particular, the custom minting policy and the oracle's trust  model are simplified for the sake of the hackathon and would require significant hardening for production use.

2. AI was used in the development of this project and may have contributed to some of the code, documentation, and commit messages. The project was developed by Max Weber with the assistance of Claude Opus 4.7 for code generation and documentation.

## License 
MIT License: [MIT](./LICENSE)
