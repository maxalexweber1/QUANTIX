namespace c3.supply;

using { cuid, managed } from '@sap/cds/common';

/**
 * A physical supplier whose inventory + unit price are BOTH verified
 * on-chain via TWO Charli3 feeds pulling from the supplier's ERP:
 *   - inventory feed: grams available (Rate.price = value_g)
 *   - price feed:     lovelace per gram (Rate.price = unit_price_lovelace)
 *
 * Each feed is its own C3AS UTxO with its own policy ID. The order
 * validator references BOTH as read-only inputs during execute.
 */
entity Suppliers : cuid, managed {
  name                       : String(120) not null;
  paymentAddress             : String(120) not null;   // Bech32 address (preprod)

  // ── Inventory oracle ──
  inventoryOraclePolicyId    : String(56)  not null;   // Policy ID of the inventory C3AS feed
  inventoryOracleAssetName   : String(64);             // Asset name hex (often empty)

  // ── Price oracle ──
  priceOraclePolicyId        : String(56)  not null;   // Policy ID of the price C3AS feed
  priceOracleAssetName       : String(64);

  productCode                : String(32)  not null;   // ERP SKU code
  erpUrl                     : String(200);            // ERP endpoint (for UI "update" button)

  // Live-derived values (populated by custom handler from bridge)
  virtual currentInventoryG       : Integer;
  virtual currentUnitPriceLovelace: Integer64;
  virtual inventoryValidThroughMs : Integer64;
  virtual inventoryTimestampMs    : Integer64;
  virtual priceValidThroughMs     : Integer64;
  virtual priceTimestampMs        : Integer64;

  // Outgoing association
  orders : Association to many Orders on orders.supplier = $self;
}

type OrderStatus : String enum {
  executing;   // Buy tx built + signing-request issued, awaiting wallet signature
  executed;    // Buy tx submitted on-chain successfully
  failed;      // Wallet declined or submit failed
}

/**
 * A buy-history record. Each row is one 1-step buyFromSupplier mint tx:
 * buyer pays (requestedG * pricePerGram) lovelace to the supplier + mints
 * a per-order receipt NFT in a single atomic Cardano transaction.
 */
entity Orders : cuid, managed {
  buyer             : String(120) not null;   // Buyer Bech32 address
  supplier          : Association to Suppliers not null;
  requestedG        : Integer     not null;   // Quantity bought in grams
  pricePerGram      : Integer64;              // Lovelace/gram at buy time (live oracle feed)
  paidLovelace      : Integer64;              // Total lovelace paid = requestedG * pricePerGram
  status            : OrderStatus not null default 'executing';
  executeTxHash     : String(64);             // Preprod tx hash of the buy mint tx
}
