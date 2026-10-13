"""Tests unitaires du standardizer (hors-ligne, purs)."""

from app.validation.parser import parse_address
from app.validation.standardizer import standardize_address


def _standardize(raw: dict) -> dict:
    return standardize_address(parse_address(raw))


def test_abbreviations_are_expanded():
    out = _standardize(
        {
            "street": "123 Main St Apt 4B",
            "city": "new york",
            "state": "ny",
            "zip": "10001-1234",
            "country": "US",
        }
    )
    assert out["line1"] == "123 Main Street"
    assert out["line2"] == "Apartment 4B"
    assert out["city"] == "New York"
    assert out["state"] == "NY"
    assert out["zip"] == "10001"  # ZIP+4 tronqué à 5 pour les US


def test_directional_abbreviations():
    out = _standardize(
        {
            "street": "100 N Main St",
            "city": "Austin",
            "state": "TX",
            "zip": "78701",
            "country": "US",
        }
    )
    assert out["line1"] == "100 North Main Street"


def test_non_us_zip_is_preserved():
    out = _standardize(
        {
            "street": "10 Rue de la Paix",
            "city": "Paris",
            "state": "IDF",
            "zip": "75002",
            "country": "FR",
        }
    )
    assert out["zip"] == "75002"
    assert out["country"] == "FR"
    assert out["line2"] == ""


def test_unknown_word_is_capitalized():
    out = _standardize(
        {
            "street": "1 grange road",
            "city": "Springfield",
            "state": "IL",
            "zip": "62701",
            "country": "US",
        }
    )
    assert out["line1"] == "1 Grange Road"


def _line(street: str, country: str = "US") -> dict:
    return _standardize({"street": street, "city": "X", "state": "CA", "zip": "90001", "country": country})


def test_us_words_containing_unit_keywords_are_not_mangled():
    # D51 : mesuré avant correctif, "100 Street" + "Apartment wart"
    out = _line("100 Stewart St")
    assert out["line1"] == "100 Stewart Street"
    assert out["line2"] == ""
    out = _line("1 Florida Ave")
    assert out["line1"] == "1 Florida Avenue"
    assert out["line2"] == ""


def test_non_us_street_is_kept_as_typed():
    out = _line("12 Résidence Les Flamboyants", "RE")
    assert out["line1"] == "12 Résidence Les Flamboyants"
    assert out["line2"] == ""
    out = _line("5 Rue St Joseph", "RE")
    assert out["line1"] == "5 Rue St Joseph"
    assert out["line2"] == ""


def test_us_leading_st_is_saint_otherwise_street():
    assert _line("123 St Charles Ave")["line1"] == "123 St Charles Avenue"
    assert _line("123 Main St")["line1"] == "123 Main Street"


def test_us_hash_unit_still_extracted():
    out = _line("500 Fifth Ave #12")
    assert out["line1"] == "500 Fifth Avenue"
    assert out["line2"] == "Apartment 12"
