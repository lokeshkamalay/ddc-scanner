"""Barcode scanner lookup UI for invoice PDFs.

Run:
    python app.py --pdf Laxmi1.pdf

Then open http://127.0.0.1:5000 and scan. A USB/Bluetooth barcode scanner acts as
a keyboard, so the scanned code lands in the always-focused search box and is
submitted automatically by the scanner's trailing Enter.
"""

from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path
from typing import Iterable

from flask import Flask, jsonify, render_template, request

from invoice_parser import InvoiceIndex, load_index

logger = logging.getLogger(__name__)

MAX_QUERY_LENGTH = 64

app = Flask(__name__)
app.config["INDEX"] = None


def get_index() -> InvoiceIndex:
    index = app.config.get("INDEX")
    if index is None:
        raise RuntimeError("Invoice index is not loaded")
    return index


@app.route("/")
def home() -> str:
    index = get_index()
    return render_template("index.html", source=index.source, item_count=len(index.items))


@app.route("/api/lookup")
def api_lookup():
    """Look up a scanned code; falls back to free-text search when no exact hit."""
    raw = (request.args.get("code") or "").strip()
    if not raw:
        return jsonify({"query": "", "match_type": "none", "results": []}), 400
    if len(raw) > MAX_QUERY_LENGTH:
        return jsonify({"error": "Query too long"}), 400

    results = get_index().lookup(raw)
    match_type = "exact"
    if not results:
        results = get_index().search(raw)
        match_type = "text" if results else "none"

    logger.info("lookup query=%r match=%s hits=%d", raw, match_type, len(results))
    return jsonify(
        {
            "query": raw,
            "match_type": match_type,
            "count": len(results),
            "results": [item.to_dict() for item in results],
        }
    )


@app.route("/api/health")
def api_health():
    index = app.config.get("INDEX")
    return jsonify({"ok": index is not None, "items": len(index.items) if index else 0})


def create_app(pdf_path: str | Path | Iterable[str | Path], vendor: str = "hos") -> Flask:
    """Build the app with the invoice PDFs parsed and indexed once at startup."""
    app.config["INDEX"] = load_index(pdf_path, vendor=vendor)
    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cli = argparse.ArgumentParser(description="Barcode lookup UI for an invoice PDF.")
    cli.add_argument("--pdf", nargs="+", default=[os.environ.get("INVOICE_PDF", "Laxmi1.pdf")],
                     help="One or more PDFs, or a folder containing PDFs for the selected vendor")
    cli.add_argument("--vendor", default=os.environ.get("INVOICE_VENDOR", "hos"),
                     choices=("hos", "balaji", "zeenat"), help="Invoice layout (Zeenat needs a sample PDF)")
    cli.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    cli.add_argument("--port", type=int, default=int(os.environ.get("PORT", "5000")))
    args = cli.parse_args()

    create_app(args.pdf, vendor=args.vendor)
    logger.info("Serving %s line items on http://%s:%s", len(get_index().items), args.host, args.port)
    app.run(host=args.host, port=args.port, debug=False)


if __name__ == "__main__":
    main()
