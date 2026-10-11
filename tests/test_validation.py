"""Tests du pipeline de validation (spec Phase 7, complétés).

Les deux tests fournis par la spec (`test_valid_us_address`, `test_invalid_address`)
décrivent en réalité un test d'**intégration** : ils exigent des identifiants
SmartyStreets réels et un accès réseau. `test_valid_us_address` est donc marqué
`integration` et *skippé* sans identifiants ; `test_invalid_address` reste
exécutable hors-ligne car le chemin « fournisseur injoignable » doit de toute
façon produire `validated == False`, ce que le disjoncteur garantit.

Le chemin nominal (adresse valide) est couvert de façon déterministe par
`test_valid_address_with_mocked_provider`.
"""

import pytest

from app.config import get_settings
from app.validation import validate_full

settings = get_settings()

requires_provider = pytest.mark.skipif(
    not settings.smarty_auth_id,
    reason="Test d'intégration : nécessite SMARTY_AUTH_ID/TOKEN et un accès réseau.",
)


@requires_provider
@pytest.mark.integration
def test_valid_us_address():
    result = validate_full(
        {
            "street": "1600 Amphitheatre Parkway",
            "city": "Mountain View",
            "state": "CA",
            "zip": "94043",
            "country": "US",
        }
    )
    assert result["validated"] is True
    assert result["deliverability"] in ["commercial", "residential"]
    assert result["latitude"] is not None


def test_invalid_address():
    result = validate_full(
        {
            "street": "123 Fake Street",
            "city": "Nowhere",
            "state": "XX",
            "zip": "00000",
            "country": "US",
        }
    )
    assert result["validated"] is False


def test_abbreviation_standardization():
    result = validate_full(
        {
            "street": "123 Main St Apt 4B",
            "city": "New York",
            "state": "NY",
            "zip": "10001",
            "country": "US",
        }
    )
    assert "Street" in result["standardized"]["line1"]
    assert "Apartment 4B" in result["standardized"]["line2"]


def test_degraded_flag_set_when_provider_unreachable():
    """Fournisseur injoignable -> résultat honnêtement marqué `degraded`."""
    result = validate_full(
        {
            "street": "123 Main St",
            "city": "New York",
            "state": "NY",
            "zip": "10001",
            "country": "US",
        }
    )
    assert result["degraded"] is True
    assert result["deliverability"] == "invalid"
    assert result["confidence"] == 0.0


def test_valid_address_with_mocked_provider(monkeypatch):
    """Chemin nominal, déterministe et hors-ligne."""
    import app.validation as validation
    from app.validation import verifier

    monkeypatch.setattr(
        verifier,
        "_call_smarty",
        lambda *a, **k: [
            {"analysis": {"dpv_match_code": "Y"}, "metadata": {"lat": 37.42, "lon": -122.08}}
        ],
    )
    monkeypatch.setattr(
        validation, "geocode", lambda standardized: {"lat": 37.42, "lng": -122.08}
    )

    result = validate_full(
        {
            "street": "1600 Amphitheatre Parkway",
            "city": "Mountain View",
            "state": "CA",
            "zip": "94043",
            "country": "US",
        }
    )
    assert result["validated"] is True
    assert result["degraded"] is False
    assert result["deliverability"] == "residential"
    assert result["confidence"] == 1.0  # 0.99 + 0.05 bonus géocodage, plafonné à 1.0
    assert result["latitude"] == 37.42
