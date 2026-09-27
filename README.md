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

```bash
python app.py --pdf Laxmi1.pdf
```

Open <http://127.0.0.1:5000> and scan.

Options:

| Flag / env var        | Default       | Purpose                        |
| --------------------- | ------------- | ------------------------------ |
| `--pdf` / `INVOICE_PDF` | `Laxmi1.pdf` | Invoice PDF to index           |
| `--host` / `HOST`     | `127.0.0.1`   | Bind address                   |
| `--port` / `PORT`     | `5000`        | Port                           |

## How scanning works

Most USB/Bluetooth barcode scanners are *keyboard wedge* devices: they type the
code and press Enter. The search box keeps itself focused, so you just scan and
the result appears. No driver or scanner configuration is needed.

- **Enter** &mdash; search (sent automatically by the scanner)
- **Esc** &mdash; clear the box and results

If the scanned code is not on the invoice, the app falls back to a free-text
search so you can also type part of a product name (e.g. `kashmiri chili`) or an
item code (e.g. `1CHK5`).

## API

| Endpoint             | Description                                              |
| -------------------- | -------------------------------------------------------- |
| `GET /`              | Scanner UI                                               |
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
python invoice_parser.py Laxmi1.pdf > items.json
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

If a different vendor's invoice uses a different layout, adjust the `COL_*`
column bands at the top of [invoice_parser.py](invoice_parser.py).

## Notes

- The PDF is parsed once at startup and held in memory; lookups are O(1).
- Bind to `127.0.0.1` (the default) unless you intend to expose the invoice data
  on your network. This app has no authentication, so do not run it on a public
  interface.
- The bundled Flask server is a development server. For multi-user or
  long-running use, put it behind a WSGI server such as gunicorn or waitress.
