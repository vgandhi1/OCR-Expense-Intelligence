// Dependency-free spreadsheet exporters for the receipts table.
// CSV is universally importable; the "Excel" export uses the SpreadsheetML 2003
// XML format (.xls) so it opens natively in Excel / LibreOffice / Google Sheets
// with a styled header and currency-formatted totals — no npm dependency required.

const COLUMNS = [
    { key: 'date', label: 'Date' },
    { key: 'merchant_name', label: 'Merchant' },
    { key: 'category', label: 'Category' },
    { key: 'currency', label: 'Currency' },
    { key: 'total_amount', label: 'Total', numeric: true },
];

const fmtDate = (value) => {
    if (!value) return '';
    const d = new Date(value);
    return Number.isNaN(d.getTime()) ? '' : d.toISOString().slice(0, 10);
};

const cellValue = (receipt, col) => {
    if (col.key === 'date') return fmtDate(receipt.date);
    if (col.key === 'merchant_name') return receipt.merchant_name || 'Unknown Merchant';
    if (col.key === 'category') return receipt.category || 'Uncategorized';
    if (col.key === 'currency') return receipt.currency || 'USD';
    if (col.key === 'total_amount') {
        return receipt.total_amount == null ? '' : Number(receipt.total_amount);
    }
    return '';
};

const stamp = () => new Date().toISOString().slice(0, 10);

const triggerDownload = (content, mime, filename) => {
    const blob = new Blob([content], { type: `${mime};charset=utf-8;` });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    // Revoke on next tick so the download has a chance to start.
    setTimeout(() => URL.revokeObjectURL(url), 0);
};

// --- CSV -------------------------------------------------------------------

const csvEscape = (value) => {
    const s = String(value ?? '');
    return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
};

export const exportReceiptsCsv = (receipts, filename = `receipts-${stamp()}.csv`) => {
    const header = COLUMNS.map((c) => csvEscape(c.label)).join(',');
    const rows = receipts.map((r) =>
        COLUMNS.map((c) => csvEscape(cellValue(r, c))).join(','),
    );
    // Prepend BOM so Excel detects UTF-8 for non-ASCII merchant names.
    const content = '\uFEFF' + [header, ...rows].join('\r\n');
    triggerDownload(content, 'text/csv', filename);
};

// --- Excel (SpreadsheetML 2003) -------------------------------------------

const xmlEscape = (value) =>
    String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');

const xmlCell = (value, numeric) => {
    if (numeric && value !== '' && value != null) {
        return `<Cell ss:StyleID="money"><Data ss:Type="Number">${value}</Data></Cell>`;
    }
    return `<Cell><Data ss:Type="String">${xmlEscape(value)}</Data></Cell>`;
};

export const exportReceiptsExcel = (receipts, filename = `receipts-${stamp()}.xls`) => {
    const headerRow =
        '<Row ss:StyleID="hdr">' +
        COLUMNS.map((c) => `<Cell><Data ss:Type="String">${xmlEscape(c.label)}</Data></Cell>`).join('') +
        '</Row>';

    const dataRows = receipts
        .map(
            (r) =>
                '<Row>' +
                COLUMNS.map((c) => xmlCell(cellValue(r, c), c.numeric)).join('') +
                '</Row>',
        )
        .join('');

    const total = receipts.reduce((acc, r) => acc + (Number(r.total_amount) || 0), 0);
    const footerRow =
        '<Row ss:StyleID="foot">' +
        '<Cell><Data ss:Type="String">Total</Data></Cell>' +
        '<Cell><Data ss:Type="String"></Data></Cell>' +
        '<Cell><Data ss:Type="String"></Data></Cell>' +
        '<Cell><Data ss:Type="String"></Data></Cell>' +
        `<Cell ss:StyleID="money"><Data ss:Type="Number">${total}</Data></Cell>` +
        '</Row>';

    const workbook =
        '<?xml version="1.0"?>\n' +
        '<?mso-application progid="Excel.Sheet"?>\n' +
        '<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet"' +
        ' xmlns:o="urn:schemas-microsoft-com:office:office"' +
        ' xmlns:x="urn:schemas-microsoft-com:office:excel"' +
        ' xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet">' +
        '<Styles>' +
        '<Style ss:ID="hdr"><Font ss:Bold="1" ss:Color="#FFFFFF"/>' +
        '<Interior ss:Color="#16A34A" ss:Pattern="Solid"/>' +
        '<Alignment ss:Vertical="Center"/></Style>' +
        '<Style ss:ID="foot"><Font ss:Bold="1"/><Borders>' +
        '<Border ss:Position="Top" ss:LineStyle="Continuous" ss:Weight="1"/></Borders></Style>' +
        '<Style ss:ID="money"><NumberFormat ss:Format="#,##0.00"/></Style>' +
        '</Styles>' +
        '<Worksheet ss:Name="Receipts"><Table>' +
        '<Column ss:Width="80"/><Column ss:Width="180"/><Column ss:Width="110"/>' +
        '<Column ss:Width="70"/><Column ss:Width="90"/>' +
        headerRow +
        dataRows +
        footerRow +
        '</Table></Worksheet></Workbook>';

    triggerDownload(workbook, 'application/vnd.ms-excel', filename);
};
