"""Tests de l'essai gratuit public `GET`/`POST /validate-form` (décision D46).

Ce que ces tests mesurent, et pourquoi :

  * la page **publique** est indexable et cachable, mais elle ne charge **rien** :
    aucune ressource externe, aucun script — c'est la première page qu'un visiteur
    voit, et le plafond comme le plafond du fournisseur sont écrits dessus ;
  * dès qu'un `POST` a eu lieu, la réponse contient l'**adresse du visiteur** :
    elle est `no-store` et `noindex`. Une page d'essai en cache serait une adresse
    gardée par un proxy ;
  * une adresse incomplète est refusée **avant** d'interroger le fournisseur :
    aucun essai gratuit ne doit coûter un appel payant pour rien ;
  * le onzième essai de la journée vaut `429`, avec un `Retry-After` **exact** et
    une porte de sortie (créer un compte) : un plafond muet serait un mur ;
  * un refus de plafond **ne compte pas** comme un essai : un visiteur bloqué ne
    repousse pas lui-même la fin de sa fenêtre ;
  * le pipeline est **celui de la route payante** (même objet), donc l'essai et le
    service vendu ne peuvent pas rendre deux verdicts différents ;
  * un résultat **dégradé** n'est pas présenté comme une adresse invalide : la
    panne d'un tiers n'est pas un jugement sur l'adresse du visiteur ;
  * la valeur saisie est **échappée** : le formulaire réaffiche ce que le visiteur
    a tapé, jamais du HTML.

Aucun appel réseau, aucune base : le pipeline est remplacé et le plafond est remis
à zéro avant chaque test.
"""

import pytest
from fastapi.testclient import TestClient

from app import ratelimit
from app.config import get_settings
from app.main import app
from app.routes import validate as validate_route
from app.routes import validate_form as validate_form_route
from app.routes.metrics import metrics

client = TestClient(app)

RESULTAT = {
    "validated": True,
    "standardized": {
        "line1": "1 Rue de Paris",
        "line2": "",
        "city": "Saint-Denis",
        "state": "RE",
        "zip": "97400",
        "country": "RE",
    },
    "latitude": -20.88,
    "longitude": 55.45,
    "deliverability": "residential",
    "confidence": 0.99,
    "degraded": False,
}

ADRESSE = {
    "street": "1 Rue de Paris",
    "city": "Saint-Denis",
    "state": "RE",
    "zip": "97400",
    "country": "RE",
}


@pytest.fixture(autouse=True)
def plafond_vide():
    """Fenêtre de plafond remise à zéro : un test ne déborde pas sur le suivant."""
    ratelimit.reset()
    yield
    ratelimit.reset()


def _pipeline(monkeypatch, resultat: dict | None = None, appels: list | None = None):
    """Remplace le pipeline partagé : aucun appel au fournisseur n'est fait."""

    def _valide(adresse: dict) -> tuple[dict, str]:
        if appels is not None:
            appels.append(dict(adresse))
        return dict(resultat or RESULTAT), "cle-de-cache"

    monkeypatch.setattr(validate_form_route, "perform_validation", _valide)


def _limite(monkeypatch, essais: int):
    """Réglage du plafond, pour ne pas poster onze fois dans un test."""
    reglages = get_settings().model_copy(
        update={"validate_form_rate_limit_per_day": essais}
    )
    monkeypatch.setattr(validate_form_route, "get_settings", lambda: reglages)
    return reglages


def test_la_page_est_publique_indexable_et_ne_charge_rien():
    page = client.get("/validate-form")

    assert page.status_code == 200
    assert page.headers["cache-control"] == "public, max-age=300"
    assert "form-action 'self'" in page.headers["content-security-policy"]
    assert 'name="robots" content="index, follow"' in page.text
    for nom in ("street", "city", "state", "zip", "country"):
        assert f'name="{nom}"' in page.text
    assert 'action="/validate-form"' in page.text
    # Autonome : rien à charger, donc rien qui puisse suivre le visiteur.
    assert "<script" not in page.text
    assert "http://" not in page.text and "https://" not in page.text
    assert 'href="/signup"' in page.text and 'href="/pricing"' in page.text


def test_un_essai_valide_affiche_le_resultat(monkeypatch):
    appels: list[dict] = []
    _pipeline(monkeypatch, appels=appels)
    avant = metrics.snapshot()

    reponse = client.post("/validate-form", data=ADRESSE)

    assert reponse.status_code == 200
    # La réponse contient l'adresse du visiteur : jamais de cache, jamais d'index.
    assert reponse.headers["cache-control"] == "no-store"
    assert 'name="robots" content="noindex, nofollow"' in reponse.text
    assert "Adresse reconnue" in reponse.text
    assert "1 Rue de Paris" in reponse.text and "Saint-Denis" in reponse.text
    assert "residential" in reponse.text
    # Le pipeline reçoit exactement les cinq champs, et une seule fois.
    assert appels == [ADRESSE]
    apres = metrics.snapshot()
    assert apres["validate_form_requests"] - avant["validate_form_requests"] == 1
    assert apres["validate_form_validations"] - avant["validate_form_validations"] == 1


def test_une_adresse_incomplete_est_refusee_sans_interroger_le_fournisseur(monkeypatch):
    appels: list[dict] = []
    _pipeline(monkeypatch, appels=appels)

    reponse = client.post("/validate-form", data={**ADRESSE, "street": "  "})

    assert reponse.status_code == 400
    assert reponse.headers["cache-control"] == "no-store"
    assert "Nombre de rue" in reponse.text or "Numéro et rue" in reponse.text
    # Ce que le visiteur a tapé est réaffiché, pour qu'il corrige au lieu de tout
    # ressaisir — mais rien n'est calculé pour rien.
    assert "Saint-Denis" in reponse.text
    assert appels == []


def test_un_pays_sans_deux_lettres_est_refuse(monkeypatch):
    _pipeline(monkeypatch)

    reponse = client.post("/validate-form", data={**ADRESSE, "country": "France"})

    assert reponse.status_code == 400
    assert "deux lettres" in reponse.text


def test_le_pays_vide_prend_la_valeur_par_defaut(monkeypatch):
    appels: list[dict] = []
    _pipeline(monkeypatch, appels=appels)

    reponse = client.post("/validate-form", data={**ADRESSE, "country": ""})

    assert reponse.status_code == 200
    assert appels and appels[0]["country"] == "US"


def test_la_valeur_saisie_est_echappee(monkeypatch):
    """Le formulaire réaffiche la saisie : elle ne peut pas devenir du HTML."""
    _pipeline(monkeypatch)

    reponse = client.post(
        "/validate-form", data={**ADRESSE, "street": '<script>alert(1)</script>'}
    )

    assert reponse.status_code == 200
    assert "<script>" not in reponse.text
    assert "&lt;script&gt;" in reponse.text


def test_le_onzieme_essai_vaut_429_et_dit_quand_reessayer(monkeypatch):
    _pipeline(monkeypatch)
    _limite(monkeypatch, 2)
    avant = metrics.snapshot()

    assert client.post("/validate-form", data=ADRESSE).status_code == 200
    assert client.post("/validate-form", data=ADRESSE).status_code == 200
    bloque = client.post("/validate-form", data=ADRESSE)

    assert bloque.status_code == 429
    assert bloque.headers["cache-control"] == "no-store"
    attente = int(bloque.headers["retry-after"])
    # Exact, pas décoratif : la fenêtre dure 24 h, et elle a déjà commencé.
    assert 0 < attente <= 24 * 60 * 60
    assert "2 essais gratuits" in bloque.text
    # La porte de sortie est sur la page, pas seulement dans un journal.
    assert 'href="/signup"' in bloque.text
    assert "minute" in bloque.text
    apres = metrics.snapshot()
    assert apres["validate_form_denied"] - avant["validate_form_denied"] == 1


def test_un_refus_ne_compte_pas_comme_un_essai(monkeypatch):
    """Un visiteur bloqué ne repousse pas lui-même la fin de sa fenêtre."""
    _pipeline(monkeypatch)
    _limite(monkeypatch, 1)

    assert client.post("/validate-form", data=ADRESSE).status_code == 200
    premier = client.post("/validate-form", data=ADRESSE)
    second = client.post("/validate-form", data=ADRESSE)

    assert premier.status_code == 429 and second.status_code == 429
    assert int(second.headers["retry-after"]) <= int(premier.headers["retry-after"])


def test_le_plafond_est_par_ip(monkeypatch):
    _pipeline(monkeypatch)
    _limite(monkeypatch, 1)

    une = client.post(
        "/validate-form", data=ADRESSE, headers={"X-Forwarded-For": "203.0.113.7"}
    )
    autre = client.post(
        "/validate-form", data=ADRESSE, headers={"X-Forwarded-For": "198.51.100.4"}
    )
    meme = client.post(
        "/validate-form", data=ADRESSE, headers={"X-Forwarded-For": "203.0.113.7"}
    )

    assert une.status_code == 200 and autre.status_code == 200
    assert meme.status_code == 429


def test_un_resultat_degrade_n_est_pas_un_verdict(monkeypatch):
    """La panne du fournisseur n'est pas imputée à l'adresse du visiteur."""
    _pipeline(monkeypatch, resultat={**RESULTAT, "validated": False, "degraded": True})

    reponse = client.post("/validate-form", data=ADRESSE)

    assert reponse.status_code == 200
    assert "pas répondu" in reponse.text
    assert "pas une preuve d'inexistence" in reponse.text
    assert "Adresse non reconnue" not in reponse.text


def test_le_pipeline_est_celui_de_la_route_payante():
    """Un essai et un appel payé ne peuvent pas rendre deux verdicts différents."""
    assert validate_form_route.perform_validation is validate_route.perform_validation
