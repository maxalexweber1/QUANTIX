// ─────────────────────────────────────────────────────────────────────
// C3-Supply — Order Mint Validator — Helper Unit Tests
//
// Covers the helpers used by order_mint.ak:
//   - output_pays_at_least
//   - find_and_decode_feed against the real Charli3 AggState datum shape
//     (AggStateVariant(GenericData(price_map)))
// ─────────────────────────────────────────────────────────────────────

use cardano/address.{Address, Script, VerificationKey}
use cardano/assets.{from_asset, from_lovelace, merge}
use cardano/transaction.{InlineDatum, Input, NoDatum, Output, OutputReference}

use order_utils.{
  AggStateVariant, Charli3OracleDatum, FeedDatum, GenericData,
  OracleSettingsVariant, find_and_decode_feed, output_pays_at_least,
}

// ── output_pays_at_least ─────────────────────────────────────────────

fn vk_output(hash: ByteArray, lovelace: Int) -> Output {
  Output {
    address: Address {
      payment_credential: VerificationKey(hash),
      stake_credential: None,
    },
    value: from_lovelace(lovelace),
    datum: NoDatum,
    reference_script: None,
  }
}

fn script_output(hash: ByteArray, lovelace: Int) -> Output {
  Output {
    address: Address {
      payment_credential: Script(hash),
      stake_credential: None,
    },
    value: from_lovelace(lovelace),
    datum: NoDatum,
    reference_script: None,
  }
}

test output_pays_at_least_accepts_exact_match() {
  let buyer = #"aabbcc"
  output_pays_at_least([vk_output(buyer, 1_000_000)], buyer, 1_000_000)
}

test output_pays_at_least_accepts_overpay() {
  let buyer = #"aabbcc"
  output_pays_at_least([vk_output(buyer, 2_000_000)], buyer, 1_000_000)
}

test output_pays_at_least_rejects_underpay() {
  let buyer = #"aabbcc"
  !output_pays_at_least([vk_output(buyer, 500_000)], buyer, 1_000_000)
}

test output_pays_at_least_rejects_wrong_recipient() {
  let buyer = #"aabbcc"
  let other = #"112233"
  !output_pays_at_least([vk_output(other, 1_000_000)], buyer, 1_000_000)
}

test output_pays_at_least_rejects_script_credential() {
  let buyer = #"aabbcc"
  !output_pays_at_least([script_output(buyer, 1_000_000)], buyer, 1_000_000)
}

test output_pays_at_least_finds_in_list() {
  let buyer = #"aabbcc"
  let other = #"112233"
  let outs =
    [
      vk_output(other, 500_000),
      script_output(buyer, 500_000),
      vk_output(buyer, 1_500_000),
    ]
  output_pays_at_least(outs, buyer, 1_000_000)
}

test output_pays_at_least_rejects_empty_list() {
  !output_pays_at_least([], #"aabbcc", 1_000_000)
}

// ── find_and_decode_feed against real Charli3 AggState shape ─────────

// Mimic Supplier-A inventory oracle on Preprod.
const mock_policy_id = #"78adae0debdf47708f49b5bae9b3ee5eae053ce7b86874d7947ec622"

const mock_asset_name = "C3AS_inventory"

const mock_script_hash =
  #"c1cee8b08c5af148ab400a1809575e2332fa07830da9c9083bbdef74"

fn aggstate_ref_input(
  policy_id: ByteArray,
  asset_name: ByteArray,
  value: Int,
  created_at: Int,
  valid_until: Int,
) -> Input {
  let datum =
    AggStateVariant(
      GenericData { price_map: [Pair(0, value), Pair(1, created_at), Pair(2, valid_until)] },
    )
  Input {
    output_reference: OutputReference {
      transaction_id: #"aabbccddeeff00112233445566778899aabbccddeeff00112233445566778899",
      output_index: 1,
    },
    output: Output {
      address: Address {
        payment_credential: Script(mock_script_hash),
        stake_credential: None,
      },
      value: merge(from_lovelace(2_000_000), from_asset(policy_id, asset_name, 1)),
      datum: InlineDatum(datum),
      reference_script: None,
    },
  }
}

test decode_feed_happy_path() {
  let refs =
    [aggstate_ref_input(mock_policy_id, mock_asset_name, 10_000, 1_776_434_046_000, 1_776_434_646_000)]
  expect Some(feed) = find_and_decode_feed(refs, mock_policy_id, mock_asset_name)
  feed == FeedDatum {
    value: 10_000,
    timestamp_ms: 1_776_434_046_000,
    valid_through_ms: 1_776_434_646_000,
  }
}

test decode_feed_picks_matching_asset_from_two() {
  let inv_input =
    aggstate_ref_input(mock_policy_id, "C3AS_inventory", 50_000, 100, 200)
  let price_input =
    aggstate_ref_input(mock_policy_id, "C3AS_price", 850, 100, 200)
  let refs = [price_input, inv_input]
  expect Some(feed) = find_and_decode_feed(refs, mock_policy_id, "C3AS_inventory")
  feed.value == 50_000
}

test decode_feed_returns_none_when_asset_missing() {
  let refs =
    [aggstate_ref_input(mock_policy_id, "C3AS_price", 850, 100, 200)]
  find_and_decode_feed(refs, mock_policy_id, "C3AS_inventory") == None
}

test decode_feed_returns_none_on_empty_refs() {
  find_and_decode_feed([], mock_policy_id, mock_asset_name) == None
}

test decode_feed_rejects_non_aggstate_variant() {
  // Attach an OracleSettings-shaped datum to what otherwise looks like an
  // AggState asset. The decoder should return None (not crash) because
  // the outer variant isn't AggStateVariant.
  let settings_datum: Data = OracleSettingsVariant(#"")
  let input =
    Input {
      output_reference: OutputReference {
        transaction_id: #"0000000000000000000000000000000000000000000000000000000000000000",
        output_index: 0,
      },
      output: Output {
        address: Address {
          payment_credential: Script(mock_script_hash),
          stake_credential: None,
        },
        value: merge(from_lovelace(2_000_000), from_asset(mock_policy_id, mock_asset_name, 1)),
        datum: InlineDatum(settings_datum),
        reference_script: None,
      },
    }
  find_and_decode_feed([input], mock_policy_id, mock_asset_name) == None
}
