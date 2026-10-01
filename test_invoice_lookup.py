"""Unit tests for the invoice parser and the lookup API."""

from __future__ import annotations

from pathlib import Path

import pytest

import app as app_module
from invoice_parser import InvoiceIndex, LineItem, PackSize, parse_pack_size

PDF = Path(__file__).parent / "Laxmi1.pdf"
BALAJI_PDF = Path(__file__).parent / "Invoice_23832_from_BALAJI_WHOLESALE_FOODS_LLC.pdf"


def make_item(**kw) -> LineItem:
    defaults = dict(
        sn="1",
        item_code="1CHK5",
        description="LX. KASHMIRI CHILI POWDER 10X800 GM (NONGMO)",
        upc="723246111111",
        barcode="723246111111",
        shipped="1",
        price="80.00",
        amount="80.00",
        page=1,
        pack_size=PackSize(10, 800.0, "GM"),
    )
    defaults.update(kw)
    return LineItem(**defaults)


class TestParsePackSize:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("LX. KASHMIRI CHILI POWDER 10X800 GM (NONGMO)", (10, 800.0, "GM")),
            ("LX. DESI BESAN 20X2 LB", (20, 2.0, "LB")),
            ("LX. MILK RUSK 6X1.2 KG", (6, 1.2, "KG")),
            ("LX. SOMETHING 24 x 400 GMS", (24, 400.0, "GM")),
            ("LX. SOMETHING 12X500ML", (12, 500.0, "ML")),
        ],
    )
    def test_parses_pack_sizes(self, text, expected):
        pack = parse_pack_size(text)
        assert pack is not None
        assert (pack.packs, pack.size, pack.unit) == expected

    def test_returns_none_without_pack_info(self):
        assert parse_pack_size("LX. ASSORTED SWEETS") is None

    def test_text_property_drops_trailing_zero(self):
        assert PackSize(10, 800.0, "GM").text == "10 x 800 GM"
        assert PackSize(6, 1.2, "KG").text == "6 x 1.2 KG"

    def test_unitless_pack_without_guessing_a_unit(self):
        assert parse_pack_size("Gopuram Wick 12 * 1").text == "12 x 1"
        assert parse_pack_size("Adani Potato 20 * 1 400 gms") is None


class TestInvoiceIndex:
    @pytest.fixture
    def index(self) -> InvoiceIndex:
        second = make_item(
            sn="2",
            item_code="5RR2",
            description="LX. SOUTH INDIAN RICE RAVA 20X2 LB(NONGMO)",
            barcode="723246292472",
            upc="723246292472",
            pack_size=PackSize(20, 2.0, "LB"),
        )
        return InvoiceIndex([make_item(), second])

    def test_lookup_by_barcode(self, index):
        assert len(index.lookup("723246111111")) == 1

    def test_lookup_by_item_code_is_case_insensitive(self, index):
        assert index.lookup("1chk5")[0].item_code == "1CHK5"

    def test_lookup_tolerates_leading_zero_from_scanner(self, index):
        assert index.lookup("0723246111111")[0].barcode == "723246111111"

    def test_lookup_unknown_code_returns_empty(self, index):
        assert index.lookup("999999999999") == []

    def test_lookup_empty_query_returns_empty(self, index):
        assert index.lookup("  ") == []

    def test_search_matches_description_substring(self, index):
        assert len(index.search("kashmiri chili")) == 1

    def test_search_empty_returns_empty(self, index):
        assert index.search("") == []

    def test_to_dict_exposes_quantity_fields(self, index):
        data = index.items[0].to_dict()
        assert data["pack_size_text"] == "10 x 800 GM"
        assert data["total_units"] == 8000.0


@pytest.mark.skipif(not PDF.is_file(), reason="sample invoice PDF not available")
class TestRealInvoice:
    @pytest.fixture(scope="class")
    def index(self) -> InvoiceIndex:
        from invoice_parser import load_index

        return load_index(PDF)

    def test_extracts_line_items(self, index):
        assert len(index.items) > 100

    def test_every_item_has_a_barcode_and_price(self, index):
        assert all(item.barcode and item.price for item in index.items)

    def test_known_item_is_parsed_correctly(self, index):
        hits = [i for i in index.items if i.item_code == "1CHK5"]
        assert hits, "expected KASHMIRI CHILI POWDER 10X800 GM line"
        item = hits[0]
        assert "KASHMIRI CHILI POWDER" in item.description
        assert item.pack_size is not None
        assert item.pack_size.packs == 10
        assert item.pack_size.size == 800.0
        assert item.pack_size.unit == "GM"

    def test_barcodes_are_numeric(self, index):
        assert all(item.barcode.isdigit() for item in index.items)


@pytest.mark.skipif(not PDF.is_file(), reason="sample invoice PDF not available")
class TestApi:
    @pytest.fixture(scope="class")
    def client(self):
        flask_app = app_module.create_app(PDF)
        flask_app.config["TESTING"] = True
        return flask_app.test_client()

    def test_home_page_renders(self, client):
        assert client.get("/").status_code == 200

    def test_health(self, client):
        payload = client.get("/api/health").get_json()
        assert payload["ok"] is True and payload["items"] > 0

    def test_lookup_requires_code(self, client):
        assert client.get("/api/lookup?code=").status_code == 400

    def test_lookup_rejects_overlong_query(self, client):
        assert client.get("/api/lookup?code=" + "1" * 100).status_code == 400

    def test_lookup_returns_exact_match(self, client):
        payload = client.get("/api/lookup?code=723246293356").get_json()
        assert payload["match_type"] == "exact"
        assert payload["count"] >= 1

    def test_lookup_falls_back_to_text_search(self, client):
        payload = client.get("/api/lookup?code=KASHMIRI CHILI POWDER").get_json()
        assert payload["match_type"] == "text"
        assert payload["count"] >= 1

    def test_lookup_unknown_code(self, client):
        payload = client.get("/api/lookup?code=000000000001").get_json()
        assert payload["match_type"] == "none" and payload["count"] == 0


@pytest.mark.skipif(not BALAJI_PDF.is_file(), reason="Balaji sample invoice not available")
def test_balaji_first_row():
    from invoice_parser import load_index

    index = load_index(BALAJI_PDF, vendor="balaji")
    item = index.lookup("8904147414275")[0]
    assert item.item_code == "asri004"
    assert item.shipped == "2"
    assert item.price == "48.00"
    assert item.amount == "96.00"
    assert item.pack_size is not None
    assert item.pack_size.text == "10 x 4 LB"


@pytest.mark.skipif(not BALAJI_PDF.is_file(), reason="Balaji sample invoice not available")
def test_balaji_wrapped_rows_and_footer():
    from invoice_parser import load_index

    index = load_index(BALAJI_PDF, vendor="balaji")
    assert index.lookup("wbt500")[0].price == "108.00"
    assert index.lookup("asr800")[0].pack_size.text == "20 x 800 GM"
    assert index.lookup("asr800")[0].barcode == ""
    assert index.lookup("gwfdy012")[0].pack_size.text == "12 x 1"
    assert "Freight" not in index.items[-1].description
    assert index.items[-1].item_code == "psaio001"


@pytest.mark.skipif(not BALAJI_PDF.is_file(), reason="Balaji sample invoice not available")
def test_balaji_api_lookup():
    flask_app = app_module.create_app(BALAJI_PDF, vendor="balaji")
    flask_app.config["TESTING"] = True
    response = flask_app.test_client().get("/api/lookup?code=8904147414275")
    item = response.get_json()["results"][0]
    assert item["price"] == "48.00"
    assert item["shipped"] == "2"
    assert item["pack_size_text"] == "10 x 4 LB"


def test_unsupported_vendor_requires_sample():
    from invoice_parser import load_index

    with pytest.raises(ValueError, match="provide a sample PDF"):
        load_index(PDF, vendor="zeenat")


def test_multiple_invoices_preserve_distinct_results(tmp_path, monkeypatch):
    from invoice_parser import load_index

    first = tmp_path / "balaji-1.pdf"
    second = tmp_path / "balaji-2.pdf"
    first.touch()
    second.touch()

    def fake_parse(path, vendor):
        assert vendor == "balaji"
        return [make_item(price="48.00" if Path(path) == first else "52.00")]

    monkeypatch.setattr("invoice_parser.parse_invoice", fake_parse)
    client = app_module.create_app(tmp_path, vendor="balaji").test_client()
    response = client.get("/api/lookup?code=723246111111").get_json()
    assert response["count"] == 2
    assert [(item["source_pdf"], item["price"]) for item in response["results"]] == [
        ("balaji-1.pdf", "48.00"), ("balaji-2.pdf", "52.00")
    ]
    assert load_index([first, first, second], vendor="balaji").source == "2 PDFs"


def test_empty_invoice_folder_fails_clearly(tmp_path):
    from invoice_parser import load_index

    with pytest.raises(ValueError, match="No PDF files found"):
        load_index(tmp_path, vendor="balaji")
