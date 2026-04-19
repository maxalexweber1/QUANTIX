sap.ui.define([
  "sap/ui/core/mvc/Controller"
], function (Controller) {
  "use strict";

  return Controller.extend("quantix.controller.OrderDetail", {
    onNavBack: function () {
      this.getOwnerComponent().getRouter().navTo("orders");
    }
  });
});
