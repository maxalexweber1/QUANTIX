sap.ui.define([
  "sap/ui/model/json/JSONModel"
], function (JSONModel) {
  "use strict";

  return {
    createWalletModel: function () {
      return new JSONModel({
        connected: false,
        connecting: false,
        error: "",
        name: "",
        icon: "",
        address: "",
        bech32: "",
        vkh: "",
        guest: false,
        wallets: []
      });
    }
  };
});
