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


# --- D79 : un candidat n'est une preuve que s'il porte un code DPV valide -----


def _pipeline_avec_candidat(monkeypatch, candidat):
    """SmartyStreets rend un candidat imposé ; le géocodage reste neutre."""
    import app.validation as validation
    from app.validation import verifier

    monkeypatch.setattr(verifier, "_call_smarty", lambda *a, **k: [candidat])
    monkeypatch.setattr(
        validation, "geocode", lambda standardized: {"lat": None, "lng": None}
    )


def test_candidat_sans_dpv_est_degrade_pas_valide(monkeypatch):
    """D79 : Smarty renvoie la saisie d'une adresse étrangère sans code DPV.

    Ce n'est pas une preuve d'existence : le résultat est `degraded`, jamais
    `validated` (c'est le défaut mesuré sur « 8 rue madelaine, Lyon, 75005 »).
    """
    _pipeline_avec_candidat(monkeypatch, {"analysis": {}, "metadata": {}})

    result = validate_full(
        {
            "street": "8 rue madelaine",
            "city": "Lyon",
            "state": "",
            "zip": "75005",
            "country": "US",
        }
    )

    assert result["validated"] is False
    assert result["degraded"] is True


def test_un_dpv_n_ne_valide_pas_l_adresse(monkeypatch):
    """D79 : DPV `N` (inexistant) était déclaré « validée » avant le correctif."""
    _pipeline_avec_candidat(monkeypatch, {"analysis": {"dpv_match_code": "N"}, "metadata": {}})

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
    assert result["degraded"] is False
    assert result["deliverability"] == "invalid"
    assert result["confidence"] == 0.0


def test_un_dpv_x_ne_valide_pas_l_adresse(monkeypatch):
    """DPV `X` (inconnu) ne prouve pas que l'adresse existe."""
    _pipeline_avec_candidat(monkeypatch, {"analysis": {"dpv_match_code": "X"}, "metadata": {}})

    result = validate_full(
        {
            "street": "1 Rue Inconnue",
            "city": "Somewhere",
            "state": "CA",
            "zip": "90001",
            "country": "US",
        }
    )

    assert result["validated"] is False
    assert result["degraded"] is False
    assert result["deliverability"] == "invalid"


# --- D84 : une adresse reconnue sort telle que le fournisseur l'a corrigée ----

_CORRIGEE = {
    "delivery_line_1": "100 Stewart St",
    "components": {"city_name": "Seattle", "state_abbreviation": "WA", "zipcode": "98101"},
    "metadata": {"rdi": "Commercial"},
    "analysis": {"dpv_match_code": "Y"},
}
_SAISIE = {"street": "100 stewart st", "city": "seatle", "state": "wa", "zip": "", "country": "US"}


def test_une_adresse_reconnue_sort_corrigee_par_le_fournisseur(monkeypatch):
    _pipeline_avec_candidat(monkeypatch, _CORRIGEE)

    resultat = validate_full(_SAISIE)

    assert resultat["validated"] is True
    assert resultat["standardized"] == {"line1": "100 Stewart Street", "line2": "", "city": "Seattle",
                                        "state": "WA", "zip": "98101", "country": "US"}


def test_une_adresse_refusee_garde_la_saisie(monkeypatch):
    _pipeline_avec_candidat(monkeypatch, {**_CORRIGEE, "analysis": {"dpv_match_code": "N"}})

    resultat = validate_full(_SAISIE)

    assert resultat["validated"] is False
    assert resultat["standardized"]["city"] == "Seatle" and resultat["standardized"]["zip"] == ""


# --- D85 : maison ou entreprise, c'est le RDI de Smarty qui le dit -----------

from app.validation.scorer import score_deliverability  # noqa: E402

_SANS_GPS = {"lat": None, "lng": None}


@pytest.mark.parametrize("dpv, rdi, attendu", [
    ("Y", "Commercial", ("commercial", 0.99)),
    ("Y", "Residential", ("residential", 0.99)),
    ("D", "Residential", ("residential", 0.95)),
    ("D", "", ("residential", 0.95)),
    ("Y", "", ("residential", 0.99)),
    ("N", "Commercial", ("invalid", 0.00)),
])
def test_la_nature_du_lieu_vient_du_rdi(dpv, rdi, attendu):
    verification = {"valid": dpv != "N", "deliverability": dpv, "metadata": {"rdi": rdi}}
    score = score_deliverability(verification, _SANS_GPS)
    assert (score["deliverability"], score["confidence"]) == attendu


def test_seule_une_panne_du_fournisseur_est_injoignable(monkeypatch):
    """D103 : une panne bloque l'encaissement x402 ; une adresse que le fournisseur ne sait pas juger, non."""
    import app.validation as validation
    from app.validation import ban, verifier

    def panne(*args, **kwargs):
        raise RuntimeError("connexion coupee")

    monkeypatch.setattr(validation, "geocode", lambda standardized: {"lat": None, "lng": None})
    us = {"street": "1 Main St", "city": "Austin", "state": "TX", "zip": "78701", "country": "US"}
    try:
        monkeypatch.setattr(verifier, "_call_smarty", panne)
        assert validation.validate_full(us)["injoignable"] is True
        monkeypatch.setattr(verifier, "_call_smarty", lambda *args: [{"analysis": {}, "metadata": {}}])
        hors_couverture = validation.validate_full({**us, "country": "DE"})
        assert hors_couverture["degraded"] is True and hors_couverture["injoignable"] is False
    finally:
        verifier.breaker.reset()
    monkeypatch.setattr(ban, "_call_ban", panne)
    francaise = validation.validate_full({"street": "5 rue des ecoles", "city": "paris", "zip": "75005", "country": "FR"})
    assert francaise["degraded"] is True and francaise["injoignable"] is True


@pytest.mark.parametrize(("statut", "bloque"), [(429, 1), (403, 1), (500, 0)])
def test_un_refus_de_nominatim_est_compte(monkeypatch, statut, bloque):
    """D109 : 429 et 403 = la carte nous limite ou nous bloque ; la veille du matin le dit."""
    import httpx

    from app.routes.metrics import metrics
    from app.validation import geocoder

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def get(self, url, params=None, headers=None):
            return httpx.Response(statut, request=httpx.Request("GET", url))

    avant = metrics.snapshot()
    monkeypatch.setattr(geocoder.httpx, "Client", Client)
    assert geocoder.geocode({"line1": "1 Main St", "city": "Austin", "country": "US"}) == {"lat": None, "lng": None}
    apres = metrics.snapshot()
    assert apres["geocode_errors"] == avant.get("geocode_errors", 0) + 1
    assert apres["geocode_blocked"] == avant.get("geocode_blocked", 0) + bloque
