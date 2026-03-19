-- =========================================
-- PRE-MIGRATE FIX SCRIPT (ERPNext v16)
-- =========================================

SET SQL_SAFE_UPDATES = 0;

-- =========================================
-- 1. CORE SCHEMA FIXES
-- =========================================

-- DocField fix
ALTER TABLE `tabDocField`
ADD COLUMN IF NOT EXISTS `is_virtual` INT(1) NOT NULL DEFAULT 0;

-- Patch Log fix
ALTER TABLE `tabPatch Log`
MODIFY COLUMN `patch` VARCHAR(255);

ALTER TABLE `tabPatch Log`
ADD COLUMN IF NOT EXISTS `migration_hash` VARCHAR(140);

ALTER TABLE `tabDocType`
ADD COLUMN IF NOT EXISTS `migration_hash` VARCHAR(255);


-- =========================================
-- 2. BOOLEAN DATA NORMALIZATION (CRITICAL)
-- =========================================

-- Purchase Invoice
UPDATE `tabPurchase Invoice`
SET disable_rounded_total = 0
WHERE disable_rounded_total IS NULL OR CAST(disable_rounded_total AS CHAR) IN ('No', '');

UPDATE `tabPurchase Invoice`
SET is_subcontracted = 0
WHERE is_subcontracted IS NULL OR CAST(is_subcontracted AS CHAR) IN ('No', '');

-- GL Entry
UPDATE `tabGL Entry`
SET is_cancelled = 0
WHERE is_cancelled IS NULL OR CAST(is_cancelled AS CHAR) IN ('No', '');

UPDATE `tabGL Entry`
SET to_rename = 0
WHERE to_rename IS NULL OR CAST(to_rename AS CHAR) IN ('No', '');

-- Purchase Order
UPDATE `tabPurchase Order`
SET disable_rounded_total = 0
WHERE disable_rounded_total IS NULL OR CAST(disable_rounded_total AS CHAR) IN ('No', '');

UPDATE `tabPurchase Order`
SET group_same_items = 0
WHERE group_same_items IS NULL OR CAST(group_same_items AS CHAR) IN ('No', '');

UPDATE `tabPurchase Order`
SET background_processing = 0
WHERE background_processing IS NULL OR CAST(background_processing AS CHAR) IN ('No', '');

UPDATE `tabPurchase Order`
SET ignore_pricing_rule = 0
WHERE ignore_pricing_rule IS NULL OR CAST(ignore_pricing_rule AS CHAR) IN ('No', '');

UPDATE `tabPurchase Order`
SET is_subcontracting = 0
WHERE is_subcontracting IS NULL OR CAST(is_subcontracting AS CHAR) IN ('No', '');

UPDATE `tabPurchase Order`
SET is_subcontracted = 0
WHERE is_subcontracted IS NULL OR CAST(is_subcontracted AS CHAR) IN ('No', '');

-- Stock Ledger Entry
UPDATE `tabStock Ledger Entry`
SET is_cancelled = 0
WHERE is_cancelled IS NULL OR CAST(is_cancelled AS CHAR) IN ('No', '');

UPDATE `tabStock Ledger Entry`
SET to_rename = 0
WHERE to_rename IS NULL OR CAST(to_rename AS CHAR) IN ('No', '');

-- Purchase Receipt
UPDATE `tabPurchase Receipt`
SET disable_rounded_total = 0
WHERE disable_rounded_total IS NULL OR CAST(disable_rounded_total AS CHAR) IN ('No', '');

UPDATE `tabPurchase Receipt`
SET ignore_pricing_rule = 0
WHERE ignore_pricing_rule IS NULL OR CAST(ignore_pricing_rule AS CHAR) IN ('No', '');

UPDATE `tabPurchase Receipt`
SET group_same_items = 0
WHERE group_same_items IS NULL OR CAST(group_same_items AS CHAR) IN ('No', '');

UPDATE `tabPurchase Receipt`
SET set_posting_time = 0
WHERE set_posting_time IS NULL OR CAST(set_posting_time AS CHAR) IN ('No', '');

UPDATE `tabPurchase Receipt`
SET is_return = 0
WHERE is_return IS NULL OR CAST(is_return AS CHAR) IN ('No', '');

UPDATE `tabPurchase Receipt`
SET is_subcontracted = 0
WHERE is_subcontracted IS NULL OR CAST(is_subcontracted AS CHAR) IN ('No', '');


-- =========================================
-- 3. SALES INVOICE (ROW SIZE + po_no FIX)
-- =========================================

ALTER TABLE `tabSales Invoice` ROW_FORMAT=DYNAMIC;

-- Convert to TEXT
ALTER TABLE `tabSales Invoice`
MODIFY `customer_name` TEXT,
MODIFY `contact_display` TEXT,
MODIFY `remarks` TEXT,
MODIFY `base_in_words` TEXT,
MODIFY `in_words` TEXT,
MODIFY `po_no` TEXT,
MODIFY `contact_mobile` TEXT,
MODIFY `contact_email` TEXT,
MODIFY `against_income_account` TEXT,
MODIFY `title` TEXT,
MODIFY `status` TEXT,
MODIFY `apply_discount_on` TEXT,
MODIFY `campaign` TEXT,
MODIFY `source` TEXT,
MODIFY `select_print_heading` TEXT,
MODIFY `mode_of_payment` TEXT,
MODIFY `shipping_rule` TEXT,
MODIFY `reason_for_issuing_document` TEXT,
MODIFY `invoice_type` TEXT,
MODIFY `export_type` TEXT,
MODIFY `transporters` TEXT,
MODIFY `transporter_name` TEXT,
MODIFY `driver_name` TEXT,
MODIFY `charge` TEXT,
MODIFY `in_words_export` TEXT,
MODIFY `shipping_location` TEXT,
MODIFY `ship_to` TEXT,
MODIFY `cancel_reason` TEXT,
MODIFY `delivery_note_main` TEXT,
MODIFY `sales_order_main` TEXT,
MODIFY `company_contact_person` TEXT,
MODIFY `gst_category` TEXT,
MODIFY `ewaybill` TEXT,
MODIFY `gst_vehicle_type` TEXT,
MODIFY `mode_of_transport` TEXT,
MODIFY `driver` TEXT,
MODIFY `set_warehouse` TEXT,
MODIFY `scan_barcode` TEXT,
MODIFY `inter_company_invoice_reference` TEXT,
MODIFY `loyalty_program` TEXT,
MODIFY `auto_repeat` TEXT,
MODIFY `subscription` TEXT;

-- Reduce varchar fields
ALTER TABLE `tabSales Invoice`
MODIFY `shipping_tin_no` varchar(50),
MODIFY `tin_no` varchar(50),
MODIFY `excise_no` varchar(50),
MODIFY `shipping_excise_no` varchar(50),
MODIFY `port_code` varchar(50),
MODIFY `ecommerce_gstin` varchar(50),
MODIFY `shipping_bill_number` varchar(50),
MODIFY `transporter` varchar(80),
MODIFY `gst_transporter_id` varchar(80),
MODIFY `vehicle_no` varchar(80);

-- Backup + trim po_no
ALTER TABLE `tabSales Invoice`
ADD COLUMN IF NOT EXISTS `po_no_backup` LONGTEXT;

UPDATE `tabSales Invoice`
SET po_no_backup = po_no
WHERE CHAR_LENGTH(po_no) > 188;

UPDATE `tabSales Invoice`
SET po_no = LEFT(po_no, 188)
WHERE CHAR_LENGTH(po_no) > 188;


-- =========================================
-- 4. PATCH FIXES / SKIPS
-- =========================================

-- Newsletter fix
ALTER TABLE `tabNewsletter`
ADD COLUMN IF NOT EXISTS `content_type` VARCHAR(50);

-- Workspace fix
UPDATE `tabWorkspace`
SET type = 'Workspace'
WHERE name = 'Rohit Common' AND (type IS NULL OR type = '');

-- Missing column
ALTER TABLE `tabProcess Statement Of Accounts`
ADD COLUMN IF NOT EXISTS `cc_to` TEXT;

-- Skip broken patches
INSERT IGNORE INTO `tabPatch Log` (name, patch)
VALUES
(UUID(), 'frappe.patches.v13_0.update_notification_channel_if_empty'),
(UUID(), 'erpnext.patches.v14_0.update_reports_with_range');

-- Company fixes (only missing ones)
ALTER TABLE `tabCompany`
ADD COLUMN IF NOT EXISTS `enable_perpetual_inventory_for_non_stock_items` INT(1) DEFAULT 0,
ADD COLUMN IF NOT EXISTS `service_received_but_not_billed` VARCHAR(255);