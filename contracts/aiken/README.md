# C3-Supply Order Mint Validator

Aiken Plutus V3 **minting policy** that gates the 1-step atomic buy flow.
A mint under this policy is valid only if the same transaction references
both of the supplier's Charli3 AggState UTxOs (inventory + price) and pays
the supplier the correct lovelace amount. See the root README for the full
semantics.

## Commands

```bash
aiken check          # typecheck + run helper tests
aiken build          # compile to UPLC, write plutus.json

# Publish the blueprint to the TS package so getOrderMintValidatorCbor()
# can resolve it at runtime:
cp plutus.json ../../packages/contracts-ts/src/order_mint.plutus.json
```

## Layout

- `validators/order_mint.ak` — the mint validator
- `validators/order_mint_tests.ak` — helper tests (12)
- `lib/order_utils.ak` — `find_and_decode_feed` + `output_pays_at_least`
  + Charli3 datum types

## Parameters

The mint validator takes five UPLC-applied parameters:

1. `inventory_policy_id: PolicyId`
2. `inventory_asset_name: AssetName`
3. `price_policy_id: PolicyId`
4. `price_asset_name: AssetName`
5. `supplier_payment_hash: ByteArray` — vkh the payout output must pay

Apply them at deploy time via `aiken blueprint apply` (see
`scripts/deploy-order-mint-refscript.py`) or at Tx-build time through
ODATANO's `BuildMintTransaction.scriptParamsJson`.
