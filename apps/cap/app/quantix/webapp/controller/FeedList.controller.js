sap.ui.define([
  "sap/ui/core/mvc/Controller",
  "sap/ui/model/json/JSONModel"
], function (Controller, JSONModel) {
  "use strict";

  // Dual-coordinator routing (see docker-compose.yml + bridge main.py):
  //   node-*-1 → inventory coordinator (Warehouse Ops wallet)
  //   node-*-2 → pricing coordinator   (Pricing Desk wallet)
  var COORDINATOR_ADDRS = {
    a: {
      inventory: "addr_test1vqk5xfwfv9l7xn6wyyth5cagxjlv3uq483lgkg75dll99xq3w89l2",
      price: "addr_test1vrkxkeyhml229fyce6jpgke83lgup2u3mz70duly7n03pjcnv092g",
    },
    b: {
      inventory: "addr_test1vzz4grhg6ayaq4d52crr38umqwf0v73gz4frl8dgrwk65nc2jy6zh",
      price: "addr_test1vp4st26zx9rjdjy9p9usrurcdfm0cy6myehp588899pe2xgf9cplq",
    },
    c: {
      inventory: "addr_test1vprkh6sm87cn9wwfw2qw909ga07a009ana8xv3jht057f2c7rtyzl",
      price: "addr_test1vzhny7xu2q87swrktkkw0r5dphdnsmfwp6gjd0ctqmrkgzcnjgsdc",
    },
  };

  function supplierKey(supplierId) {
    var last = supplierId.slice(-1);
    return last === "1" ? "a" : last === "2" ? "b" : "c";
  }
  function cardanoscanAddr(addr) {
    return "https://preprod.cardanoscan.io/address/" + addr;
  }

  function formatFreshnessText(validThroughMs) {
    var n = Number(validThroughMs);
    if (!isFinite(n) || n <= 0) return "no data";
    var sec = Math.round((n - Date.now()) / 1000);
    if (sec <= 0) return "expired";
    if (sec < 60) return sec + "s left";
    if (sec < 3600) return Math.round(sec / 60) + "m left";
    return Math.round(sec / 3600) + "h left";
  }
  function formatFreshnessState(validThroughMs) {
    var n = Number(validThroughMs);
    if (!isFinite(n) || n <= 0) return "None";
    var sec = Math.round((n - Date.now()) / 1000);
    if (sec <= 0) return "Error";
    if (sec < 60) return "Warning";
    return "Success";
  }
  function formatUpdated(timestampMs) {
    var n = Number(timestampMs);
    if (!isFinite(n) || n <= 0) return "—";
    var d = new Date(n);
    var sec = Math.round((Date.now() - n) / 1000);
    if (sec < 60) return sec + "s ago";
    if (sec < 3600) return Math.round(sec / 60) + "m ago (" + d.toLocaleTimeString() + ")";
    return d.toLocaleString(undefined, {
      month: "short", day: "numeric",
      hour: "2-digit", minute: "2-digit",
    });
  }

  function buildCard(supplier, kind) {
    var isInv = kind === "inventory";
    var key = supplierKey(supplier.ID);
    var coordAddr = COORDINATOR_ADDRS[key][kind];
    var rawValue = Number(isInv ? supplier.currentInventoryG : supplier.currentUnitPriceLovelace);
    var validThroughMs = isInv ? supplier.inventoryValidThroughMs : supplier.priceValidThroughMs;
    var timestampMs = isInv ? supplier.inventoryTimestampMs : supplier.priceTimestampMs;
    var assetName = isInv ? supplier.inventoryOracleAssetName : supplier.priceOracleAssetName;
    var policyId = isInv ? supplier.inventoryOraclePolicyId : supplier.priceOraclePolicyId;

    var displayValue, displayUnit;
    if (!isFinite(rawValue) || rawValue < 0) {
      displayValue = "—"; displayUnit = "";
    } else if (isInv) {
      displayValue = Math.round(rawValue).toLocaleString();
      displayUnit = "g";
    } else {
      displayValue = (rawValue / 1_000_000).toLocaleString(undefined, {
        minimumFractionDigits: 2, maximumFractionDigits: 2,
      });
      displayUnit = "ADA/g";
    }

    return {
      supplierName: supplier.name,
      label: isInv ? "Warehouse Feed" : "Pricing Desk Feed",
      icon: isInv ? "sap-icon://inventory" : "sap-icon://money-bills",
      displayValue: displayValue,
      displayUnit: displayUnit,
      freshnessText: formatFreshnessText(validThroughMs),
      freshnessState: formatFreshnessState(validThroughMs),
      updatedText: formatUpdated(timestampMs),
      coordinatorAddr: coordAddr,
      coordinatorShort: coordAddr.slice(0, 14) + "…" + coordAddr.slice(-6),
      coordinatorUrl: cardanoscanAddr(coordAddr),
      assetName: assetName || "(none)",
      policyId: policyId || "",
      policyShort: policyId ? policyId.slice(0, 14) + "…" + policyId.slice(-6) : "—",
      refTxHash: null,
      refTxShort: "—",
      refTxUrl: "",
    };
  }

  return Controller.extend("quantix.controller.FeedList", {

    onInit: function () {
      var oView = this.getView();
      oView.setModel(new JSONModel({ cards: [] }), "feeds");
      var that = this;
      // Rebuild on every navigate-to so tab-switch gives fresh data.
      oView.addEventDelegate({ onBeforeShow: function () { that._reload(); } });
      // CAP OData model is usually attached slightly after onInit; retry.
      this._reload();
    },

    onRefresh: function () { this._reload(); },

    _reload: function () {
      var oModel = this.getView().getModel();
      if (!oModel) {
        setTimeout(this._reload.bind(this), 200);
        return;
      }
      var that = this;
      var oBinding = oModel.bindList("/Suppliers");
      oBinding.requestContexts(0, 20).then(function (aCtxs) {
        var aCards = [];
        aCtxs.forEach(function (ctx) {
          var s = ctx.getObject();
          aCards.push(buildCard(s, "inventory"));
          aCards.push(buildCard(s, "price"));
        });
        that.getView().getModel("feeds").setProperty("/cards", aCards);
      }).catch(function (err) {
        // eslint-disable-next-line no-console
        console.warn("FeedList load failed:", err);
      });
    }

  });
});
