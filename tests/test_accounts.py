"""Tests des comptes clients : hachage, clés API, quotas, estimation (D44).

Ces tests mesurent ce qui protège l'argent et la vie privée d'un client :

  * un mot de passe n'est **jamais** stocké ni lisible (`$2b$12$…`) ;
  * une clé API n'est stockée que sous forme d'empreinte, et n'est affichée
    qu'une fois ;
  * les quotas affichés sur `/pricing` sont ceux qui s'appliquent ;
  * l'estimation d'économies suit une formule vérifiable.

La base est un SQLite jetable : les tables des comptes n'ont aucun type
propriétaire PostgreSQL, elles peuvent donc être créées telles quelles. Les
tables d'adresses (JSONB) ne sont pas créées ici — elles ne servent pas.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import accounts
from app.models import Customer, CustomerValidation

MOT_DE_PASSE = "un-mot-de-passe-solide-12"


@pytest.fixture
def session(tmp_path):
    """Session SQLite jetable avec les deux tables de D44."""
    moteur = create_engine(f"sqlite:///{tmp_path / 'comptes.sqlite'}")
    Customer.__table__.create(moteur)
    CustomerValidation.__table__.create(moteur)
    return sessionmaker(bind=moteur)()


def test_mot_de_passe_hache_bcrypt_et_jamais_lisible():
    empreinte = accounts.hash_password(MOT_DE_PASSE)
    assert empreinte.startswith("$2b$12$")
    assert MOT_DE_PASSE not in empreinte
    assert accounts.verify_password(MOT_DE_PASSE, empreinte)
    assert not accounts.verify_password(MOT_DE_PASSE + "x", empreinte)
    # Deux comptes ayant le meme mot de passe n'ont pas la meme empreinte (sel).
    assert accounts.hash_password(MOT_DE_PASSE) != empreinte
    # Une empreinte illisible est un refus, pas une exception.
    assert not accounts.verify_password(MOT_DE_PASSE, "pas-une-empreinte")


def test_regles_de_mot_de_passe():
    assert accounts.password_problem("court") is not None
    assert "12" in accounts.password_problem("court")
    assert accounts.password_problem("x" * 73) is not None
    assert accounts.password_problem(MOT_DE_PASSE) is None


def test_cle_api_format_et_empreinte():
    cle, empreinte, prefixe = accounts.new_api_key()
    assert cle.startswith("sk_live_")
    assert len(cle) == len("sk_live_") + 32
    assert cle[8:].isalnum()
    assert empreinte == accounts.api_key_hash(cle)
    assert empreinte != cle and len(empreinte) == 64
    assert prefixe == cle[:12]
    assert cle not in prefixe
    autre, empreinte_autre, _ = accounts.new_api_key()
    assert autre != cle and empreinte_autre != empreinte


def test_quotas_publies_sont_ceux_appliques():
    """Les quotas de `/pricing` sont la source : 100 / 2 000 / 10 000 / illimité."""
    assert accounts.quota_for("trial") == 100
    assert accounts.quota_for("starter") == 2000
    assert accounts.quota_for("pro") == 10000
    assert accounts.quota_for("enterprise") is None
    assert accounts.quota_for("inconnu") == 100


def test_estimation_economies_avec_sa_formule():
    assert accounts.savings_usd(0) == 0.0
    assert accounts.savings_usd(2) == 2 * accounts.COUT_ECHEC_USD
    assert accounts.savings_usd(-5) == 0.0
    # Borne basse de la fourchette publiee sur /pricing (17 a 30 $).
    assert accounts.COUT_ECHEC_USD == 17.0


def test_essai_de_quatorze_jours():
    depart = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)
    assert accounts.trial_end(depart, 14) == depart + timedelta(days=14)


def test_email_valide_ou_non():
    assert accounts.valid_email(" boutique@example.re ")
    assert accounts.normalize_email(" Boutique@Example.RE ") == "boutique@example.re"
    for mauvais in ("sans-arobase", "a@b", "@example.re", "a b@example.re"):
        assert not accounts.valid_email(mauvais), mauvais


def test_creation_et_authentification(session):
    client, cle = accounts.create_customer(
        session,
        email="Tiitoune@Example.RE",
        password=MOT_DE_PASSE,
        shop_name="  Tiitouneart  ",
        platform="shopify",
    )
    assert client.email == "tiitoune@example.re"  # normalise
    assert client.shop_name == "Tiitouneart"  # espaces retires
    assert client.plan == "trial"
    assert client.trial_ends_at is not None
    assert accounts.find_by_api_key(session, cle) is not None
    assert accounts.find_by_api_key(session, "sk_live_inconnue") is None
    assert accounts.authenticate(session, "tiitoune@example.re", MOT_DE_PASSE) is not None
    assert accounts.authenticate(session, "tiitoune@example.re", "mauvais") is None
    assert accounts.authenticate(session, "inconnu@example.re", MOT_DE_PASSE) is None


def test_regeneration_change_l_empreinte(session):
    client, ancienne = accounts.create_customer(
        session,
        email="boutique@example.re",
        password=MOT_DE_PASSE,
        shop_name="Boutique",
        platform="woocommerce",
    )
    empreinte_avant = client.api_key_hash
    nouvelle = accounts.regenerate_api_key(session, client)
    assert nouvelle != ancienne
    assert client.api_key_hash != empreinte_avant
    assert accounts.find_by_api_key(session, ancienne) is None
    assert accounts.find_by_api_key(session, nouvelle) is not None


def test_consommation_et_etat_du_plan(session):
    client, _ = accounts.create_customer(
        session,
        email="compte@example.re",
        password=MOT_DE_PASSE,
        shop_name="Boutique",
        platform="autre",
    )
    assert accounts.consumption(session, client)["validations"] == 0
    for issue in (True, False, False):
        accounts.record_validation(session, client.id, "cle-de-cache", issue)

    statut = accounts.plan_status(session, client)
    assert statut["validations"] == 3
    assert statut["invalides"] == 2
    assert statut["economies_usd"] == 34.0
    assert statut["quota"] == 100 and statut["restant"] == 97
    assert statut["quota_atteint"] is False
    assert statut["trial_expire"] is False
    assert statut["trial_jours_restants"] is not None
    assert statut["cout_par_echec_usd"] == 17.0


def test_quota_atteint_et_essai_expire_sont_mesures(session):
    client, _ = accounts.create_customer(
        session,
        email="essai@example.re",
        password=MOT_DE_PASSE,
        shop_name="Boutique",
        platform="autre",
    )
    client.plan = "starter"
    client.trial_ends_at = datetime.now(timezone.utc) - timedelta(days=1)
    session.commit()
    statut = accounts.plan_status(session, client)
    assert statut["quota"] == 2000
    assert statut["trial_expire"] is True
    assert statut["trial_jours_restants"] == 0

    # Un plan illimite n'affiche ni reste ni plafond atteint.
    client.plan = "enterprise"
    session.commit()
    statut = accounts.plan_status(session, client)
    assert statut["quota"] is None and statut["restant"] is None
    assert statut["quota_atteint"] is False


# --------------------------------------------------------------------------- #
# D45 : ce qui decide de servir une cle API
# --------------------------------------------------------------------------- #


def _compte(session, *, plan="trial", email="cle@example.re", fin_essai=None):
    """Compte pret a interroger, avec une fin d'essai eventuellement forcee."""
    client, cle = accounts.create_customer(
        session,
        email=email,
        password=MOT_DE_PASSE,
        shop_name="Boutique",
        platform="autre",
        plan=plan,
    )
    if fin_essai is not None:
        client.trial_ends_at = fin_essai
        session.commit()
    return client, cle


def test_cle_api_autorisee_ou_refusee(session):
    client, cle = _compte(session)
    assert accounts.authorize_api_key(session, cle) == (client, None)

    # Une cle inconnue ne renvoie **aucun** compte : impossible de servir par
    # inadvertance apres un refus.
    inconnu, motif = accounts.authorize_api_key(session, "sk_live_" + "z" * 32)
    assert inconnu is None and motif == accounts.REFUS_CLE_INCONNUE
    # Une cle vide, blanche ou absente est le meme refus, pas une exception.
    for vide in ("", "   ", None):
        assert accounts.authorize_api_key(session, vide)[1] == accounts.REFUS_CLE_INCONNUE


def test_essai_termine_ferme_le_service(session):
    passe = datetime.now(timezone.utc) - timedelta(days=1)
    client, cle = _compte(session, email="expire@example.re", fin_essai=passe)
    assert accounts.refusal_reason(session, client) == accounts.REFUS_COMPTE_EXPIRE
    assert accounts.authorize_api_key(session, cle)[1] == accounts.REFUS_COMPTE_EXPIRE


def test_un_plan_payant_n_est_pas_bloque_par_la_date_d_essai(session):
    """La date d'essai ne ferme la porte qu'a l'essai : qui paie est servi."""
    passe = datetime.now(timezone.utc) - timedelta(days=1)
    client, _ = _compte(
        session, plan="starter", email="payant@example.re", fin_essai=passe
    )
    statut = accounts.plan_status(session, client)
    assert statut["trial_expire"] is True  # le fait est vrai...
    assert statut["expire"] is False  # ...mais la consequence ne s'applique plus
    assert accounts.refusal_reason(session, client) is None


def test_quota_atteint_puis_essai_termine_dans_cet_ordre(session):
    client, _ = _compte(session, email="quota@example.re")
    for _ in range(accounts.quota_for("trial")):
        accounts.record_validation(session, client.id, "cle", True)

    statut = accounts.plan_status(session, client)
    assert statut["quota_atteint"] is True and statut["restant"] == 0
    assert accounts.refusal_reason(session, client) == accounts.REFUS_QUOTA_ATTEINT

    # Essai termine **et** quota atteint : c'est l'essai qui parle, parce que
    # c'est la raison actionnable (« passez a un plan »).
    client.trial_ends_at = datetime.now(timezone.utc) - timedelta(days=1)
    session.commit()
    assert accounts.refusal_reason(session, client) == accounts.REFUS_COMPTE_EXPIRE


def test_le_quota_compte_le_mois_et_pas_la_vie_du_compte(session):
    """Le quota publie est mensuel : ce qui est hors du mois ne le consomme pas."""
    client, _ = _compte(session, email="mois@example.re")
    # Horodatage naif en UTC : c'est la convention des colonnes `TIMESTAMP`
    # (voir `_naif_utc`), donc celle que la base ecrit reellement.
    session.add(
        CustomerValidation(
            customer_id=client.id,
            cache_key="cle",
            validated=True,
            created_at=accounts.month_start().replace(tzinfo=None) - timedelta(days=1),
        )
    )
    session.add(
        CustomerValidation(customer_id=client.id, cache_key="cle", validated=True)
    )
    session.commit()

    statut = accounts.plan_status(session, client)
    assert statut["validations"] == 2  # le total reste visible au client
    assert statut["validations_mois"] == 1  # le mois est ce qui decide
    assert statut["restant"] == accounts.quota_for("trial") - 1
    assert statut["quota_atteint"] is False
    assert accounts.refusal_reason(session, client) is None


def test_debut_du_mois_en_utc():
    assert accounts.month_start(datetime(2026, 10, 12, 23, 59, tzinfo=timezone.utc)) == (
        datetime(2026, 10, 1, tzinfo=timezone.utc)
    )
    assert accounts.month_start(datetime(2026, 2, 28, tzinfo=timezone.utc)) == (
        datetime(2026, 2, 1, tzinfo=timezone.utc)
    )
