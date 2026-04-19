sap.ui.define([
  "sap/ui/core/mvc/Controller",
  "sap/m/MessageToast",
  "sap/ui/model/Filter",
  "sap/ui/model/FilterOperator",
  "sap/ui/model/json/JSONModel"
], function (Controller, MessageToast, Filter, FilterOperator, JSONModel) {
  "use strict";

  return Controller.extend("quantix.controller.OrderList", {

    onInit: function () {
      this.getView().setModel(new JSONModel({ mineCount: 0 }), "viewModel");
      this._lastAppliedBech32 = null;

      // Re-apply the buyer filter every time the view becomes visible —
      // covers the Guest → Wallet switch (and vice versa) where the wallet
      // model changes while this view is hidden, and JSONModel's
      // propertyChange event is too flaky to rely on alone.
      var that = this;
      this.getView().addEventDelegate({
        onBeforeShow: function () { that._applyBuyerFilter(); },
        onAfterRendering: function () { that._applyBuyerFilter(); },
      });

      // Best-effort: also listen on wallet-model changes for the case where
      // My Buys is the currently-active view while the user switches wallet.
      this.getOwnerComponent().getModel("wallet")
        .attachPropertyChange(this._onWalletChanged.bind(this));
    },

    _onWalletChanged: function () {
      clearTimeout(this._walletChangeTimer);
      this._walletChangeTimer = setTimeout(this._applyBuyerFilter.bind(this), 50);
    },

    _applyBuyerFilter: function () {
      var oTable = this.byId("orderTable");
      if (!oTable) { return; }
      var oBinding = oTable.getBinding("items");
      if (!oBinding) { return; }

      var sBech32 = this.getOwnerComponent().getModel("wallet").getProperty("/bech32");
      var sFilterValue = sBech32 || "__no_wallet__";

      // Short-circuit: no need to re-filter if nothing changed.
      if (this._lastAppliedBech32 === sFilterValue) { return; }
      this._lastAppliedBech32 = sFilterValue;

      if (oBinding.isSuspended && oBinding.isSuspended()) { oBinding.resume(); }
      oBinding.filter([new Filter("buyer", FilterOperator.EQ, sFilterValue)]);
      // Force a fresh backend round-trip — filter() alone doesn't always
      // re-query in ODataV4 when the binding is still alive from a prior
      // view-show with a stale filter value.
      oBinding.refresh();
    },

    onListUpdateFinished: function (oEvent) {
      this.getView().getModel("viewModel").setProperty("/mineCount", oEvent.getParameter("total") || 0);
    },

    onRefresh: function () {
      var oBinding = this.byId("orderTable").getBinding("items");
      if (oBinding) { oBinding.refresh(); }
      MessageToast.show("Buys refreshed");
    },

    onOrderPress: function (oEvent) {
      var oCtx = oEvent.getSource().getBindingContext();
      var sOrderId = oCtx.getProperty("ID");
      this.getOwnerComponent().getRouter().navTo("orderDetail", { orderId: sOrderId });
    },

    // ---- Formatters ----

    formatStatusState: function (s) {
      switch (s) {
        case "executing": return "Warning";
        case "executed": return "Success";
        case "failed": return "Error";
        default: return "None";
      }
    },

    formatStatusIcon: function (s) {
      switch (s) {
        case "executing": return "sap-icon://pending";
        case "executed": return "sap-icon://accept";
        case "failed": return "sap-icon://decline";
        default: return "";
      }
    },

    /** "Apr 18, 21:05" style — short + readable, no timezone noise. */
    formatDateTime: function (iso) {
      if (!iso) return "—";
      var d = new Date(iso);
      if (isNaN(d.getTime())) return "—";
      return d.toLocaleString(undefined, {
        month: "short", day: "numeric",
        hour: "2-digit", minute: "2-digit",
      });
    },

    formatLovelace: function (lov) {
      if (lov == null || lov === "") { return "—"; }
      var n = Number(lov);
      if (!isFinite(n)) return "—";
      return (n / 1_000_000).toFixed(2) + " ₳";
    },

    formatGrams: function (g) {
      var n = g == null ? NaN : Number(g);
      if (!isFinite(n) || n < 0) return "—";
      return Math.round(n).toLocaleString();
    },

    /** pricePerGram is already unit-scaled (lovelace/g) by CAP. Render as ADA. */
    formatPriceAda: function (lov) {
      var n = lov == null ? NaN : Number(lov);
      if (!isFinite(n) || n < 0) return "—";
      return (n / 1_000_000).toLocaleString(undefined, {
        minimumFractionDigits: 2, maximumFractionDigits: 2,
      }) + " ADA";
    },

    /** Total paidLovelace (raw lovelace). */
    formatPaidAda: function (lov) {
      var n = lov == null ? NaN : Number(lov);
      if (!isFinite(n) || n < 0) return "—";
      return (n / 1_000_000).toLocaleString(undefined, {
        minimumFractionDigits: 2, maximumFractionDigits: 2,
      }) + " ADA";
    }
  });
});
