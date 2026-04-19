// C3-Supply — Order MINT Validator (1-step atomic buy)
//
// A minting policy parameterised per supplier. A mint of any quantity
// under this policy is valid ONLY IF the same tx atomically:
//   (a) references the supplier's inventory and price AggState UTxOs,
//   (b) both decode as FeedDatum (AggStateVariant(GenericData price_map)),
//   (c) feeds are still valid (valid_through_ms >= tx lower bound),
//   (d) inv_feed.value / 1_000_000 >= redeemer.requested_g,
//   (e) price_feed.value <= redeemer.max_price_lovelace,
//   (f) tx upper_bound <= redeemer.deadline_ms,
//   (g) some output pays supplier_payment_hash >=
//       requested_g * price_feed.value / 1_000_000 lovelace.
//
// Charli3's generic-api adapter normalizes feed values by x1_000_000
// (6-decimal precision). Raw ERP values: 10000g and 12000000 lov/g are
// stored on-chain as 10_000_000_000 and 12_000_000_000_000. We divide
// by the scale before comparing against redeemer fields, which are in
// the ERP's semantic units.

use aiken/interval.{Finite, IntervalBound}
use cardano/assets.{AssetName, PolicyId}
use cardano/transaction.{Transaction}
use order_utils.{find_and_decode_feed, output_pays_at_least}

pub type MintRedeemer {
  requested_g: Int,
  max_price_lovelace: Int,
  deadline_ms: Int,
}

validator order_mint(
  inventory_policy_id: PolicyId,
  inventory_asset_name: AssetName,
  price_policy_id: PolicyId,
  price_asset_name: AssetName,
  supplier_payment_hash: ByteArray,
) {
  mint(redeemer: MintRedeemer, _own_policy: PolicyId, tx: Transaction) {
    expect Some(inv_feed) =
      find_and_decode_feed(
        tx.reference_inputs,
        inventory_policy_id,
        inventory_asset_name,
      )
    expect Some(price_feed) =
      find_and_decode_feed(
        tx.reference_inputs,
        price_policy_id,
        price_asset_name,
      )

    let IntervalBound { bound_type: lower_bt, .. } =
      tx.validity_range.lower_bound
    let IntervalBound { bound_type: upper_bt, .. } =
      tx.validity_range.upper_bound
    expect Finite(lower_ms) = lower_bt
    expect Finite(upper_ms) = upper_bt

    // Charli3 adapter stores values with 1e6 precision on-chain.
    let scale = 1_000_000
    let inv_value_unit = inv_feed.value / scale
    let price_value_unit = price_feed.value / scale

    let feeds_valid =
      inv_feed.valid_through_ms >= lower_ms && price_feed.valid_through_ms >= lower_ms
    let before_deadline = upper_ms <= redeemer.deadline_ms
    let inv_sufficient = inv_value_unit >= redeemer.requested_g
    let price_ok = price_value_unit <= redeemer.max_price_lovelace
    let required_pay = redeemer.requested_g * price_value_unit
    let supplier_paid =
      output_pays_at_least(tx.outputs, supplier_payment_hash, required_pay)

    feeds_valid && before_deadline && inv_sufficient && price_ok && supplier_paid
  }

  else(_) {
    fail
  }
}
