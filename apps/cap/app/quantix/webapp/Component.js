sap.ui.define([
  "sap/ui/core/UIComponent",
  "quantix/model/models"
], function (UIComponent, models) {
  "use strict";

  return UIComponent.extend("quantix.Component", {
    metadata: { manifest: "json" },

    init: function () {
      UIComponent.prototype.init.apply(this, arguments);
      this.getRouter().initialize();
    }
  });
});
