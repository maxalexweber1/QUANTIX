using { c3.supply as db } from '../db/schema';

service OrdersService @(path: '/odata/v4/orders-service') {

  entity Orders    as projection on db.Orders;
  entity Suppliers as projection on db.Suppliers;

  // ── Actions on Orders ─────────────────────────────────────────────

  /**
   * 1-step atomic buy via the mint-based validator. Creates an Order
   * server-side (status=executing), builds a mint+pay tx that references
   * both oracle feeds, atomically pays supplier `requestedG × live_price`,
   * and returns the unsigned CBOR for wallet signing.
   */
  action buyFromSupplier(
    supplierId    : UUID,
    requestedG    : Integer,
    buyerAddress  : String,   // CIP-30 wallet bech32 address
    buyerVkh      : String    // 56-hex payment key hash (for required_signers)
  ) returns {
    orderId          : UUID;
    unsignedCborHex  : String;
    signingRequestId : UUID;
    paidLovelace     : Integer64;
    livePrice        : Integer64;
    receiptAssetName : String;
    fee              : Integer64;
  };

  /**
   * Submit a CIP-30-signed witness for the buy mint tx. Verifies + submits
   * via ODATANO, flips order status executing → executed + stores tx hash.
   */
  action submitSigned(
    orderId          : UUID,
    signingRequestId : UUID,
    signedTxCbor     : String    // wallet witness-set CBOR (partial sign)
  ) returns {
    txHash    : String;
    newStatus : String;
  };

  // ── Actions on Suppliers ──────────────────────────────────────────

  /**
   * Trigger a Charli3 ODV update for one of the supplier's two feeds.
   * Bridge runs the aggregation + signing + submit cycle and returns
   * the new C3AS tx hash. Blocking — may take 20–40s on preprod.
   */
  action triggerOdvUpdate(
    supplierId : UUID,
    feed       : String   // 'inventory' | 'price'
  ) returns {
    newC3asTxHash : String;
    value         : Integer64;   // grams (inventory) OR lovelace/gram (price)
    timestampMs   : String;      // string so UI can format without Integer64 precision loss
    status        : String;      // 'submitted' | 'not_yet_expired' | 'collected' | ...
    note          : String;      // human-readable explanation from bridge
  };
}
