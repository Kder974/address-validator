"""Tests de la vérification des adresses françaises par la BAN (D79).

Ce que ces tests mesurent, et pourquoi :

  * une adresse dont le pays relève de la BAN (`FR`, `RE`, …) est vérifiée par
    la Base Adresse Nationale et **jamais** par SmartyStreets, qui ne sait pas
    juger une adresse française (mesuré le 17/10/2026 : il renvoie la saisie
    sans code DPV et le pipeline la déclarait « validée ») ;
  * une ville ou un code postal incohérent est **refusé** (`validated: false`) :
    « 8 rue madelaine, Lyon, 75005 » ne doit pas passer ;
  * un score BAN inférieur au seuil (0,8) est refusé ;
  * un résultat sans voie (`municipality`, `locality`) est refusé ;
  * une BAN injoignable donne `degraded: true`, jamais un verdict d'inexistence
    et jamais de repli sur SmartyStreets.

Aucun octet ne sort : `_call_ban` est remplacé par un double déterministe, comme
`_call_smarty` dans `tests/test_validation.py`.
"""

import pytest

from app.validation import ban, validate_full

ADRESSE_FR = {
    "street": "8 Rue de la Paix",
    "city": "Paris",
    "state": "IDF",
    "zip": "75002",
    "country": "FR",
}

ADRESSE_RE = {
    "street": "1 Rue de Paris",
    "city": "Saint-Denis",
    "state": "RE",
    "zip": "97400",
    "country": "RE",
}


def _feature(
    score: float = 0.964,
    postcode: str = "75002",
    city: str = "Paris",
    type_: str = "housenumber",
    label: str = "8 Rue de la Paix 75002 Paris",
) -> dict:
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [2.330992, 48.868831]},
        "properties": {
            "label": label,
            "score": score,
            "postcode": postcode,
            "city": city,
            "type": type_,
            "id": "75102_7225_00008",
        },
    }


@pytest.fixture(autouse=True)
def _geo_neutre(monkeypatch):
    """Pas de géocodage : la confiance mesurée est celle du seul verdict."""
    import app.validation as validation

    monkeypatch.setattr(
        validation, "geocode", lambda standardized: {"lat": None, "lng": None}
    )


def _ban_double(monkeypatch, features: list[dict]) -> list[tuple[str, str]]:
    appels: list[tuple[str, str]] = []

    def _stub(q: str, postcode: str) -> list[dict]:
        appels.append((q, postcode))
        return features

    monkeypatch.setattr(ban, "_call_ban", _stub)
    return appels


def _smarty_interdit(monkeypatch) -> None:
    from app.validation import verifier

    def _boom(*args, **kwargs):
        raise AssertionError(
            "SmartyStreets ne doit jamais être appelé pour une adresse française"
        )

    monkeypatch.setattr(verifier, "_call_smarty", _boom)


def test_une_adresse_francaise_valide_passe_par_la_ban_et_pas_par_smarty(monkeypatch):
    """Une adresse FR cohérente est validée par la BAN, sans toucher Smarty."""
    appels = _ban_double(monkeypatch, [_feature()])
    _smarty_interdit(monkeypatch)

    resultat = validate_full(ADRESSE_FR)

    assert resultat["validated"] is True
    assert resultat["degraded"] is False
    assert resultat["deliverability"] == "residential"
    assert resultat["confidence"] == 0.99
    assert appels == [("8 Rue de la Paix Paris", "75002")]


def test_une_ville_incoherente_est_refusee(monkeypatch):
    """Le cas signalé : « 8 rue madelaine, Lyon, 75005 » (75005 est à Paris)."""
    _smarty_interdit(monkeypatch)
    # La BAN, interrogée avec la ville saisie, ne trouve rien du tout.
    _ban_double(monkeypatch, [])

    resultat = validate_full(
        {
            "street": "8 rue madelaine",
            "city": "Lyon",
            "state": "ARA",
            "zip": "75005",
            "country": "FR",
        }
    )

    assert resultat["validated"] is False
    assert resultat["degraded"] is False
    assert resultat["deliverability"] == "invalid"
    assert resultat["confidence"] == 0.0


def test_une_ville_differente_refusee_meme_avec_un_score_eleve(monkeypatch):
    """La BAN trouve Paris : la ville saisie (Lyon) ne correspond pas -> refus."""
    _smarty_interdit(monkeypatch)
    _ban_double(monkeypatch, [_feature(score=0.95, city="Paris", postcode="75005")])

    resultat = validate_full(
        {
            "street": "8 rue madelaine",
            "city": "Lyon",
            "state": "ARA",
            "zip": "75005",
            "country": "FR",
        }
    )

    assert resultat["validated"] is False
    assert resultat["degraded"] is False


def test_un_code_postal_different_est_refuse(monkeypatch):
    """« 8 rue de la Paix » existe en 75002 : saisi en 75005, c'est un refus."""
    _smarty_interdit(monkeypatch)
    _ban_double(monkeypatch, [_feature(postcode="75002")])

    verification = ban.verify_ban(
        {
            "line1": "8 Rue de la Paix",
            "city": "Paris",
            "state": "IDF",
            "zip": "75005",
            "country": "FR",
        }
    )

    assert verification["valid"] is False
    assert "code postal" in verification["metadata"]["motif"]


def test_un_score_sous_le_seuil_est_refuse(monkeypatch):
    """Score 0,398 (mesuré sur « 8 rue madelaine » à Paris) < 0,8 -> refus."""
    _smarty_interdit(monkeypatch)
    _ban_double(
        monkeypatch,
        [_feature(score=0.398, label="8 Rue Linné 75005 Paris", postcode="75005")],
    )

    resultat = validate_full(
        {
            "street": "8 rue madelaine",
            "city": "Paris",
            "state": "IDF",
            "zip": "75005",
            "country": "FR",
        }
    )

    assert resultat["validated"] is False
    assert resultat["degraded"] is False


def test_un_resultat_sans_voie_est_refuse(monkeypatch):
    """`municipality` (la ville seule) n'est pas une adresse livrable."""
    _smarty_interdit(monkeypatch)
    _ban_double(
        monkeypatch,
        [_feature(score=0.97, type_="municipality", label="Paris 75002 Paris")],
    )

    resultat = validate_full(ADRESSE_FR)

    assert resultat["validated"] is False
    assert resultat["degraded"] is False


def test_la_ban_injoignable_est_degradee_sans_repli_smarty(monkeypatch):
    """Panne BAN : `degraded: true`, pas de verdict, et Smarty n'est pas appelé."""
    import httpx

    from app.validation import verifier

    def _ban_hors_service(*args, **kwargs):
        raise httpx.ConnectError("api-adresse.data.gouv.fr injoignable")

    monkeypatch.setattr(ban, "_call_ban", _ban_hors_service)

    def _boom(*args, **kwargs):
        raise AssertionError("aucun repli sur SmartyStreets n'est permis")

    monkeypatch.setattr(verifier, "_call_smarty", _boom)

    resultat = validate_full(ADRESSE_FR)

    assert resultat["validated"] is False
    assert resultat["degraded"] is True
    assert resultat["deliverability"] == "invalid"
    assert resultat["confidence"] == 0.0


def test_la_reunion_passe_par_la_ban(monkeypatch):
    """`RE` est un cas d'usage du service : la BAN couvre l'outre-mer."""
    appels = _ban_double(
        monkeypatch,
        [_feature(score=0.82, type_="street", city="Saint-Denis", postcode="97400",
                  label="Rue de Paris 97400 Saint-Denis")],
    )
    _smarty_interdit(monkeypatch)

    resultat = validate_full(ADRESSE_RE)

    assert resultat["validated"] is True
    assert resultat["degraded"] is False
    assert resultat["deliverability"] == "residential"
    assert resultat["confidence"] == 0.95
    assert appels == [("1 Rue de Paris Saint-Denis", "97400")]


def test_la_comparaison_de_ville_ignore_accents_et_casse(monkeypatch):
    """« SAINT-DENIS » saisi, « Saint-Denis » publié par la BAN : même ville."""
    _smarty_interdit(monkeypatch)
    _ban_double(
        monkeypatch,
        [_feature(score=0.90, type_="housenumber", city="Saint-Denis",
                  postcode="97400", label="1 Rue de Paris 97400 Saint-Denis")],
    )

    resultat = validate_full(
        {
            "street": "1 Rue de Paris",
            "city": "SAINT-DENIS",
            "state": "RE",
            "zip": "97400",
            "country": "RE",
        }
    )

    assert resultat["validated"] is True


def test_une_adresse_francaise_incomplete_ne_sort_pas(monkeypatch):
    """Voie ou code postal absent : refus local, aucun appel réseau."""
    appels = _ban_double(monkeypatch, [_feature()])

    verification = ban.verify_ban(
        {"line1": "", "city": "Paris", "state": "IDF", "zip": "75002", "country": "FR"}
    )

    assert verification["valid"] is False
    assert appels == []


# --- D84 : la voie, la ville et le code postal écrits comme la BAN les écrit ---


def _nommee(nom: str, type_: str = "housenumber") -> dict:
    feature = _feature(type_=type_)
    feature["properties"]["name"] = nom
    return feature


def test_une_adresse_reconnue_sort_ecrite_comme_la_ban(monkeypatch):
    _ban_double(monkeypatch, [_nommee("8 Rue de la Paix")])
    _smarty_interdit(monkeypatch)

    resultat = validate_full({**ADRESSE_FR, "street": "8 rue de la paix", "city": "paris"})

    assert resultat["standardized"]["line1"] == "8 Rue de la Paix"
    assert resultat["standardized"]["city"] == "Paris" and resultat["standardized"]["zip"] == "75002"


def test_une_voie_seule_garde_le_numero_saisi(monkeypatch):
    _ban_double(monkeypatch, [_nommee("Rue de la Paix", type_="street")])
    _smarty_interdit(monkeypatch)

    resultat = validate_full({**ADRESSE_FR, "street": "8 rue de la paix"})

    assert resultat["standardized"]["line1"] == "8 Rue de la Paix"
