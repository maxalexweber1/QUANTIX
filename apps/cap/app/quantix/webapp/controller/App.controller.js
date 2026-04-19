sap.ui.define([
  "sap/ui/core/mvc/Controller",
  "sap/ui/core/Fragment",
  "quantix/model/CardanoWallet"
], function (Controller, Fragment, CardanoWallet) {
  "use strict";

  return Controller.extend("quantix.controller.App", {

    onInit: function () {
      var oWalletModel = this.getOwnerComponent().getModel("wallet");
      oWalletModel.setProperty("/connecting", false);
      oWalletModel.setProperty("/error", "");
      oWalletModel.setProperty("/guest", false);
      oWalletModel.setProperty("/wallets", CardanoWallet.detect());

      var oRouter = this.getOwnerComponent().getRouter();
      oRouter.attachRouteMatched(this._onRouteGuard, this);
    },

    _onRouteGuard: function (oEvent) {
      var oWalletModel = this.getOwnerComponent().getModel("wallet");
      if (!oWalletModel.getProperty("/guest")) { return; }
      var sRouteName = oEvent.getParameter("name");
      // Guests may only browse Suppliers + Feeds
      if (sRouteName === "orders" || sRouteName === "orderDetail") {
        this.getOwnerComponent().getRouter().navTo("suppliers");
      }
    },

    _navToApp: function () {
      this.byId("rootApp").to(this.byId("toolPage"));
    },

    _navToWelcome: function () {
      this.byId("rootApp").to(this.byId("welcomePage"));
    },

    onToggleMenu: function () {
      var oToolPage = this.byId("toolPage");
      oToolPage.setSideExpanded(!oToolPage.getSideExpanded());
    },

    onNavSelect: function (oEvent) {
      var sKey = oEvent.getParameter("item").getKey();
      this.getOwnerComponent().getRouter().navTo(sKey);
    },

    onGuestLogin: function () {
      var oWalletModel = this.getOwnerComponent().getModel("wallet");
      oWalletModel.setProperty("/guest", true);
      this._navToApp();
      this.getOwnerComponent().getRouter().navTo("suppliers");
      var oSideNav = this.byId("sideNav");
      if (oSideNav) { oSideNav.setSelectedKey("suppliers"); }
    },

    onWalletPress: function (oEvent) {
      var oWalletModel = this.getOwnerComponent().getModel("wallet");

      if (oWalletModel.getProperty("/guest")) {
        oWalletModel.setProperty("/guest", false);
        this._navToWelcome();
        return;
      }

      if (oWalletModel.getProperty("/connected")) {
        CardanoWallet.disconnect();
        oWalletModel.setProperty("/connected", false);
        oWalletModel.setProperty("/name", "");
        oWalletModel.setProperty("/address", "");
        oWalletModel.setProperty("/bech32", "");
        oWalletModel.setProperty("/vkh", "");
        var oList = this.byId("welcomeWalletList");
        if (oList) { oList.removeSelections(true); }
        this._navToWelcome();
        return;
      }

      // Not connected and not guest — show popover on header click
      var oButton = oEvent.getSource();
      if (!this._pWalletPopover) {
        this._pWalletPopover = Fragment.load({
          id: this.getView().getId(),
          name: "quantix.fragment.WalletConnect",
          controller: this
        }).then(function (oPopover) {
          this.getView().addDependent(oPopover);
          return oPopover;
        }.bind(this));
      }
      this._pWalletPopover.then(function (oPopover) {
        oPopover.openBy(oButton);
      });
    },

    onWelcomeWalletSelect: function (oEvent) {
      var oItem = oEvent.getParameter("listItem");
      var sWalletId = oItem.getBindingContext("wallet").getProperty("id");
      this._connect(sWalletId);
    },

    onWalletSelect: function (oEvent) {
      var sWalletId = oEvent.getSource().data("walletName");
      var that = this;
      this._connect(sWalletId).then(function () {
        if (that._pWalletPopover) {
          that._pWalletPopover.then(function (p) { p.close(); });
        }
      });
    },

    _connect: function (sWalletId) {
      var oWalletModel = this.getOwnerComponent().getModel("wallet");
      var that = this;

      oWalletModel.setProperty("/connecting", true);
      oWalletModel.setProperty("/error", "");

      return CardanoWallet.connect(sWalletId)
        .then(function (oInfo) {
          oWalletModel.setProperty("/guest", false);
          oWalletModel.setProperty("/connected", true);
          oWalletModel.setProperty("/name", oInfo.name);
          oWalletModel.setProperty("/address", oInfo.address);
          oWalletModel.setProperty("/bech32", oInfo.bech32);
          oWalletModel.setProperty("/vkh", oInfo.vkh);
          oWalletModel.setProperty("/connecting", false);
          that._navToApp();
          that.getOwnerComponent().getRouter().navTo("orders");
          sap.m.MessageToast.show("Connected to " + oInfo.name);
        })
        .catch(function (err) {
          oWalletModel.setProperty("/connecting", false);
          oWalletModel.setProperty("/error", "Connection failed: " + err.message);
        });
    }
  });
});
