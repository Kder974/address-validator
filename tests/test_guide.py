"""Tests du guide d'intégration public `GET /guide` (décision D48).

Ce que ces tests mesurent, et pourquoi :

  * la page est **publique**, indexable et cachable cinq minutes : elle doit être
    lisible par un inconnu qui vient de recevoir une clé ;
  * elle ne charge **rien** (aucun script, aucune ressource externe) et ne
    contient **aucun secret** : c'est une page indexable, donc tout ce qui s'y
    trouve est public par construction ;
  * les faits qu'elle publie sont ceux du **code** : base publique de la
    configuration, prix réel, plafond réel de l'essai gratuit, en-têtes réellement
    posés par la route, et les huit codes de retour ;
  * elle est **liée depuis la navigation** : un guide introuvable n'est pas un
    guide.
"""

from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from app.routes.guide import CODES

client = TestClient(app)


def test_le_guide_est_public_indexable_et_cachable():
    page = client.get("/guide")

    assert page.status_code == 200
    assert page.headers["cache-control"] == "public, max-age=300"
    assert "form-action 'none'" in page.headers["content-security-policy"]
    assert 'name="robots" content="index, follow"' in page.text
    # Les sections attendues par un intégrateur.
    for attendu in (
        "Guide d'intégration",
        "X-API-Key",
        "/validate-form",
        "Shopify",
        "WooCommerce",
        "Import CSV",
        "cache_key",
        "Retry-After",
    ):
        assert attendu in page.text, f"« {attendu} » a disparu du guide"


def test_le_guide_ne_charge_rien_et_ne_publie_aucun_secret():
    page = client.get("/guide")
    reglages = get_settings()

    # Autonome : rien à charger, donc rien qui puisse suivre le visiteur.
    for interdit in ("<script", "<link", "<img", "src=", "@import", "url("):
        assert interdit not in page.text, f"le guide charge quelque chose : {interdit}"
    # Page indexable : aucun secret ne peut y figurer.
    if reglages.api_secret_key:
        assert reglages.api_secret_key not in page.text
    if reglages.dashboard_password:
        assert reglages.dashboard_password not in page.text
    # La clé d'exemple est un gabarit reconnaissable, pas une clé.
    assert "sk_live_VOTRE_CLE" in page.text


def test_la_base_publique_vient_de_la_configuration():
    reglages = get_settings()
    base = reglages.public_base_url.rstrip("/")

    page = client.get("/guide")

    assert base in page.text
    # Aucun gabarit non remplacé ne doit rester à l'écran.
    assert "BASE_URL" not in page.text


def test_les_faits_publies_sont_ceux_du_code():
    reglages = get_settings()

    page = client.get("/guide")

    assert f"{reglages.x402_price:.2f}" in page.text, "le prix publié n'est plus celui du code"
    assert (
        str(reglages.validate_form_rate_limit_per_day) in page.text
    ), "le plafond de l'essai gratuit n'est plus celui du code"
    for en_tete in ("X-Payment-Mode", "X-Plan", "X-Quota-Remaining"):
        assert en_tete in page.text, f"l'en-tête {en_tete} a disparu du guide"
    for code, _situation, _quoi in CODES:
        assert f">{code}<" in page.text, f"le code {code} a disparu du tableau"


def test_le_guide_est_lie_depuis_la_navigation():
    page = client.get("/pricing")

    assert 'href="/guide"' in page.text, "le guide n'est plus atteignable depuis le site"
