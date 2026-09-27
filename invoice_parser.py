"""Parse House of Spices style invoice PDFs into structured, searchable line items.

The invoice table uses fixed column bands (x positions on the page):

    SN | ITEM | DESCRIPTION | UPC | BARCODE | SHIPPED | PRICE | AMOUNT

A single logical row is spread over several physical text lines: the description
may wrap above/below the row that carries the SN, and the UPC/barcode always sit
on their own lines. The parser therefore groups words into lines, classifies each
word by its x position, and stitches the lines back into one record per SN.
"""

from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator

logger = logging.getLogger(__name__)

# Column boundaries in PDF points, derived from the invoice layout.
COL_SN = (0.0, 52.0)
COL_ITEM = (52.0, 110.0)
COL_DESC = (110.0, 282.0)
COL_UPC = (282.0, 312.0)
COL_BARCODE = (312.0, 430.0)
COL_SHIPPED = (430.0, 462.0)
COL_PRICE = (462.0, 520.0)
COL_AMOUNT = (520.0, 1000.0)

# "10X800 GM", "20X2 LB", "6X1.2 KG", "24 X 400 GM"
_PACK_RE = re.compile(
    r"(?P<packs>\d+)\s*[xX]\s*(?P<size>\d+(?:\.\d+)?)\s*(?P<unit>KG|GM|GMS|G|LB|LBS|OZ|ML|LTR|L|PCS|PC)\b"
)
_DIGITS_RE = re.compile(r"\d{8,14}")
_MONEY_RE = re.compile(r"^\d[\d,]*\.\d{2}$")


@dataclass
class PackSize:
    """Pack breakdown parsed out of a description, e.g. 10 units of 800 GM."""

    packs: int
    size: float
    unit: str

    @property
    def text(self) -> str:
        size = int(self.size) if float(self.size).is_integer() else self.size
        return f"{self.packs} x {size} {self.unit}"


@dataclass
class LineItem:
    """One invoice line."""

    sn: str
    item_code: str
    description: str
    upc: str
    barcode: str
    shipped: str
    price: str
    amount: str
    page: int
    pack_size: PackSize | None = None
    invoice_no: str = ""
    invoice_date: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["pack_size_text"] = self.pack_size.text if self.pack_size else ""
        data["total_units"] = (
            round(self.pack_size.packs * self.pack_size.size, 2) if self.pack_size else None
        )
        return data


@dataclass
class _Row:
    """Mutable accumulator used while stitching physical lines together."""

    sn: str = ""
    item_code: str = ""
    page: int = 0
    desc_parts: list[str] = field(default_factory=list)
    upc: str = ""
    barcode: str = ""
    shipped: str = ""
    price: str = ""
    amount: str = ""

    def closed(self) -> bool:
        """True once the barcode line has been consumed for this row."""
        return bool(self.barcode)


def _in(x: float, band: tuple[float, float]) -> bool:
    return band[0] <= x < band[1]


def _group_lines(words: Iterable[dict[str, Any]], tolerance: float = 2.5) -> list[list[dict[str, Any]]]:
    """Group extracted words into visual lines using their vertical position."""
    buckets: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for word in words:
        buckets[int(round(word["top"] / tolerance))].append(word)
    return [sorted(buckets[key], key=lambda w: w["x0"]) for key in sorted(buckets)]


def parse_pack_size(description: str) -> PackSize | None:
    """Extract the pack breakdown (count x size + unit) from a description."""
    match = _PACK_RE.search(description.replace("(", " ").replace(")", " "))
    if not match:
        return None
    unit = match.group("unit").upper()
    unit = {"GMS": "GM", "G": "GM", "LBS": "LB", "PC": "PCS"}.get(unit, unit)
    return PackSize(packs=int(match.group("packs")), size=float(match.group("size")), unit=unit)


def _extract_header(text: str) -> tuple[str, str]:
    """Pull invoice number and date out of the page header text."""
    invoice_no = re.search(r"INVOICE\s*No\.?\s*:\s*([A-Z0-9\-]+)", text or "", re.I)
    invoice_date = re.search(r"INVOICE\s*DATE\s*:\s*([\d/\-]+)", text or "", re.I)
    return (invoice_no.group(1) if invoice_no else "", invoice_date.group(1) if invoice_date else "")


def _finalize(row: _Row, invoice_no: str, invoice_date: str) -> LineItem:
    description = " ".join(part for part in row.desc_parts if part).strip()
    description = re.sub(r"\s{2,}", " ", description)
    return LineItem(
        sn=row.sn,
        item_code=row.item_code,
        description=description,
        upc=row.upc,
        barcode=row.barcode or row.upc,
        shipped=row.shipped,
        price=row.price,
        amount=row.amount,
        page=row.page,
        pack_size=parse_pack_size(description),
        invoice_no=invoice_no,
        invoice_date=invoice_date,
    )


def _is_line_item(item: LineItem) -> bool:
    """Reject header/address blocks that happen to start with a number."""
    return bool(item.barcode or item.upc) and bool(_MONEY_RE.match(item.price))


def _parse_page(page: Any, page_number: int) -> Iterator[LineItem]:
    invoice_no, invoice_date = _extract_header(page.extract_text() or "")
    current: _Row | None = None
    pending_desc: list[str] = []

    for line in _group_lines(page.extract_words()):
        sn_tokens = [w for w in line if _in(w["x0"], COL_SN) and w["text"].isdigit()]
        desc_tokens = [w for w in line if _in(w["x0"], COL_DESC)]
        upc_tokens = [w for w in line if _in(w["x0"], COL_UPC)]
        barcode_tokens = [w for w in line if _in(w["x0"], COL_BARCODE)]

        if sn_tokens:
            item_tokens = [w for w in line if _in(w["x0"], COL_ITEM)]
            if not item_tokens:  # header row or page furniture, not a line item
                pending_desc.clear()
                continue
            if current is not None:
                finished = _finalize(current, invoice_no, invoice_date)
                if _is_line_item(finished):
                    yield finished
            current = _Row(
                sn=sn_tokens[0]["text"],
                item_code=" ".join(w["text"] for w in item_tokens),
                page=page_number,
                desc_parts=pending_desc + [" ".join(w["text"] for w in desc_tokens)],
            )
            pending_desc = []
            shipped = [w["text"] for w in line if _in(w["x0"], COL_SHIPPED)]
            price = [w["text"] for w in line if _in(w["x0"], COL_PRICE)]
            amount = [w["text"] for w in line if _in(w["x0"], COL_AMOUNT)]
            current.shipped = shipped[0] if shipped else ""
            current.price = price[0] if price else ""
            current.amount = amount[0] if amount else ""
            continue

        if current is None:
            if desc_tokens:
                pending_desc.append(" ".join(w["text"] for w in desc_tokens))
            continue

        if upc_tokens and not current.upc:
            digits = _DIGITS_RE.search("".join(w["text"] for w in upc_tokens))
            if digits:
                current.upc = digits.group(0)
                continue

        if barcode_tokens and not current.barcode:
            digits = _DIGITS_RE.search("".join(w["text"] for w in barcode_tokens))
            if digits:
                current.barcode = digits.group(0)
                continue

        if desc_tokens:
            # Description text after the barcode belongs to the *next* line item.
            if current.closed():
                pending_desc.append(" ".join(w["text"] for w in desc_tokens))
            else:
                current.desc_parts.append(" ".join(w["text"] for w in desc_tokens))

    if current is not None:
        finished = _finalize(current, invoice_no, invoice_date)
        if _is_line_item(finished):
            yield finished


def parse_invoice(pdf_path: str | Path) -> list[LineItem]:
    """Parse every line item from the invoice PDF.

    Raises:
        FileNotFoundError: if the PDF does not exist.
        RuntimeError: if pdfplumber is not installed.
    """
    path = Path(pdf_path)
    if not path.is_file():
        raise FileNotFoundError(f"Invoice PDF not found: {path}")

    try:
        import pdfplumber  # imported lazily so the module can be introspected without the dep
    except ImportError as exc:  # pragma: no cover - environment specific
        raise RuntimeError("pdfplumber is required. Install it with: pip install pdfplumber") from exc

    items: list[LineItem] = []
    with pdfplumber.open(str(path)) as pdf:
        for page_number, page in enumerate(pdf.pages, start=1):
            try:
                items.extend(_parse_page(page, page_number))
            except Exception:  # keep parsing remaining pages if one page is malformed
                logger.exception("Failed to parse page %s of %s", page_number, path.name)
    logger.info("Parsed %s line items from %s", len(items), path.name)
    return items


class InvoiceIndex:
    """Lookup index over parsed line items, keyed by barcode, UPC and item code."""

    def __init__(self, items: list[LineItem], source: str = "") -> None:
        self.items = items
        self.source = source
        self._by_key: dict[str, list[LineItem]] = defaultdict(list)
        for item in items:
            keys = {self._normalize(k) for k in (item.barcode, item.upc, item.item_code) if k}
            for key in filter(None, keys):
                self._by_key[key].append(item)

    @staticmethod
    def _normalize(value: str) -> str:
        return re.sub(r"[^A-Za-z0-9]", "", value or "").upper()

    def lookup(self, code: str) -> list[LineItem]:
        """Exact match on barcode/UPC/item code, tolerating scanner prefix zeros."""
        key = self._normalize(code)
        if not key:
            return []
        if key in self._by_key:
            return list(self._by_key[key])
        # Scanners may emit a leading country digit (UPC-A -> EAN-13) or drop it.
        for candidate in (key.lstrip("0"), f"0{key}"):
            if candidate and candidate in self._by_key:
                return list(self._by_key[candidate])
        return []

    def search(self, text: str, limit: int = 50) -> list[LineItem]:
        """Free-text substring search across description, item code and codes."""
        needle = (text or "").strip().upper()
        if not needle:
            return []
        results = [
            item
            for item in self.items
            if needle in item.description.upper()
            or needle in item.item_code.upper()
            or needle in item.barcode
            or needle in item.upc
        ]
        return results[:limit]

    def to_json(self) -> str:
        return json.dumps([item.to_dict() for item in self.items], indent=2)


def load_index(pdf_path: str | Path) -> InvoiceIndex:
    """Parse the PDF and return a ready-to-query index."""
    return InvoiceIndex(parse_invoice(pdf_path), source=Path(pdf_path).name)


if __name__ == "__main__":  # pragma: no cover - manual inspection helper
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    cli = argparse.ArgumentParser(description="Dump invoice line items as JSON.")
    cli.add_argument("pdf", help="Path to the invoice PDF")
    args = cli.parse_args()
    print(load_index(args.pdf).to_json())
