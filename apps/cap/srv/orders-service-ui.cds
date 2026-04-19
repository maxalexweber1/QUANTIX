using { OrdersService } from './orders-service';

annotate OrdersService.Orders with @(
  UI: {
    HeaderInfo: {
      TypeName      : 'Buy',
      TypeNamePlural: 'Buys',
      Title         : { Value: ID },
      Description   : { Value: status }
    },
    Identification: [
      { Value: buyer,            Label: 'Buyer Address' },
      { Value: supplier_ID,      Label: 'Supplier'      },
      { Value: status,           Label: 'Status'        }
    ],
    SelectionFields: [
      status,
      supplier_ID
    ],
    LineItem: [
      { Value: supplier.name, Label: 'Supplier'                },
      { Value: requestedG,    Label: 'Quantity (g)'            },
      { Value: pricePerGram,  Label: 'Price / g (lovelace)'    },
      { Value: paidLovelace,  Label: 'Paid (lovelace)'         },
      { Value: status,        Label: 'Status',
        Criticality: #Critical                                 },
      { Value: executeTxHash, Label: 'Buy Tx'                  }
    ],
    Facets: [
      {
        $Type : 'UI.ReferenceFacet',
        Target: '@UI.FieldGroup#Details',
        Label : 'Buy Details'
      }
    ],
    FieldGroup#Details: {
      Data: [
        { Value: buyer,         Label: 'Buyer'                 },
        { Value: supplier_ID,   Label: 'Supplier ID'           },
        { Value: requestedG,    Label: 'Quantity (g)'          },
        { Value: pricePerGram,  Label: 'Price / g (lovelace)'  },
        { Value: paidLovelace,  Label: 'Total Paid (lovelace)' },
        { Value: status,        Label: 'Status'                },
        { Value: executeTxHash, Label: 'Buy Tx Hash'           }
      ]
    }
  }
);

annotate OrdersService.Orders with {
  buyer         @title: 'Buyer (Bech32)';
  supplier      @title: 'Supplier'
                @Common.ValueList: {
                   $Type         : 'Common.ValueListType',
                   CollectionPath: 'Suppliers',
                   Parameters    : [
                     { $Type: 'Common.ValueListParameterInOut',
                       LocalDataProperty: supplier_ID,
                       ValueListProperty: 'ID'           },
                     { $Type: 'Common.ValueListParameterDisplayOnly',
                       ValueListProperty: 'name'         }
                   ]
                };
  requestedG    @title: 'Quantity (g)'           @Measures.Unit: 'g';
  pricePerGram  @title: 'Price / g (lovelace)';
  paidLovelace  @title: 'Total Paid (lovelace)';
  status        @title: 'Status';
  executeTxHash @title: 'Buy Tx Hash'            @readonly;
};

annotate OrdersService.Suppliers with @(
  UI: {
    HeaderInfo: {
      TypeName      : 'Supplier',
      TypeNamePlural: 'Suppliers',
      Title         : { Value: name },
      Description   : { Value: productCode }
    },
    LineItem: [
      { Value: name,                      Label: 'Name'                },
      { Value: productCode,               Label: 'SKU'                 },
      { Value: currentInventoryG,         Label: 'Inventory (g)'       },
      { Value: currentUnitPriceLovelace,  Label: 'Unit Price (lovelace)' },
      { Value: paymentAddress,            Label: 'Payment Address'     }
    ],
    Facets: [
      {
        $Type : 'UI.ReferenceFacet',
        Target: '@UI.FieldGroup#Identity',
        Label : 'Identity'
      },
      {
        $Type : 'UI.ReferenceFacet',
        Target: '@UI.FieldGroup#Oracles',
        Label : 'Charli3 Oracles'
      },
      {
        $Type : 'UI.ReferenceFacet',
        Target: '@UI.FieldGroup#Live',
        Label : 'Live Feed Values'
      }
    ],
    FieldGroup#Identity: {
      Data: [
        { Value: name,           Label: 'Name'             },
        { Value: productCode,    Label: 'Product Code'     },
        { Value: paymentAddress, Label: 'Payment Address'  },
        { Value: erpUrl,         Label: 'ERP URL'          }
      ]
    },
    FieldGroup#Oracles: {
      Data: [
        { Value: inventoryOraclePolicyId,  Label: 'Inventory Policy ID' },
        { Value: inventoryOracleAssetName, Label: 'Inventory Asset'     },
        { Value: priceOraclePolicyId,      Label: 'Price Policy ID'     },
        { Value: priceOracleAssetName,     Label: 'Price Asset'         }
      ]
    },
    FieldGroup#Live: {
      Data: [
        { Value: currentInventoryG,        Label: 'Inventory (g)'        },
        { Value: inventoryTimestampMs,     Label: 'Inventory Timestamp'  },
        { Value: inventoryValidThroughMs,  Label: 'Inventory Valid Until'},
        { Value: currentUnitPriceLovelace, Label: 'Unit Price (lovelace)'},
        { Value: priceTimestampMs,         Label: 'Price Timestamp'      },
        { Value: priceValidThroughMs,      Label: 'Price Valid Until'    }
      ]
    }
  }
);
