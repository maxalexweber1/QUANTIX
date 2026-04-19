import type { OrderMintScriptParams } from '@c3-supply/shared';

const toHex = (s: string): string => Buffer.from(s, 'utf8').toString('hex');
const isHex = (s: string): boolean => /^[0-9a-fA-F]*$/.test(s) && s.length % 2 === 0;
const assetNameHex = (s: string | undefined): string =>
  !s ? '' : isHex(s) ? s : toHex(s);

/**
 * Params for the mint-based validator — 5 entries in the exact order
 * the Aiken `order_mint` validator expects:
 *   [inv_policy, inv_asset, price_policy, price_asset, supplier_vkh]
 *
 * ODATANO expects PlutusData `bytes` as hex. Policy IDs arrive hex-encoded
 * already (28 bytes = 56 hex chars). Asset names come in as human-readable
 * strings (e.g. "C3AS_inventory") so we UTF-8 → hex them here.
 */
export function buildOrderMintScriptParamsJson(p: OrderMintScriptParams): string {
  return JSON.stringify([
    { bytes: p.inventoryPolicyId },
    { bytes: assetNameHex(p.inventoryAssetName) },
    { bytes: p.pricePolicyId },
    { bytes: assetNameHex(p.priceAssetName) },
    { bytes: p.supplierPaymentHash },
  ]);
}

/**
 * Return the unapplied order-mint validator CBOR hex. Resolution order:
 *   1. ORDER_MINT_VALIDATOR_CBOR env var (quick override for dev/tests)
 *   2. src/order_mint.plutus.json (Aiken blueprint, validators[].compiledCode)
 *
 * The caller passes this to ODATANO's BuildMintTransaction as
 * `mintingPolicyScript`, together with buildOrderMintScriptParamsJson(...).
 */
export function getOrderMintValidatorCbor(): string {
  const fromEnv = process.env.ORDER_MINT_VALIDATOR_CBOR;
  if (fromEnv) return fromEnv;
  try {
    const blueprint = require('./order_mint.plutus.json') as {
      validators?: Array<{ title?: string; compiledCode?: string }>;
    };
    const found = blueprint.validators?.find(
      (v) => v.title === 'order_mint.order_mint.mint'
    );
    if (found?.compiledCode) return found.compiledCode;
  } catch {
    // blueprint not yet exported — fall through to error
  }
  throw new Error(
    `ORDER_MINT_VALIDATOR_CBOR not set and no matching validator in ` +
    `order_mint.plutus.json. Run \`aiken build\` in contracts/aiken and ` +
    `copy plutus.json to packages/contracts-ts/src/order_mint.plutus.json, ` +
    `or set the env var.`
  );
}
