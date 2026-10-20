"""Tests du paiement Stripe (D91) : signature, activation, résiliation, liens.

Ce qui est mesuré, et pourquoi :

  * rien n'est cru sans signature : mauvais secret, signature absente ou
    horodatage trop vieux (rejeu) sont refusés ; sans secret configuré, la route
    répond `503` et ne touche à rien ;
  * un paiement signé, payé, en euros, du montant d'une offre, passe le compte
    désigné par `client_reference_id` à cette offre ; un montant inconnu ou un
    compte absent ne change rien ;
  * la fin d'un abonnement ramène le compte à l'essai, échu : sa clé n'est plus servie ;
  * l'espace client ouvre les liens Stripe avec le numéro et l'e-mail du compte ;
  * D93 : Starter et Pro, sur Tarifs, mènent à l'inscription avec leur offre ; la
    page de la clé ouvre alors le paiement Stripe de cette offre, rattaché au
    compte, qui reste un essai tant que Stripe n'a pas confirmé (D54).
"""

import hashlib
import hmac
import html
import json
import re
import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import accounts, i18n, ratelimit
from app.config import get_settings
from app.main import app
from app.models import Customer, CustomerValidation
from app.routes import signup, stripe_webhook, tableau

SECRET = "whsec_test_" + "0" * 24
client_http = TestClient(app)


def _signer(corps: bytes, secret: str = SECRET, horodatage: int | None = None) -> str:
    t = int(time.time()) if horodatage is None else horodatage
    return f"t={t},v1=" + hmac.new(secret.encode(), f"{t}.".encode() + corps, hashlib.sha256).hexdigest()


@pytest.fixture
def base(tmp_path, monkeypatch):
    """SQLite jetable, un compte d'essai, le secret du webhook configuré."""
    moteur = create_engine(f"sqlite:///{tmp_path / 'stripe.sqlite'}")
    Customer.__table__.create(moteur)
    CustomerValidation.__table__.create(moteur)
    fabrique = sessionmaker(bind=moteur)
    with fabrique() as s:
        compte, _cle = accounts.create_customer(s, email="shop@example.re", password="un-mot-de-passe-solide-12",
                                                shop_name="Shop", platform="shopify")
        identifiant = compte.id
    monkeypatch.setattr(stripe_webhook, "_session", fabrique)
    reglages = get_settings().model_copy(update={"stripe_webhook_secret": SECRET})
    monkeypatch.setattr(stripe_webhook, "get_settings", lambda: reglages)
    return fabrique, identifiant


def _envoyer(evenement: dict, **signature) -> int:
    corps = json.dumps(evenement).encode()
    return client_http.post("/stripe/webhook", content=corps, headers={"Stripe-Signature": _signer(corps, **signature)}).status_code


def _paiement(reference, montant=2900, devise="eur", statut="paid", client="cus_123") -> dict:
    return {"type": "checkout.session.completed", "data": {"object": {
        "client_reference_id": reference, "amount_total": montant, "currency": devise, "payment_status": statut, "customer": client}}}


def _plan(fabrique, identifiant) -> str:
    with fabrique() as s:
        return s.get(Customer, identifiant).plan


def test_la_signature_stripe_est_verifiee():
    corps = b'{"type":"x"}'
    maintenant = time.time()
    assert stripe_webhook.signature_valide(corps, _signer(corps), SECRET, maintenant)
    assert not stripe_webhook.signature_valide(corps, _signer(corps, secret="whsec_autre"), SECRET, maintenant)
    assert not stripe_webhook.signature_valide(corps + b" ", _signer(corps), SECRET, maintenant)
    assert not stripe_webhook.signature_valide(corps, _signer(corps, horodatage=int(maintenant) - 301), SECRET, maintenant)
    assert not stripe_webhook.signature_valide(corps, "", SECRET, maintenant)
    assert not stripe_webhook.signature_valide(corps, _signer(corps), "", maintenant)


def test_sans_secret_la_route_ne_fait_rien(monkeypatch):
    reglages = get_settings().model_copy(update={"stripe_webhook_secret": ""})
    monkeypatch.setattr(stripe_webhook, "get_settings", lambda: reglages)
    assert _envoyer(_paiement("1")) == 503


def test_un_paiement_signe_active_l_offre_payee(base):
    fabrique, identifiant = base
    assert _envoyer(_paiement(str(identifiant), montant=2900)) == 200
    assert _plan(fabrique, identifiant) == "starter"
    assert _envoyer(_paiement(str(identifiant), montant=7900)) == 200
    with fabrique() as s:
        compte = s.get(Customer, identifiant)
        assert compte.plan == "pro" and compte.stripe_customer == "cus_123"


@pytest.mark.parametrize("evenement", [
    _paiement("1", montant=1234),
    _paiement("1", devise="usd"),
    _paiement("1", statut="unpaid"),
    _paiement("999"),
    _paiement("pas-un-numero"),
    {"type": "invoice.paid", "data": {"object": {}}},
])
def test_un_paiement_inconnu_ne_change_rien(base, evenement):
    fabrique, identifiant = base
    assert _envoyer(evenement) == 200
    assert _plan(fabrique, identifiant) == "trial"


def test_une_signature_fausse_est_refusee_sans_rien_changer(base):
    fabrique, identifiant = base
    assert _envoyer(_paiement(str(identifiant)), secret="whsec_autre") == 400
    assert _plan(fabrique, identifiant) == "trial"


def test_la_fin_de_l_abonnement_coupe_la_cle(base):
    fabrique, identifiant = base
    _envoyer(_paiement(str(identifiant), montant=2900, client="cus_fin"))
    assert _envoyer({"type": "customer.subscription.deleted", "data": {"object": {"customer": "cus_fin"}}}) == 200
    with fabrique() as s:
        compte = s.get(Customer, identifiant)
        statut = accounts.plan_status(s, compte, datetime.now(timezone.utc) + timedelta(seconds=1))
    assert compte.plan == "trial" and statut["expire"]


def test_l_espace_client_ouvre_stripe_avec_le_compte():
    reglages = get_settings()
    lien = tableau.lien_paiement(reglages.stripe_lien_starter, {"id": 42, "email": "a+b@shop.re"})
    assert lien == f"{reglages.stripe_lien_starter}?client_reference_id=42&prefilled_email=a%2Bb%40shop.re"
    assert tableau.lien_paiement(reglages.stripe_lien_pro, {}) == reglages.stripe_lien_pro
    assert accounts.PLAN_PAR_MONTANT == {2900: "starter", 7900: "pro"}


@pytest.fixture
def inscriptions(tmp_path, monkeypatch):
    moteur = create_engine(f"sqlite:///{tmp_path / 'inscriptions.sqlite'}")
    Customer.__table__.create(moteur)
    CustomerValidation.__table__.create(moteur)
    fabrique = sessionmaker(bind=moteur)
    monkeypatch.setattr(signup, "SessionLocal", fabrique)
    ratelimit.reset()
    yield fabrique
    ratelimit.reset()


@pytest.mark.parametrize("lang", i18n.LANGUES)
def test_l_offre_choisie_mene_au_paiement_du_compte(inscriptions, lang):
    chemin = i18n.chemin(lang, "signup")
    formulaire = client_http.get(f"{chemin}?offre=pro").text
    assert '<input type="hidden" name="offre" value="pro">' in formulaire
    rappel = re.search(r'class="form-offre">(.*?)</p>', formulaire, re.S).group(1)
    assert "€" in rappel and i18n.nombre(lang, i18n.EUROS["pro"]) in rappel  # le montant prélevé, en euros
    assert 'name="offre"' not in client_http.get(f"{chemin}?offre=enterprise").text

    forme = {"email": f"{lang}@shop.re", "password": "un-mot-de-passe-solide-12", "shop_name": "S", "platform": "shopify"}
    refus = client_http.post(chemin, data={**forme, "email": "pas-un-email", "offre": "starter"})
    assert refus.status_code == 400 and 'name="offre" value="starter"' in refus.text

    page = client_http.post(chemin, data={**forme, "offre": "pro"})
    with inscriptions() as s:
        compte = accounts.find_by_email(s, forme["email"])
        assert compte.plan == "trial"  # D54 : seul Stripe fait passer à Pro
    lien = tableau.lien_paiement(get_settings().stripe_lien_pro, {"id": compte.id, "email": compte.email})
    assert page.status_code == 200 and page.headers["cache-control"] == "no-store"
    bouton = re.search(rf'href="{re.escape(html.escape(lien))}" target="_blank" rel="noopener noreferrer">(.*?)<svg', page.text, re.S)
    assert bouton and "€" in bouton.group(1) and i18n.nombre(lang, i18n.EUROS["pro"]) in bouton.group(1)


def test_sans_offre_la_page_de_la_cle_ne_parle_pas_de_paiement(inscriptions):
    forme = {"email": "essai@shop.re", "password": "un-mot-de-passe-solide-12", "shop_name": "S", "platform": "shopify"}
    page = client_http.post("/signup", data={**forme, "offre": "enterprise"}).text
    assert "https://" not in page and 'class="pay"' not in page
