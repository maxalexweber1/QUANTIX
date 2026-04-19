const cds = require('@sap/cds');
const {
  encodeMintRedeemerJson,
  buildOrderMintScriptParamsJson,
  getOrderMintValidatorCbor,
} = require('@c3-supply/contracts-ts');

const BRIDGE_URL = process.env.BRIDGE_URL || 'http://localhost:8000';

async function fetchFeed(policyId, assetName) {
  if (!policyId || !assetName) return null;
  try {
    const res = await fetch(`${BRIDGE_URL}/feeds/${policyId}/${assetName}`);
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  }
}

module.exports = cds.service.impl(async function () {
  const { Orders, Suppliers } = this.entities;

  const getCardanoTx = () => cds.connect.to('CardanoTransactionService');
  const getCardanoSign = () => cds.connect.to('CardanoSignService');

  // Build → CreateSigningRequest → return {unsignedCborHex, signingRequestId, ...meta}
  async function buildAndCreateSigningRequest(buildAction, payload, message) {
    const cardanoTx = await getCardanoTx();
    const cardanoSign = await getCardanoSign();
    const build = await cardanoTx.send(buildAction, payload);
    const sr = await cardanoSign.send('CreateSigningRequest', {
      buildId: build.id,
      message,
    });
    return { build, sr };
  }

  this.after('READ', Suppliers, async (rows) => {
    const list = Array.isArray(rows) ? rows : [rows];
    await Promise.all(
      list.map(async (s) => {
        const [inv, price] = await Promise.all([
          fetchFeed(s.inventoryOraclePolicyId, s.inventoryOracleAssetName),
          fetchFeed(s.priceOraclePolicyId, s.priceOracleAssetName),
        ]);
        // Charli3 stores feed values scaled by 1e6 (lovelace-precision
        // convention). Our ERP reports ints (grams, lovelace/gram) that
        // we want to surface 1:1 in the UI -- so divide by 1e6 here.
        if (inv) {
          Object.assign(s, {
            currentInventoryG: Math.round(inv.value / 1_000_000),
            inventoryValidThroughMs: inv.validThroughMs,
            inventoryTimestampMs: inv.timestampMs,
          });
        }
        if (price) {
          Object.assign(s, {
            currentUnitPriceLovelace: String(Math.round(price.value / 1_000_000)),
            priceValidThroughMs: price.validThroughMs,
            priceTimestampMs: price.timestampMs,
          });
        }
      })
    );
  });

  // Buy horizon: the on-chain validator checks `upper_ms <= deadline_ms`,
  // where `upper_ms` is the tx validity-range upper bound. ODATANO /
  // buildooor set that to ~3h ahead by default, so a 10-minute deadline
  // guarantees a rejection. 6h gives us headroom for any realistic
  // wallet-sign + submit latency.
  const BUY_DEADLINE_MS = 6 * 60 * 60 * 1000;

  this.on('buyFromSupplier', async (req) => {
    const { supplierId, requestedG, buyerAddress, buyerVkh } = req.data;
    if (!requestedG || requestedG <= 0) return req.error(400, 'requestedG must be positive');
    if (!buyerAddress || !buyerVkh) return req.error(400, 'buyerAddress + buyerVkh required');

    const supplier = await SELECT.one.from(Suppliers).where({ ID: supplierId });
    if (!supplier) return req.error(404, `Supplier ${supplierId} not found`);

    const [invFeed, priceFeed] = await Promise.all([
      fetchFeed(supplier.inventoryOraclePolicyId, supplier.inventoryOracleAssetName),
      fetchFeed(supplier.priceOraclePolicyId, supplier.priceOracleAssetName),
    ]);
    if (!invFeed) return req.error(503, 'Inventory feed unavailable — refresh and retry');
    if (!priceFeed) return req.error(503, 'Price feed unavailable — refresh and retry');
    // Charli3 on-chain feed values are stored scaled by 1e6. Scale back
    // to the semantic unit (grams for inventory, lovelace/g for price)
    // for user-facing comparisons and the actual payment amount.
    const SCALE = 1_000_000n;
    const availableG = BigInt(invFeed.value) / SCALE;
    if (availableG < BigInt(requestedG)) {
      return req.error(400, `Insufficient inventory: ${availableG} g available, ${requestedG} g requested`);
    }

    const cardanoTx = await getCardanoTx();
    const { paymentKeyHash: supplierVkh } = await cardanoTx.send('ExtractPaymentKeyHash', {
      address: supplier.paymentAddress,
    });

    const orderId = cds.utils.uuid();
    // livePrice is scaled (1e6) -- feed redeemer + on-chain validator
    // both read this scaled value; only the actual payment needs
    // descaling.
    const livePrice = BigInt(priceFeed.value);
    const livePriceUnit = livePrice / SCALE; // lovelace/gram
    const paidLovelace = BigInt(requestedG) * livePriceUnit;
    const deadlineMs = BigInt(Date.now() + BUY_DEADLINE_MS);
    const receiptAssetName = orderId.replace(/-/g, '');   // 32 hex chars = 16 bytes

    const mintPolicyId = deriveMintPolicyId(supplier.ID);
    const assetUnit = mintPolicyId + receiptAssetName;

    // Tx-validity bounds. Set explicitly so the mint-validator's
    // `upper_ms <= deadline_ms` check is deterministic and the tx doesn't
    // depend on ODATANO's generic defaults.
    const nowMs = Date.now();
    const validityStartMs = String(nowMs - 120_000);
    const validityEndMs = String(nowMs + 60 * 60 * 1000);

    // CIP-20 label 674. Supplier ERP's chain-watch parses
    // json_metadata[674].c3supply.grams for precise inventory decrement
    // (otherwise it falls back to `lovelace_paid / current_price`, which
    // drifts under jitter). Requires @odatano/core >= 1.7.2 — earlier
    // versions either drop the field (1.7.0) or fail submit verification
    // on the cardano-ledger-ts 0.5.1 aux-data parser bug (1.7.1). 1.7.2
    // routes verification through CSL.FixedTransaction which bypasses
    // the buggy parser entirely.
    const metadataJson = JSON.stringify({
      "674": {
        "c3supply": {
          "grams": Number(requestedG),
          "order_id": orderId,
        },
        "msg": [`C3-Supply buy: ${requestedG}g ${supplier.productCode} @ ${(Number(livePriceUnit) / 1_000_000).toFixed(2)} ADA/g`],
      },
    });

    const { build, sr } = await buildAndCreateSigningRequest('BuildMintTransaction', {
      senderAddress: buyerAddress,
      recipientAddress: supplier.paymentAddress,   // supplier gets payment + receipt NFT
      lovelaceAmount: paidLovelace.toString(),
      mintActionsJson: JSON.stringify([{ assetUnit, quantity: '1' }]),
      mintingPolicyScript: getOrderMintValidatorCbor(),
      scriptParamsJson: buildOrderMintScriptParamsJson({
        inventoryPolicyId: supplier.inventoryOraclePolicyId,
        inventoryAssetName: supplier.inventoryOracleAssetName || '',
        pricePolicyId: supplier.priceOraclePolicyId,
        priceAssetName: supplier.priceOracleAssetName || '',
        supplierPaymentHash: supplierVkh,
      }),
      mintRedeemerJson: encodeMintRedeemerJson({
        requestedG: BigInt(requestedG),
        maxPriceLovelace: livePriceUnit,
        deadlineMs,
      }),
      changeAddress: buyerAddress,
      requiredSignersJson: JSON.stringify([buyerVkh]),
      referenceInputsJson: JSON.stringify([
        { txHash: invFeed.refTxHash, outputIndex: invFeed.refOutputIndex },
        { txHash: priceFeed.refTxHash, outputIndex: priceFeed.refOutputIndex },
      ]),
      validityStartMs,
      validityEndMs,
      metadataJson,
    }, `Buy ${requestedG}g from ${supplier.name}`);

    await INSERT.into(Orders).entries({
      ID: orderId,
      buyer: buyerAddress,
      supplier_ID: supplierId,
      requestedG,
      pricePerGram: String(livePriceUnit),
      paidLovelace: paidLovelace.toString(),
      status: 'executing',
    });

    return {
      orderId,
      unsignedCborHex: sr.unsignedTxCbor,
      signingRequestId: sr.id,
      paidLovelace: Number(paidLovelace),
      livePrice: Number(livePriceUnit),
      receiptAssetName,
      fee: Number(build.fee ?? 0),
    };
  });

  function deriveMintPolicyId(supplierId) {
    // Env-vars populated by scripts/deploy-order-mint-refscript.py per supplier.
    const letter = supplierId.endsWith('1') ? 'A' : supplierId.endsWith('2') ? 'B' : 'C';
    const id = process.env[`ORDER_MINT_POLICY_SUPPLIER_${letter}`];
    if (!id) {
      throw new Error(
        `ORDER_MINT_POLICY_SUPPLIER_${letter} not set in .env — ` +
        `deploy the mint ref-script first via scripts/deploy-order-mint-refscript.py`
      );
    }
    return id;
  }

  this.on('submitSigned', async (req) => {
    const { orderId, signingRequestId, signedTxCbor } = req.data;

    const order = await SELECT.one.from(Orders).where({ ID: orderId });
    if (!order) return req.error(404, `Order ${orderId} not found`);

    const cardanoSign = await getCardanoSign();
    try {
      const submission = await cardanoSign.send('SubmitVerifiedTransaction', {
        signingRequestId,
        signedTxCbor,
        signerType: 'browser-wallet',
        signerInfo: 'CIP-30 QUANTIX webapp',
        address: order.buyer,
      });
      const txHash = submission.txHash;
      await UPDATE(Orders)
        .set({ status: 'executed', executeTxHash: txHash })
        .where({ ID: orderId });
      return { txHash, newStatus: 'executed' };
    } catch (err) {
      await UPDATE(Orders).set({ status: 'failed' }).where({ ID: orderId });
      throw err;
    }
  });

  this.on('triggerOdvUpdate', async (req) => {
    const { supplierId, feed } = req.data;
    const supplier = await SELECT.one.from(Suppliers).where({ ID: supplierId });
    if (!supplier) return req.error(404, `Supplier ${supplierId} not found`);
    const policyId = feed === 'price' ? supplier.priceOraclePolicyId : supplier.inventoryOraclePolicyId;
    const assetName = feed === 'price' ? supplier.priceOracleAssetName : supplier.inventoryOracleAssetName;
    if (!policyId || !assetName) return req.error(400, `Supplier has no ${feed} oracle configured`);

    const res = await fetch(`${BRIDGE_URL}/odv-update`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ policy_id: policyId, asset_name: assetName }),
    });
    if (!res.ok) return req.error(502, `Bridge returned ${res.status}`);
    return res.json();
  });
});
