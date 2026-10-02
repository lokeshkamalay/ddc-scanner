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
import secrets
import tempfile
import time
from collections import OrderedDict
from pathlib import Path
from threading import Lock
from typing import Iterable

from flask import Flask, jsonify, render_template, request, send_file, session
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.utils import secure_filename

from invoice_parser import InvoiceIndex, load_index

logger = logging.getLogger(__name__)

MAX_QUERY_LENGTH = 64

app = Flask(__name__)
app.config["INDEX"] = None
app.config.update(
    SECRET_KEY=os.environ.get("SECRET_KEY") or secrets.token_hex(32),
    MAX_CONTENT_LENGTH=50 * 1024 * 1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Strict",
)
uploaded_indexes: OrderedDict[str, tuple[float, InvoiceIndex]] = OrderedDict()
index_lock = Lock()
MAX_BATCHES = 20
BATCH_TTL = 8 * 60 * 60


def current_index() -> InvoiceIndex | None:
    """Return this browser's uploaded batch or the optional startup index."""
    batch_id = session.get("batch_id")
    if not batch_id:
        return app.config.get("INDEX")
    with index_lock:
        entry = uploaded_indexes.get(batch_id)
        if entry is None or time.monotonic() - entry[0] > BATCH_TTL:
            uploaded_indexes.pop(batch_id, None)
            return None
        uploaded_indexes.move_to_end(batch_id)
        return entry[1]


def get_index() -> InvoiceIndex:
    index = current_index()
    if index is None:
        raise RuntimeError("Invoice index is not loaded")
    return index


@app.route("/brand-logo.jpg")
def brand_logo():
    """Serve the Desi District logo without exposing the invoice directory."""
    return send_file(Path(app.root_path) / "logo.jpg", mimetype="image/jpeg")


@app.route("/")
def home() -> str:
    index = current_index()
    session.setdefault("csrf_token", secrets.token_urlsafe(32))
    return render_template("index.html", source=index.source if index else "No invoices loaded",
                           item_count=len(index.items) if index else 0,
                           csrf_token=session["csrf_token"])


@app.errorhandler(RequestEntityTooLarge)
def upload_too_large(error):
    """Return a UI-readable error for requests exceeding the upload limit."""
    return jsonify({"error": "Upload exceeds the 50 MB batch limit."}), 413


@app.route("/api/upload", methods=["POST"])
def api_upload():
    """Validate and parse a batch of vendor PDFs for this browser only."""
    token = request.headers.get("X-CSRF-Token", "")
    expected = session.get("csrf_token", "")
    if not expected or not secrets.compare_digest(token, expected):
        return jsonify({"error": "Refresh the page before uploading."}), 403
    vendor = request.form.get("vendor", "")
    if vendor not in ("hos", "balaji", "champs"):
        return jsonify({"error": "Select HOS/Laxmi, Balaji, or Champs."}), 400
    files = request.files.getlist("files")
    if not 1 <= len(files) <= 20:
        return jsonify({"error": "Select between 1 and 20 PDF files."}), 400

    try:
        with tempfile.TemporaryDirectory(prefix="invoice-upload-") as directory:
            paths = []
            names = set()
            for attachment in files:
                filename = secure_filename(attachment.filename or "")
                if not filename or Path(filename).suffix.lower() != ".pdf":
                    return jsonify({"error": "Only PDF attachments are accepted."}), 400
                if filename.lower() in names:
                    return jsonify({"error": "Each attachment must have a unique filename."}), 400
                names.add(filename.lower())
                if attachment.stream.read(5) != b"%PDF-":
                    return jsonify({"error": f"{filename} is not a valid PDF."}), 400
                attachment.stream.seek(0)
                path = Path(directory) / filename
                attachment.save(path)
                paths.append(path)
            index = load_index(paths, vendor=vendor)
    except Exception:
        logger.exception("Invoice upload failed vendor=%s files=%d", vendor, len(files))
        return jsonify({"error": "Cannot read this batch. Check the selected vendor and use text-based, unencrypted PDFs."}), 422

    batch_id = secrets.token_urlsafe(32)
    with index_lock:
        previous = session.get("batch_id")
        if previous:
            uploaded_indexes.pop(previous, None)
        now = time.monotonic()
        for key, entry in list(uploaded_indexes.items()):
            if now - entry[0] > BATCH_TTL:
                uploaded_indexes.pop(key)
        uploaded_indexes[batch_id] = (now, index)
        while len(uploaded_indexes) > MAX_BATCHES:
            uploaded_indexes.popitem(last=False)
    session["batch_id"] = batch_id
    logger.info("Invoice batch loaded vendor=%s files=%d items=%d", vendor, len(files), len(index.items))
    return jsonify({"source": index.source, "items": len(index.items), "files": len(files)})


@app.route("/api/lookup")
def api_lookup():
    """Look up a scanned code; falls back to free-text search when no exact hit."""
    raw = (request.args.get("code") or "").strip()
    if not raw:
        return jsonify({"query": "", "match_type": "none", "results": []}), 400
    if len(raw) > MAX_QUERY_LENGTH:
        return jsonify({"error": "Query too long"}), 400

    index = current_index()
    if index is None:
        return jsonify({"error": "Upload your invoices to start scanning. Your previous batch may have expired."}), 409
    results = index.lookup(raw)
    match_type = "exact"
    if not results:
        results = index.search(raw)
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
    index = current_index()
    return jsonify({"ok": index is not None, "items": len(index.items) if index else 0})


def create_app(pdf_path: str | Path | Iterable[str | Path] | None = None, vendor: str = "hos") -> Flask:
    """Build the app with the invoice PDFs parsed and indexed once at startup."""
    app.config["INDEX"] = load_index(pdf_path, vendor=vendor) if pdf_path else None
    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cli = argparse.ArgumentParser(description="Barcode lookup UI for an invoice PDF.")
    cli.add_argument("--pdf", nargs="+", default=[os.environ["INVOICE_PDF"]] if os.environ.get("INVOICE_PDF") else None,
                     help="One or more PDFs, or a folder containing PDFs for the selected vendor")
    cli.add_argument("--vendor", default=os.environ.get("INVOICE_VENDOR", "hos"),
                     choices=("hos", "balaji", "champs", "zeenat"), help="Invoice layout (Zeenat needs a sample PDF)")
    cli.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    cli.add_argument("--port", type=int, default=int(os.environ.get("PORT", "5000")))
    args = cli.parse_args()

    create_app(args.pdf, vendor=args.vendor)
    logger.info("Scanner UI available on http://%s:%s", args.host, args.port)
    app.run(host=args.host, port=args.port, debug=False)


if __name__ == "__main__":
    main()
