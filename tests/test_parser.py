"""Tests unitaires du parser (hors-ligne, purs)."""

from app.validation.parser import parse_address


def test_parse_simple_address():
    parsed = parse_address(
        {
            "street": "123 Main St",
            "city": "New York",
            "state": "ny",
            "zip": "10001",
            "country": "us",
        }
    )
    assert parsed["street_number"] == "123"
    assert parsed["street_name"] == "Main St"
    assert parsed["apartment"] == ""
    assert parsed["state"] == "NY"
    assert parsed["country"] == "US"


def test_parse_apartment_suffix():
    parsed = parse_address(
        {
            "street": "123 Main St Apt 4B",
            "city": "New York",
            "state": "NY",
            "zip": "10001",
            "country": "US",
        }
    )
    assert parsed["street_number"] == "123"
    assert parsed["apartment"] == "4B"
    assert "Apt" not in parsed["street_name"]
    assert parsed["street_name"] == "Main St"


def test_parse_hash_apartment():
    parsed = parse_address(
        {
            "street": "500 Fifth Ave #12",
            "city": "New York",
            "state": "NY",
            "zip": "10001",
            "country": "US",
        }
    )
    assert parsed["apartment"] == "12"
    assert parsed["street_name"] == "Fifth Ave"


def test_parse_no_street_number():
    parsed = parse_address(
        {
            "street": "Broadway",
            "city": "New York",
            "state": "NY",
            "zip": "10001",
            "country": "US",
        }
    )
    assert parsed["street_number"] == ""
    assert parsed["street_name"] == "Broadway"


def test_parse_country_defaults_to_us():
    parsed = parse_address({"street": "1 A St", "city": "X", "state": "CA", "zip": "90001"})
    assert parsed["country"] == "US"
