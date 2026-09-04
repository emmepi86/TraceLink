# Findings — ERPNext (external validation)

Written from the project's own merged pull requests, before TraceLink was run. Anchors verified with an independent scan of HEAD.

## ERP-001 — a comma decimal separator made every reading fail its range [HIGH]
### STATUS: OPEN

`inspect_and_set_status` parsed quality inspection readings with the site's Number Format. Where the decimal separator is a comma, readings inside their acceptance range were read as out of range and the inspection was rejected.

## ERP-002 — a shared target UOM needs an intermediate lookup [MEDIUM]
### STATUS: OPEN

`get_uom_conv_factor` resolves conversions through an intermediate UOM. The shared-source lookup alone missed pairs that only share a target.

## ERP-003 — the next stock reconciliation must not be found by legacy batch number [HIGH]
### STATUS: OPEN

`get_next_stock_reco` filtered by the legacy flat batch number, so a Landed Cost Voucher repricing after a batch Stock Reconciliation lost its quantity.

## ERP-004 — PO-level invoice amounts cannot be reused across receipts [HIGH]
### STATUS: OPEN

`update_billed_amount_based_on_po` overstated billed amounts when FIFO allocation spanned several Purchase Receipts, marking a receipt Completed too early.

## ERP-005 — MT940 carries the per-transaction reference in the :61: field [MEDIUM]
### STATUS: OPEN

`convert_mt940_to_csv` must take the reference from the `:61:` customer_reference; the statement-level number is not per transaction.

## ERP-006 — picked quantities spanning locations were only counted once [MEDIUM]
### STATUS: OPEN

`get_items_with_location_and_quantity` handles picks that span several locations and batches; only the final batch used to get its delivered quantity updated.

## ERP-007 — a job card transfer covers one operation, not the whole work order [MEDIUM]
### STATUS: OPEN

`make_semi_fg_work_order` belongs to the tracked semi-finished-goods flow where the coverage cap must not check materials for every operation: a Job Card transfer carries one operation's materials.

## ERP-008 — a CSV row with missing trailing values is not an invalid row [MEDIUM]
### STATUS: OPEN

`import_to_delete_template_method` treats absent trailing values as empty before trimming, and collects valid rows before replacing the existing list.

## ERP-009 — stock ageing has to survive negative stock and mixed valuation [MEDIUM]
### STATUS: OPEN

`FIFOSlots` handles batchwise valuation together with negative stock, stock reconciliations, and batches that mix batchwise and non-batchwise valuation.

## ERP-010 — PostgreSQL will not accept an arbitrary GROUP BY pick [MEDIUM]
### STATUS: OPEN

`_sub_assembly_rm_query` must make non-grouped columns deterministic: what MariaDB tolerates, strict GROUP BY rejects.

## ERP-011 — BOM Explorer reset the accumulated quantity one level down [HIGH]
### STATUS: OPEN

In `erpnext/manufacturing/report/bom_explorer/bom_explorer.py`, `get_exploded_items` lost the accumulated quantity when entering a deeper BOM level, so nested components came out wrong.

## ERP-012 — the batchwise valuation fallback can divide by zero on PostgreSQL [HIGH]
### STATUS: OPEN

In `erpnext/stock/stock_ledger.py`, `get_valuation_rate` has a batchwise fallback whose division is unguarded. Pre-existing and PostgreSQL-only; found by a database parity audit, not by a user.

## ERP-013 — skipping the WIP transfer is not, by itself, being In Process [MEDIUM]
### STATUS: OPEN

In `erpnext/manufacturing/doctype/work_order/work_order.py`, `get_status` must not move a submitted Work Order to In Process merely because Skip Material Transfer to WIP Warehouse is enabled. It stays Not Started.

## ERP-014 — saving an Item must not overwrite an explicit conversion factor [HIGH]
### STATUS: OPEN

In `erpnext/stock/doctype/item/item.py`, `validate_uom` replaced an item-level UOM conversion factor with the global one, silently changing what the user had typed.

## ERP-015 — the closing-balance timeout is a stopgap, not a design [MEDIUM]
### STATUS: OPEN

`erpnext/accounts/doctype/accounts_settings/accounts_settings.py` carries a configurable timeout because one hour is not enough for Account Closing Balance creation on large sites. It is explicitly temporary.

## ERP-016 — budget queries had to be made PostgreSQL-valid [MEDIUM]
### STATUS: OPEN

`erpnext/controllers/budget_controller.py` holds one of the one-line fixes from the MariaDB/PostgreSQL parity series.

## ERP-017 — a customer and a supplier can share a name [MEDIUM]
### STATUS: OPEN

The tax withholding report showed customer records even when a supplier was selected, because party filtering did not consider that both can carry the same name. The report entry point is `execute`.

## ERP-018 — report columns are built per report [MEDIUM]
### STATUS: OPEN

`get_columns` is defined once per report module, so a note that names it without saying which report cannot be resolved.

## ERP-019 — the setup wizard creates translated master data [MEDIUM]
### STATUS: OPEN

Running the setup wizard in German creates Opportunity Type records in German, so any default written in English stops matching. The knowledge is about data the wizard produces, not about a function.

## ERP-020 — hotfix branches are a release process, not code [MEDIUM]
### STATUS: OPEN

Fixes are carried to version-15-hotfix and version-16-hotfix by backport pull requests. Nothing in the codebase represents that policy.
