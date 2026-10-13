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


def _parse(street: str, country: str = "US") -> dict:
    return parse_address({"street": street, "city": "X", "state": "CA", "zip": "90001", "country": country})


def test_parse_unit_keywords_are_whole_words():
    # D51 : "Stewart" ne contient plus "ste", "Florida" ne contient plus "fl"
    for rue in ("100 Stewart St", "1 Florida Ave"):
        assert _parse(rue)["apartment"] == ""
    assert _parse("100 Stewart St")["street_name"] == "Stewart St"
    assert _parse("1 Florida Ave")["street_name"] == "Florida Ave"


def test_parse_hash_glued_or_spaced():
    assert _parse("500 Fifth Ave #12")["apartment"] == "12"
    assert _parse("500 Fifth Ave # 12")["apartment"] == "12"


def test_parse_non_us_keeps_street_and_no_unit():
    parsed = _parse("12 Résidence Les Flamboyants", "RE")
    assert parsed["street_name"] == "Résidence Les Flamboyants"
    assert parsed["apartment"] == ""
    assert _parse("5  Rue   St Joseph", "RE")["street_name"] == "Rue St Joseph"
