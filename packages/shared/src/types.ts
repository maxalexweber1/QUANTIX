/**
 * Types shared between CAP service, contracts-ts encoders, and
 * charli3-bridge client code.
 */

export type Bech32Address = string;
export type Hex = string;
export type PolicyId = string;  // 56 hex chars (28 bytes)
export type AssetName = string; // <= 64 hex chars (<= 32 bytes)
export type TxHash = Hex;       // 64 hex chars (32 bytes)
export type VKeyHash = Hex;     // 56 hex chars (28 bytes)

/**
 * Parameters the mint-based order validator is parameterised by.
 * Two oracle coords (inventory + price) plus the supplier's payment key
 * hash (the mint policy verifies the payout output pays this vkh).
 */
export interface OrderMintScriptParams {
  inventoryPolicyId: PolicyId;
  inventoryAssetName: AssetName;
  pricePolicyId: PolicyId;
  priceAssetName: AssetName;
  supplierPaymentHash: VKeyHash;
}

/**
 * Redeemer for the minting-policy validator.
 * Encoded as Constr(0, [int requestedG, int maxPriceLovelace, int deadlineMs]).
 */
export interface MintRedeemer {
  requestedG: bigint;
  maxPriceLovelace: bigint;
  deadlineMs: bigint;
}

/**
 * A single Charli3 C3AS feed value as returned by the bridge.
 * Each oracle (inventory or price) is its own feed with its own
 * policy ID, its own ref input, and its own numeric value.
 */
export interface SingleFeed {
  /** Numeric value carried by this feed (grams OR lovelace/gram). */
  value: number;
  timestampMs: string;       // bigint as string
  validThroughMs: string;
  refTxHash: TxHash;
  refOutputIndex: number;
}

/**
 * Convenience bundle: both of a supplier's feeds in one object.
 * CAP calls the bridge twice in parallel to assemble this.
 */
export interface SupplierFeeds {
  inventory: SingleFeed;
  price: SingleFeed;
}

export interface OdvUpdateRequest {
  policy_id: PolicyId;
}

export interface OdvUpdateResponse {
  newC3asTxHash: TxHash;
  value: number;             // grams or lovelace/gram depending on feed
  timestampMs: string;
}

export interface BridgeHealth {
  status: 'ok' | 'degraded' | 'down';
  feeds: Array<{
    policyId: PolicyId;
    lastUpdateMs: string | null;
    nodeStatus: 'up' | 'down' | 'unknown';
  }>;
}
