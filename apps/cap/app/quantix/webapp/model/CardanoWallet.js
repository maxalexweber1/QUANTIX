sap.ui.define([], function () {
  "use strict";

  var _api = null;
  var _name = "";
  var _addressHex = "";
  var _addressBech32 = "";
  var _vkh = "";

  var KNOWN_WALLETS = ["nami", "eternl", "lace", "flint", "typhon", "gerowallet", "nufi", "begin", "vespr"];

  var BECH32_CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l";

  function _polymod(values) {
    var GEN = [0x3b6a57b2, 0x26508e6d, 0x1ea119fa, 0x3d4233dd, 0x2a1462b3];
    var chk = 1;
    for (var i = 0; i < values.length; i++) {
      var b = chk >> 25;
      chk = ((chk & 0x1ffffff) << 5) ^ values[i];
      for (var j = 0; j < 5; j++) {
        if ((b >> j) & 1) chk ^= GEN[j];
      }
    }
    return chk;
  }

  function _hrpExpand(hrp) {
    var ret = [];
    for (var i = 0; i < hrp.length; i++) ret.push(hrp.charCodeAt(i) >> 5);
    ret.push(0);
    for (var i = 0; i < hrp.length; i++) ret.push(hrp.charCodeAt(i) & 31);
    return ret;
  }

  function _createChecksum(hrp, data) {
    var values = _hrpExpand(hrp).concat(data).concat([0, 0, 0, 0, 0, 0]);
    var mod = _polymod(values) ^ 1;
    var ret = [];
    for (var i = 0; i < 6; i++) ret.push((mod >> (5 * (5 - i))) & 31);
    return ret;
  }

  function _convertBits(data, fromBits, toBits, pad) {
    var acc = 0, bits = 0, ret = [], maxv = (1 << toBits) - 1;
    for (var i = 0; i < data.length; i++) {
      acc = (acc << fromBits) | data[i];
      bits += fromBits;
      while (bits >= toBits) {
        bits -= toBits;
        ret.push((acc >> bits) & maxv);
      }
    }
    if (pad && bits > 0) ret.push((acc << (toBits - bits)) & maxv);
    return ret;
  }

  function _hexToBytes(hex) {
    var bytes = [];
    for (var i = 0; i < hex.length; i += 2) {
      bytes.push(parseInt(hex.substr(i, 2), 16));
    }
    return bytes;
  }

  // CIP-30 wallets (Eternl, Lace) wrap addresses as CBOR byte-strings.
  // Nami returns raw hex.
  function _stripCborByteString(hex) {
    var b0 = parseInt(hex.substr(0, 2), 16);
    if ((b0 & 0xe0) === 0x40) {
      var addInfo = b0 & 0x1f;
      if (addInfo <= 23) return hex.substr(2);
      if (addInfo === 24) return hex.substr(4);
      if (addInfo === 25) return hex.substr(6);
    }
    return hex;
  }

  function _hexToBech32(hexAddr) {
    var rawHex = _stripCborByteString(hexAddr);
    var bytes = _hexToBytes(rawHex);
    var network = bytes[0] & 0x0F;
    var hrp = network === 0 ? "addr_test" : "addr";
    var data5bit = _convertBits(bytes, 8, 5, true);
    var checksum = _createChecksum(hrp, data5bit);
    var encoded = hrp + "1";
    for (var i = 0; i < data5bit.length; i++) encoded += BECH32_CHARSET[data5bit[i]];
    for (var i = 0; i < checksum.length; i++) encoded += BECH32_CHARSET[checksum[i]];
    return encoded;
  }

  return {

    detect: function () {
      if (typeof window === "undefined" || !window.cardano) {
        return [];
      }
      var aWallets = [];
      KNOWN_WALLETS.forEach(function (sId) {
        var oWallet = window.cardano[sId];
        if (oWallet && typeof oWallet.enable === "function") {
          aWallets.push({
            id: sId,
            name: oWallet.name || sId,
            icon: oWallet.icon || ""
          });
        }
      });
      return aWallets;
    },

    connect: function (sWalletId) {
      var oWallet = window.cardano && window.cardano[sWalletId];
      if (!oWallet) {
        return Promise.reject(new Error("Wallet '" + sWalletId + "' not found"));
      }

      return oWallet.enable().then(function (api) {
        _api = api;
        _name = oWallet.name || sWalletId;
        return api.getUsedAddresses();
      }).then(function (aAddresses) {
        var sAddrHex = aAddresses && aAddresses[0];
        if (!sAddrHex) {
          return Promise.reject(new Error("No addresses found in wallet"));
        }
        var sRawHex = _stripCborByteString(sAddrHex);
        _addressHex = sRawHex;
        _addressBech32 = _hexToBech32(sAddrHex);
        _vkh = sRawHex.slice(2, 58);

        return {
          name: _name,
          address: _addressHex,
          bech32: _addressBech32,
          vkh: _vkh
        };
      });
    },

    disconnect: function () {
      _api = null;
      _name = "";
      _addressHex = "";
      _addressBech32 = "";
      _vkh = "";
    },

    isConnected: function () {
      return _api !== null;
    },

    getName: function () {
      return _name;
    },

    getAddress: function () {
      return _addressBech32;
    },

    getAddressHex: function () {
      return _addressHex;
    },

    getVkh: function () {
      return _vkh;
    },

    signTx: function (sTxCbor, bPartialSign) {
      if (!_api) {
        return Promise.reject(new Error("No wallet connected"));
      }
      return _api.signTx(sTxCbor, bPartialSign !== false);
    },

    submitTx: function (sSignedTxCbor) {
      if (!_api) {
        return Promise.reject(new Error("No wallet connected"));
      }
      return _api.submitTx(sSignedTxCbor);
    },

    getAddresses: function () {
      if (!_api) {
        return Promise.reject(new Error("No wallet connected"));
      }
      return _api.getUsedAddresses();
    },

    bech32ToVkh: function (sBech32) {
      if (!sBech32 || typeof sBech32 !== "string") return "";
      var sepIdx = sBech32.lastIndexOf("1");
      if (sepIdx < 1) return "";

      var dataPart = sBech32.slice(sepIdx + 1);
      var data5 = [];
      for (var i = 0; i < dataPart.length - 6; i++) {
        var c = BECH32_CHARSET.indexOf(dataPart[i]);
        if (c === -1) return "";
        data5.push(c);
      }

      var bytes = _convertBits(data5, 5, 8, false);
      if (bytes.length < 29) return "";

      var vkh = "";
      for (var j = 1; j <= 28; j++) {
        var h = bytes[j].toString(16);
        vkh += h.length < 2 ? "0" + h : h;
      }
      return vkh;
    }
  };
});
