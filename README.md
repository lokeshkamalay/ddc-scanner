# Invoice Barcode Lookup

Scan a product barcode and instantly get its invoice line from a PDF invoice:
item code, description, UPC, barcode, shipped qty, price, amount, and the pack
quantity broken out (e.g. `LX. KASHMIRI CHILI POWDER 10X800 GM` &rarr;
**10 x 800 GM = 8000 GM per case**).

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## Run

Start the server once (no PDFs or vendor flags needed):

```bash
python app.py
```

Open <http://127.0.0.1:5000>. Select HOS/Laxmi, Balaji, or Champs, choose one or
more PDF attachments, and click **Load invoices**. The scanner appears when
processing finishes. Upload a new batch from the same screen without restarting
the server. All attachments in one batch must be from the selected vendor.

Uploads are isolated by browser session. A new batch replaces only that browser's
batch; matching items in different PDFs remain separate, with a source filename.
Multiple tabs in the same browser share the batch. Uploaded files are removed
after parsing; the index stays in server memory for up to 8 hours. At most 20
uploaded batches are retained, so an older batch may be evicted. Re-upload after
expiry, eviction, or a server restart. Limit: 20 PDFs per upload, less than 49 MB
combined in the UI (50 MB total HTTP request limit).

Optional: preload files using the existing command-line workflow:

```bash
# One Laxmi/HOS invoice:
python app.py --vendor hos --pdf Laxmi1.pdf
# Multiple Laxmi/HOS invoices (only HOS-format PDFs in this folder):
python app.py --vendor hos --pdf laxmi-invoices/

# One Balaji invoice:
python app.py --vendor balaji --pdf Invoice_23832_from_BALAJI_WHOLESALE_FOODS_LLC.pdf
# Multiple Balaji invoices (only Balaji-format PDFs in this folder):
python app.py --vendor balaji --pdf balaji-invoices/
# Champs delivery slips:
python app.py --vendor champs --pdf champs.pdf
# For either vendor, you can also list PDFs explicitly (quote filenames with spaces):
python app.py --vendor balaji --pdf "balaji-1.pdf" "balaji-2.pdf" "balaji-3.pdf" "balaji-4.pdf"
```

Command-line PDFs are indexed at startup; restart to pick up changes to those files.
Matching barcodes can appear in more than one invoice; each result shows its source
filename so you can tell them apart. Preloaded files are visible to any browser
without an uploaded batch. Use UI-only startup for separate team uploads.

Options:

| Flag / env var        | Default       | Purpose                        |
| --------------------- | ------------- | ------------------------------ |
| `--pdf` / `INVOICE_PDF` | None | Optional PDFs or a folder to preload (`INVOICE_PDF` accepts one path) |
| `--vendor` / `INVOICE_VENDOR` | `hos` | Invoice layout: `hos`, `balaji` or `champs` |
| `--host` / `HOST`     | `127.0.0.1`   | Bind address                   |
| `--port` / `PORT`     | `5000`        | Port                           |

## How scanning works

Most USB/Bluetooth barcode scanners are *keyboard wedge* devices: they type the
code and press Enter. The search box is focused after an upload, so you can scan
immediately. Click the search box again after interacting with upload controls.

- **Enter** &mdash; search (sent automatically by the scanner)
- **Esc** &mdash; clear the box and results

If the scanned code is not on the invoice, the app falls back to a free-text
search so you can also type part of a product name (e.g. `kashmiri chili`) or an
item code (e.g. `1CHK5`).

The Balaji PDF has some products with no printed SKU/barcode number; those rows
can still be found by activity/item code or product name. `zeenat` is reserved
but not parsed yet: a sample Zeenat invoice is needed to map its columns.

Champs is a delivery slip, not a price invoice. Type an item number (such as `21`),
vendor item code (such as `ALST-80`), or part of its description to see its full
printed row, including quantity, delivery and pending counts. Champs does not print
barcodes or prices, so those fields are not shown. Repeated rows at page breaks are
shown once, using the later copy.

## API

| Endpoint             | Description                                              |
| -------------------- | -------------------------------------------------------- |
| `GET /`              | Scanner UI                                               |
| `POST /api/upload`   | Multipart `vendor` + multiple `files`; requires the page's `X-CSRF-Token` and session cookie |
| `GET /api/lookup?code=` | JSON lookup; `match_type` is `exact`, `text`, or `none` |
| `GET /api/health`    | Readiness + number of indexed line items                 |

Example:

```bash
curl "http://127.0.0.1:5000/api/lookup?code=723246293356"
```

```json
{
  "query": "723246293356",
  "match_type": "exact",
  "count": 1,
  "results": [
    {
      "sn": "1",
      "item_code": "2RT6",
      "description": "LX. MILK RUSK 6X1.2 KG",
      "upc": "723246293356",
      "barcode": "723246293356",
      "shipped": "1",
      "price": "39.00",
      "amount": "39.00",
      "pack_size_text": "6 x 1.2 KG",
      "total_units": 7.2,
      "invoice_no": "PSI-260904597",
      "invoice_date": "9/25/2026",
      "page": 1
    }
  ]
}
```

## Dumping the invoice as JSON

```bash
python invoice_parser.py Laxmi1.pdf --vendor hos > items.json
python invoice_parser.py Invoice_23832_from_BALAJI_WHOLESALE_FOODS_LLC.pdf --vendor balaji > balaji-items.json
```

## Tests

```bash
pytest -q
```

## How the parser works

The invoice table has fixed column bands (`SN | ITEM | DESCRIPTION | UPC |
BARCODE | SHIPPED | PRICE | AMOUNT`), but a logical row spans several physical
text lines &mdash; the description can wrap above or below the line carrying the
SN. [invoice_parser.py](invoice_parser.py) groups words into visual lines,
classifies each word by its x coordinate, and stitches the lines into one record
per SN. Rows without a barcode and a valid price (address blocks, page footers)
are discarded.

Each vendor has a separate page parser in [invoice_parser.py](invoice_parser.py).
New invoice layouts require a sample PDF and a vendor-specific parser.

## Notes

- PDFs are parsed at startup or on upload; exact lookups use an in-memory index.
- Bind to `127.0.0.1` (the default) unless you intend to expose the invoice data
  on your network. This app has no authentication, so do not run it on a public
  interface.
- The bundled Flask server is a development server. For long-running shared use,
  use a single-process WSGI server, with HTTPS and authentication added by your
  administrator. The upload cache is process-local; multiple workers are not
  supported without shared storage. Set `SECRET_KEY` in the environment for a
  stable session signing key. Do not expose this unauthenticated app publicly.
