"""Tests du tableau de bord de l'espace client (décision D64).

Ce qui est mesuré, et pourquoi :

  * la « prochaine étape » suit l'état réel du compte, dans l'ordre annoncé :
    essai terminé, quota atteint, plan payant, premier appel, essai en cours ;
  * la prévision « au rythme actuel » est une règle de trois sur le mois, et elle
    ne s'affiche que si l'épuisement tombe avant la fin de l'essai ou du mois ;
  * les courbes viennent des validations jour par jour, dont la somme vaut le
    compteur du mois ;
  * le conseil du jour change chaque jour et parcourt toute la liste ;
  * les six langues se rendent sans champ oublié, sans script, saisie échappée.
"""

import re
import tempfile
from datetime import datetime, timedelta, timezone
from html import unescape
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import accounts, i18n
from app.models import Customer, CustomerValidation
from app.routes import signup as s
from app.routes import tableau

MAINTENANT = datetime(2026, 10, 14, 12, tzinfo=timezone.utc)
FIN = datetime(2026, 10, 28, 12, tzinfo=timezone.utc)
INFOS = {"shop_name": "Boutique <b>", "email": "boutique@example.re", "prefixe": "sk_live_ab12", "cree_le": None}


def _statut(**changes):
    statut = {
        "plan": "trial", "quota": 100, "validations": 0, "invalides": 0, "validations_mois": 0, "restant": 100,
        "trial_ends_at": FIN, "trial_expire": False, "expire": False, "trial_jours_restants": 13, "quota_atteint": False,
    }
    statut.update(changes)
    return statut


def _page(lang="fr", statut=None, jours=None, maintenant=MAINTENANT):
    jours = jours if jours is not None else [(0, 0)] * maintenant.day
    reponse = tableau.page(lang, infos=INFOS, statut=statut or _statut(), maintenant=maintenant, jours=jours)
    return reponse, unescape(reponse.body.decode())


def _prochaine(texte: str) -> str:
    return re.search(r'<div class="next">(.*?)</div>\s*<nav class="chips"', texte, re.S).group(1)


@pytest.mark.parametrize("lang", i18n.LANGUES)
def test_chaque_langue_se_rend_proprement(lang):
    jours = [(5, 1)] * 14
    reponse, lu = _page(lang, _statut(validations=70, invalides=14, validations_mois=70, restant=30), jours)
    texte = reponse.body.decode()
    assert reponse.status_code == 200 and reponse.headers["cache-control"] == "no-store"
    for interdit in ("{{", "<!--@", "<script"):
        assert interdit not in texte
    assert not re.search(r"https?://", texte)
    assert "Boutique &lt;b&gt;" in texte and "Boutique <b>" not in texte
    assert texte.count("<polyline") == 5  # deux étincelles, la courbe bleue, la rouge et le pointillé
    assert f'action="{i18n.chemin(lang, "compte")}/cle"' in texte


@pytest.mark.parametrize("lang", i18n.LANGUES)
def test_un_compte_neuf_commence_par_le_premier_appel(lang):
    t = tableau.TEXTES[lang]
    _, lu = _page(lang)
    assert t["titre_vide"] in lu and t["premier"] in _prochaine(lu)
    assert f'href="{i18n.chemin(lang, "guide")}"' in _prochaine(lu)
    assert 'class="state ' not in lu


def test_le_rythme_qui_epuise_l_essai_avant_sa_fin_est_annonce():
    # 69 validations en 14 jours, 31 restantes : 31 / (69/14) = 6,3 jours, donc le 21.
    jours = [(5, 1)] * 13 + [(4, 1)]
    _, lu = _page(statut=_statut(validations=69, invalides=14, validations_mois=69, restant=31), jours=jours)
    attendu = s._date("fr", MAINTENANT + timedelta(days=7), "longue")
    prochaine = _prochaine(lu)
    assert tableau.TEXTES["fr"]["rythme_essai"].format(fin=s._date("fr", FIN, "longue"), restant="31", date=attendu) in prochaine
    assert "Passer à Starter, 29 € par mois" in prochaine
    assert "14 adresses fausses arrêtées ce mois-ci." in lu
    assert "Le quota de 100 serait atteint vers le " + attendu in lu


def test_un_rythme_lent_ne_fait_pas_peur():
    jours = [(1, 0)] * 14
    _, lu = _page(statut=_statut(validations=14, validations_mois=14, restant=86), jours=jours)
    prochaine = _prochaine(lu)
    assert tableau.TEXTES["fr"]["essai"].format(fin=s._date("fr", FIN, "longue"), restant="86", quota="100") in prochaine
    assert "Passer à Starter" not in prochaine and "épuisées" not in prochaine
    assert "14 adresses vérifiées ce mois-ci, toutes reconnues." in lu


def test_plan_payant_bandeau_et_prevision_avant_la_remise_a_zero():
    statut = _statut(plan="starter", quota=2000, trial_ends_at=None, trial_jours_restants=None,
                     validations=1900, validations_mois=1900, restant=100)
    _, lu = _page(statut=statut, jours=[(135, 0)] * 14)
    prochaine = _prochaine(lu)
    assert 'class="state s-paid"' in prochaine
    assert "avant sa remise à zéro le" in prochaine and "Demander un changement de plan" in prochaine
    assert "2 000 validations par mois" in lu


def test_essai_termine_puis_quota_atteint():
    expire = _prochaine(_page(statut=_statut(trial_expire=True, expire=True, quota_atteint=True, validations=100,
                                              validations_mois=100, restant=0), jours=[(7, 0)] * 14)[1])
    assert 'class="state s-expired"' in expire and 's-quota' not in expire
    assert "Demander un changement de plan" in expire
    quota = _prochaine(_page(statut=_statut(quota_atteint=True, validations=100, validations_mois=100, restant=0),
                             jours=[(7, 0)] * 14)[1])
    assert 'class="state s-quota"' in quota and f'"reason": "{accounts.REFUS_QUOTA_ATTEINT}"' in quota


def test_le_conseil_du_jour_change_chaque_jour():
    titres = []
    for decalage in range(len(tableau.TEXTES["fr"]["conseils"])):
        jour = MAINTENANT + timedelta(days=decalage)
        _, lu = _page(maintenant=jour, jours=[(0, 0)] * jour.day)
        titres.append(next(t for _c, t, _x in tableau.TEXTES["fr"]["conseils"] if t in lu))
    assert len(set(titres)) == len(tableau.TEXTES["fr"]["conseils"])


def test_la_courbe_suit_les_jours_et_le_quota():
    jours = [(2, 1), (0, 0), (3, 0)]
    svg = tableau.courbe(jours, 31, 100, 5 / 3)
    bleue = re.findall(r'<polyline points="([^"]*)"', svg)[0].split()
    assert len(bleue) == 3 and '<line x1="0" y1="20"' in svg
    # Cumul 2, 2, 5 sur une échelle de 100 : y = 140 - v × 1,2.
    assert [float(point.split(",")[1]) for point in bleue] == [137.6, 137.6, 134.0]
    assert '<line x1="0" y1="20"' not in tableau.courbe(jours, 31, None, 5 / 3)


def test_les_jours_du_mois_somment_au_compteur_du_mois():
    moteur = create_engine(f"sqlite:///{Path(tempfile.mkdtemp()) / 'x.sqlite'}")
    Customer.__table__.create(moteur)
    CustomerValidation.__table__.create(moteur)
    with sessionmaker(bind=moteur)() as session:
        client, _cle = accounts.create_customer(
            session, email="j@example.re", password="mot-de-passe-solide", shop_name="J", platform="shopify",
            plan="trial", maintenant=MAINTENANT, jours_essai=14,
        )
        debut = accounts._naif_utc(accounts.month_start(MAINTENANT))
        for jour, valide in ((0, True), (0, False), (2, True), (13, False)):
            session.add(CustomerValidation(customer_id=client.id, cache_key="k", validated=valide, created_at=debut + timedelta(days=jour, hours=3)))
        session.add(CustomerValidation(customer_id=client.id, cache_key="k", validated=True, created_at=debut - timedelta(hours=1)))
        session.commit()
        jours = accounts.daily_usage(session, client, MAINTENANT)
        assert len(jours) == 14 and jours[0] == (2, 1) and jours[2] == (1, 0) and jours[13] == (1, 1)
        assert sum(n for n, _ in jours) == accounts.plan_status(session, client, MAINTENANT)["validations_mois"] == 4
