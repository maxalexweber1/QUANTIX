sap.ui.define([
  "sap/ui/core/mvc/Controller",
  "sap/ui/core/Fragment",
  "sap/m/MessageBox",
  "sap/m/MessageToast",
  "sap/m/BusyDialog",
  "sap/ui/model/json/JSONModel",
  "quantix/model/CardanoWallet"
], function (Controller, Fragment, MessageBox, MessageToast, BusyDialog, JSONModel, CardanoWallet) {
  "use strict";

  return Controller.extend("quantix.controller.SupplierList", {

    onRefresh: function () {
      var oBinding = this.byId("supplierTable").getBinding("items");
      if (oBinding) { oBinding.refresh(); }
      MessageToast.show("Suppliers refreshed");
    },

    onListUpdateFinished: function () {},

    formatTimestamp: function (ms) {
      var n = ms == null ? NaN : Number(ms);
      if (!isFinite(n) || n <= 0) { return "—"; }
      var d = new Date(n);
      if (isNaN(d.getTime())) { return "—"; }
      var iSecondsAgo = Math.round((Date.now() - d.getTime()) / 1000);
      if (iSecondsAgo < 0)    return "in " + Math.abs(iSecondsAgo) + "s";
      if (iSecondsAgo < 60)   return iSecondsAgo + "s ago";
      if (iSecondsAgo < 3600) return Math.round(iSecondsAgo / 60) + "m ago";
      return d.toISOString().slice(0, 16).replace("T", " ");
    },

    formatValidFor: function (ms) {
      var n = ms == null ? NaN : Number(ms);
      if (!isFinite(n) || n <= 0) { return "—"; }
      var iSecondsLeft = Math.round((n - Date.now()) / 1000);
      if (iSecondsLeft <= 0)  return "expired";
      if (iSecondsLeft < 60)  return iSecondsLeft + "s left";
      if (iSecondsLeft < 3600) return Math.round(iSecondsLeft / 60) + "m left";
      return Math.round(iSecondsLeft / 3600) + "h left";
    },

    formatValidState: function (ms) {
      var n = ms == null ? NaN : Number(ms);
      if (!isFinite(n) || n <= 0) { return "None"; }
      var iSecondsLeft = Math.round((n - Date.now()) / 1000);
      if (iSecondsLeft <= 0)  return "Error";
      if (iSecondsLeft < 60)  return "Warning";
      return "Success";
    },

    /** Render raw lovelace (scaled to per-gram unit by CAP's READ hook) as "X.YZ ADA". */
    formatPriceAda: function (lovelace) {
      var n = lovelace == null ? NaN : Number(lovelace);
      if (!isFinite(n) || n < 0) { return "—"; }
      return (n / 1_000_000).toLocaleString(undefined, {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
      }) + " ADA";
    },

    /** Render integer grams with thousands separator. */
    formatGrams: function (g) {
      var n = g == null ? NaN : Number(g);
      if (!isFinite(n) || n < 0) { return "—"; }
      return Math.round(n).toLocaleString();
    },

    /** True when both feeds are still within their on-chain validity window. */
    formatBuyEnabled: function (invValidThrough, priceValidThrough, invG, priceLov) {
      if (!invG || !priceLov) return false;
      var now = Date.now();
      var invOk   = isFinite(Number(invValidThrough))   && Number(invValidThrough)   > now;
      var priceOk = isFinite(Number(priceValidThrough)) && Number(priceValidThrough) > now;
      return invOk && priceOk;
    },

    /**
     * Push a fresh oracle round for BOTH feeds of the supplier via the bridge.
     * Coordinator fan-outs to peer nodes → submits new AggState tx.
     *
     * Cooldown UX: button is disabled for 180s after every click (matches the
     * on-chain validity window). The bridge returns status="not_yet_expired"
     * when the AggState is still fresh — we surface that as a clear message
     * rather than a confusing empty tx-hash toast.
     */
    onRefreshFeed: function (oEvent) {
      var oCtx = oEvent.getSource().getBindingContext();
      var sSupplierId = oCtx.getProperty("ID");
      var sSupplierName = oCtx.getProperty("name");
      var oModel = this.getView().getModel();
      var oBtn = oEvent.getSource();
      var that = this;

      this._startCooldown(oBtn, 180);

      var oBusy = new BusyDialog({
        title: "Refreshing " + sSupplierName,
        text: "Pushing new oracle round on-chain for inventory + price…"
      });
      oBusy.open();

      function trigger(sFeed) {
        var oBinding = oModel.bindContext("/triggerOdvUpdate(...)");
        oBinding.setParameter("supplierId", sSupplierId);
        oBinding.setParameter("feed", sFeed);
        return oBinding.execute().then(function () {
          return oBinding.getBoundContext().getObject();
        });
      }

      function summarizeFeed(sLabel, oRes) {
        if (!oRes) { return sLabel + ": (no response)"; }
        if (oRes.status === "submitted" && oRes.newC3asTxHash) {
          return sLabel + ": submitted " + oRes.newC3asTxHash.substring(0, 12) + "…";
        }
        if (oRes.status === "not_yet_expired") {
          return sLabel + ": still fresh on-chain";
        }
        return sLabel + ": " + (oRes.status || "unknown");
      }

      function firstTxHash(aResults) {
        for (var i = 0; i < aResults.length; i++) {
          var r = aResults[i];
          if (r && r.newC3asTxHash) return r.newC3asTxHash;
        }
        return null;
      }

      Promise.all([trigger("inventory"), trigger("price")])
        .then(function (aResults) {
          oBusy.close();
          var sMsg =
            summarizeFeed("Warehouse", aResults[0]) + "\n" +
            summarizeFeed("Pricing  ", aResults[1]);
          var sTxHash = firstTxHash(aResults);
          if (sTxHash) {
            var sUrl = "https://preprod.cardanoscan.io/transaction/" + sTxHash;
            MessageBox.success(sMsg, {
              title: "Oracle round submitted",
              actions: ["View on Cardanoscan", MessageBox.Action.CLOSE],
              emphasizedAction: "View on Cardanoscan",
              onClose: function (sAction) {
                if (sAction === "View on Cardanoscan") { window.open(sUrl, "_blank"); }
              }
            });
          } else {
            MessageToast.show(sMsg, { duration: 6000, width: "32em" });
          }
          setTimeout(function () {
            var oListBinding = that.byId("supplierTable").getBinding("items");
            if (oListBinding) { oListBinding.refresh(); }
          }, 3000);
        })
        .catch(function (err) {
          oBusy.close();
          that._cancelCooldown(oBtn);
          MessageBox.error("Failed to push oracle round: " + (err.message || String(err)));
        });
    },

    /**
     * Disable the button for `iSeconds` and show a live countdown in its label.
     * Stores original text under the button's custom data so we can restore it.
     */
    _startCooldown: function (oBtn, iSeconds) {
      var sOriginal = oBtn.data("originalText") || oBtn.getText();
      oBtn.data("originalText", sOriginal);
      oBtn.setEnabled(false);
      var that = this;
      var iRemaining = iSeconds;
      var fnTick = function () {
        if (iRemaining <= 0) {
          that._cancelCooldown(oBtn);
          return;
        }
        var iMin = Math.floor(iRemaining / 60);
        var iSec = iRemaining % 60;
        oBtn.setText("Cooldown " + iMin + ":" + (iSec < 10 ? "0" : "") + iSec);
        iRemaining--;
      };
      fnTick();
      var iv = setInterval(fnTick, 1000);
      oBtn.data("cooldownInterval", iv);
    },

    _cancelCooldown: function (oBtn) {
      var iv = oBtn.data("cooldownInterval");
      if (iv) { clearInterval(iv); }
      oBtn.data("cooldownInterval", null);
      var sOriginal = oBtn.data("originalText");
      if (sOriginal) { oBtn.setText(sOriginal); }
      oBtn.setEnabled(true);
    },

    // ───────── Buy flow (mint-based 1-step) ─────────

    onBuy: function (oEvent) {
      if (!CardanoWallet.isConnected()) {
        MessageBox.warning("Connect your Cardano wallet first.");
        return;
      }
      var oCtx = oEvent.getSource().getBindingContext();
      var oBuyModel = new JSONModel({
        supplierId:          oCtx.getProperty("ID"),
        supplierName:        oCtx.getProperty("name"),
        inventoryG:          oCtx.getProperty("currentInventoryG") || 0,
        unitPrice:           oCtx.getProperty("currentUnitPriceLovelace") || 0,
        requestedG:          "",
        totalLovelacePretty: "—"
      });
      this.getView().setModel(oBuyModel, "buyOrder");

      var that = this;
      if (!this._pBuyDialog) {
        this._pBuyDialog = Fragment.load({
          id: this.getView().getId(),
          name: "quantix.fragment.BuyDialog",
          controller: this
        }).then(function (oDialog) {
          that.getView().addDependent(oDialog);
          return oDialog;
        });
      }
      this._pBuyDialog.then(function (oDialog) { oDialog.open(); });
    },

    onBuyQuantityChange: function (oEvent) {
      var oModel = this.getView().getModel("buyOrder");
      var iG = parseInt(oEvent.getParameter("value"), 10);
      var iPriceLov = parseInt(oModel.getProperty("/unitPrice"), 10) || 0;
      if (!iG || !iPriceLov) {
        oModel.setProperty("/totalLovelacePretty", "—");
        return;
      }
      var iTotalLov = iG * iPriceLov;
      var sAda = (iTotalLov / 1_000_000).toLocaleString(undefined, {
        minimumFractionDigits: 2, maximumFractionDigits: 2,
      });
      oModel.setProperty("/totalLovelacePretty", sAda + " ADA");
    },

    onBuyCancel: function () {
      this.byId("buyDialog").close();
    },

    onBuyConfirm: function () {
      var oBuyModel = this.getView().getModel("buyOrder");
      var iG = parseInt(oBuyModel.getProperty("/requestedG"), 10);
      if (!iG || iG <= 0) { MessageBox.error("Enter a positive quantity."); return; }
      this.byId("buyDialog").close();
      this._mintAndBuy(oBuyModel.getProperty("/supplierId"), iG);
    },

    /**
     * 1) POST buyFromSupplier(supplierId, requestedG, buyerAddress, buyerVkh)
     *    → {orderId, unsignedCborHex, signingRequestId, paidLovelace, …}
     * 2) Wallet.signTx(unsignedCborHex, true) → witness set CBOR
     * 3) submitSigned(orderId, signingRequestId, signedTxCbor) → txHash + status=executed
     */
    _mintAndBuy: function (sSupplierId, iRequestedG) {
      var oModel = this.getView().getModel();
      var sBuyerAddr = CardanoWallet.getAddress();
      var sBuyerVkh  = CardanoWallet.getVkh();

      var oBusy = new BusyDialog({
        title: "Placing order",
        text: "Building mint transaction…"
      });
      oBusy.open();

      var oBuild = oModel.bindContext("/buyFromSupplier(...)");
      oBuild.setParameter("supplierId",   sSupplierId);
      oBuild.setParameter("requestedG",   iRequestedG);
      oBuild.setParameter("buyerAddress", sBuyerAddr);
      oBuild.setParameter("buyerVkh",     sBuyerVkh);

      var that = this;
      oBuild.execute()
        .then(function () {
          var oRes = oBuild.getBoundContext().getObject();
          oBusy.setText("Please sign with your wallet…");
          return CardanoWallet.signTx(oRes.unsignedCborHex, true).then(function (sWitness) {
            return { res: oRes, witness: sWitness };
          });
        })
        .then(function (oSigned) {
          oBusy.setText("Submitting transaction on-chain…");
          var oSubmit = oModel.bindContext("/submitSigned(...)");
          oSubmit.setParameter("orderId",          oSigned.res.orderId);
          oSubmit.setParameter("signingRequestId", oSigned.res.signingRequestId);
          oSubmit.setParameter("signedTxCbor",     oSigned.witness);
          return oSubmit.execute().then(function () {
            return { res: oSigned.res, submit: oSubmit.getBoundContext().getObject() };
          });
        })
        .then(function (o) {
          oBusy.close();
          var sPriceAda = (o.res.livePrice / 1_000_000).toFixed(2);
          var sMsg =
            "Paid: " + (o.res.paidLovelace / 1_000_000).toFixed(2) + " ADA  (" +
            iRequestedG + "g × " + sPriceAda + " ADA/g)\n" +
            "Tx:   " + o.submit.txHash.substring(0, 16) + "…";
          var sUrl = "https://preprod.cardanoscan.io/transaction/" + o.submit.txHash;
          MessageBox.success(sMsg, {
            title: "Order executed on-chain!",
            actions: ["View on Cardanoscan", MessageBox.Action.CLOSE],
            emphasizedAction: "View on Cardanoscan",
            onClose: function (sAction) {
              if (sAction === "View on Cardanoscan") { window.open(sUrl, "_blank"); }
            }
          });
        })
        .catch(function (err) {
          oBusy.close();
          var sMsg = err.message || String(err);
          if (sMsg.indexOf("User") >= 0 || sMsg.indexOf("declined") >= 0) {
            MessageToast.show("Signing cancelled");
          } else {
            MessageBox.error("Buy failed: " + sMsg);
          }
        });
    }
  });
});
