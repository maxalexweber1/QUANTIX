// Pure helpers + shared types for the order mint validator.
// Kept in lib/ so both validator and tests can import them.

use aiken/collection/list
use aiken/collection/pairs
use cardano/address.{VerificationKey}
use cardano/assets.{AssetName, PolicyId, lovelace_of, quantity_of}
use cardano/transaction.{InlineDatum, Input, Output}

/// Charli3's PriceData variants. Variant order MUST match the Charli3
/// validator (Charli3-Official/charli3-pull-oracle-contracts
/// lib/core/datum.ak) so CBOR Constr tags align:
///   SharedData = Constr 0, ExtendedData = Constr 1,
///   GenericData = Constr 2 carrying a price_map: Pairs<Int, Int>.
pub type Charli3PriceData {
  SharedData
  ExtendedData
  GenericData { price_map: Pairs<Int, Int> }
}

/// Charli3's OracleDatum envelope. AggState is variant 0 and wraps
/// PriceData. The OracleSettings / RewardAccount variants use `Data`
/// placeholders — we never see those at AggState UTxOs (filtered by
/// asset name) but the variants must exist for Constr-tag alignment.
pub type Charli3OracleDatum {
  AggStateVariant(Charli3PriceData)
  OracleSettingsVariant(Data)
  RewardAccountVariant(Data)
}

/// Unpacked feed value with timestamps — consumer-facing shape. The
/// validator only needs the three price_map entries: value / created /
/// expiration.
pub type FeedDatum {
  value: Int,
  timestamp_ms: Int,
  valid_through_ms: Int,
}

/// Scan reference inputs for a UTxO carrying (policy_id, asset_name) qty=1,
/// decode its inline datum as a Charli3 AggState, and unpack the price_map
/// into a FeedDatum. Returns None if no such input exists or the datum
/// shape is not an AggState with a non-empty price_map.
pub fn find_and_decode_feed(
  refs: List<Input>,
  pid: PolicyId,
  an: AssetName,
) -> Option<FeedDatum> {
  when refs is {
    [] -> None
    [head, ..rest] ->
      if quantity_of(head.output.value, pid, an) == 1 {
        when head.output.datum is {
          InlineDatum(d) -> decode_aggstate_feed(d)
          _ -> None
        }
      } else {
        find_and_decode_feed(rest, pid, an)
      }
  }
}

fn decode_aggstate_feed(d: Data) -> Option<FeedDatum> {
  expect oracle_datum: Charli3OracleDatum = d
  when oracle_datum is {
    AggStateVariant(price_data) ->
      when price_data is {
        GenericData { price_map } -> {
          expect Some(v) = pairs.get_first(price_map, 0)
          expect Some(c) = pairs.get_first(price_map, 1)
          expect Some(e) = pairs.get_first(price_map, 2)
          Some(FeedDatum { value: v, timestamp_ms: c, valid_through_ms: e })
        }
        _ -> None
      }
    _ -> None
  }
}

pub fn output_pays_at_least(
  outputs: List<Output>,
  pay_to: ByteArray,
  lovelace: Int,
) -> Bool {
  list.any(
    outputs,
    fn(o) {
      when o.address.payment_credential is {
        VerificationKey(h) -> h == pay_to && lovelace_of(o.value) >= lovelace
        _ -> False
      }
    },
  )
}
