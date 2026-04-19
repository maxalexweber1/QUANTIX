import type { MintRedeemer } from '@c3-supply/shared';

/**
 * PlutusData JSON in the "detailed schema" expected by CSL and Buildooor
 * (after normalizeConstructorKey). Using `constructor` here; ODATANO
 * converts to `constr` internally for Buildooor.
 */
export type PlutusDataJson =
  | { int: number | string }
  | { bytes: string }
  | { list: PlutusDataJson[] }
  | { map: Array<{ k: PlutusDataJson; v: PlutusDataJson }> }
  | { constructor: number; fields: PlutusDataJson[] };

/**
 * Encode mint-policy redeemer as PlutusData:
 *   Constr(0, [int requestedG, int maxPriceLovelace, int deadlineMs])
 */
export function encodeMintRedeemer(r: MintRedeemer): PlutusDataJson {
  return {
    constructor: 0,
    fields: [
      { int: r.requestedG.toString() },
      { int: r.maxPriceLovelace.toString() },
      { int: r.deadlineMs.toString() },
    ],
  };
}

export function encodeMintRedeemerJson(r: MintRedeemer): string {
  return JSON.stringify(encodeMintRedeemer(r));
}
