"""Tests du suivi client (D50) : l'état affiché est l'état servi.

Ces tests mesurent ce qui décide d'un appel commercial :

  * un état n'est jamais contredit par la route payante — « essai terminé » et
    « quota atteint » sont *exactement* les situations où la clé reçoit `403` /
    `402`, et l'inverse ;
  * un **abonné** dont la date d'essai est passée n'est pas « essai terminé » : le
    rapport ne doit pas faire appeler quelqu'un qui est en règle (règle D44/D45) ;
  * l'ordre publié est l'ordre d'urgence déclaré, l'échéance la plus proche
    d'abord, le nom ensuite ;
  * le rapport porte un préfixe de clé et jamais une clé complète ;
  * le suivi lit sans écrire, et refuse un seuil absurde au lieu de rendre un
    rapport vide (un rapport vide a l'air d'un « tout va bien »).

La base est un SQLite jetable (mêmes tables que `tests/test_accounts.py`) et
l'instant de référence est fixe : aucun test ne depend de l'horloge du poste.
"""

from datetime import datetime, timedelta, timezone
from functools import lru_cache
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import accounts, suivi
from app.models import Customer, CustomerValidation

ROOT = Path(__file__).resolve().parents[1]
MAINTENANT = datetime(2026, 10, 13, 9, 0, tzinfo=timezone.utc)


@pytest.fixture
def session(tmp_path):
    """Session SQLite jetable avec les deux tables des comptes."""
    moteur = create_engine(f"sqlite:///{tmp_path / 'suivi.sqlite'}")
    Customer.__table__.create(moteur)
    CustomerValidation.__table__.create(moteur)
    yield sessionmaker(bind=moteur)()


@lru_cache(maxsize=1)
def _empreinte() -> str:
    """Une seule empreinte bcrypt pour tous les comptes d'essai.

    bcrypt cout 12 prend ~0,25 s par hachage : le suivi ne verifie aucun mot de
    passe, donc payer ce cout vingt fois ne mesurerait rien de plus.
    """
    return accounts.hash_password("un-mot-de-passe-solide-12")


def _compte(
    session,
    email: str,
    *,
    nom: str | None = None,
    plan: str = "trial",
    cree_il_y_a: int = 0,
    essai_dans: int = accounts.JOURS_ESSAI,
) -> tuple[Customer, str]:
    """Compte reel en base, et sa cle en clair (la seule fois ou elle existe)."""
    cle, empreinte, prefixe = accounts.new_api_key()
    client = Customer(
        email=email,
        password_hash=_empreinte(),
        shop_name=nom or email.split("@")[0].capitalize(),
        platform="shopify",
        api_key_hash=empreinte,
        api_key_prefix=prefixe,
        plan=plan,
        trial_ends_at=MAINTENANT + timedelta(days=essai_dans),
        created_at=(MAINTENANT - timedelta(days=cree_il_y_a)).replace(tzinfo=None),
    )
    session.add(client)
    session.commit()
    return client, cle


def _usage(
    session,
    client: Customer,
    *,
    il_y_a_jours: int = 1,
    combien: int = 1,
    valides: bool = True,
) -> None:
    """Inscrit `combien` validations servies au compte, a une date choisie."""
    quand = (MAINTENANT - timedelta(days=il_y_a_jours)).replace(tzinfo=None)
    for _ in range(combien):
        session.add(
            CustomerValidation(
                customer_id=client.id,
                cache_key=None,
                validated=valides,
                created_at=quand,
            )
        )
    session.commit()


def test_etat_refuse_est_exactement_l_etat_servi(session):
    """« Essai terminé » et « quota atteint » ne s'affichent que si c'est vrai."""
    expire, _ = _compte(session, "expire@example.re", essai_dans=-2)
    epuise, _ = _compte(session, "epuise@example.re", essai_dans=10)
    _usage(session, epuise, combien=accounts.VALIDATIONS_ESSAI)

    for client in (expire, epuise):
        refus = accounts.refusal_reason(session, client, MAINTENANT)
        ligne = suivi.etat_client(session, client, maintenant=MAINTENANT)
        attendu = {
            accounts.REFUS_COMPTE_EXPIRE: "essai_fini",
            accounts.REFUS_QUOTA_ATTEINT: "quota_atteint",
        }[refus]
        assert ligne["etat"] == attendu
        assert ligne["refus"] == refus
        assert ligne["refus_http"] == (403 if refus == accounts.REFUS_COMPTE_EXPIRE else 402)

    # Et l'inverse : un compte servi n'entre dans aucun etat de refus.
    sain, _ = _compte(session, "sain@example.re", essai_dans=10)
    _usage(session, sain, combien=3)
    ligne = suivi.etat_client(session, sain, maintenant=MAINTENANT)
    assert ligne["refus"] is None
    assert ligne["refus_http"] is None
    assert ligne["etat"] == suivi.ETAT_OK
    assert "refus" not in ligne["detail"]


def test_abonne_dont_l_essai_est_fini_n_est_pas_un_essai_fini(session):
    """D44/D45 : la date ne ferme que l'essai. Le rapport ne doit pas mentir."""
    abonne, _ = _compte(
        session, "abonne@example.re", plan="starter", essai_dans=-10, cree_il_y_a=40
    )
    _usage(session, abonne, il_y_a_jours=2)
    assert accounts.refusal_reason(session, abonne, MAINTENANT) is None
    ligne = suivi.etat_client(session, abonne, maintenant=MAINTENANT)
    assert ligne["etat"] == suivi.ETAT_OK
    assert ligne["essai_fin"] is not None  # le fait reste vrai, la consequence non
    assert ligne["refus"] is None
    # La date d'essai passee d'un abonne n'explique rien : le detail ne la cite pas.
    assert "essai jusqu'au" not in ligne["detail"]
    # Et les grands quotas se lisent (« 2 000 », pas « 2000 »).
    assert "1/2 000 validations" in ligne["detail"]


def test_chaque_seuil_classe_une_situation_reelle(session):
    """Les seuils publies sont mesures, et restent des reglages."""
    jamais, _ = _compte(session, "jamais@example.re", cree_il_y_a=5)
    recent, _ = _compte(session, "recent@example.re", cree_il_y_a=1)
    dort, _ = _compte(
        session, "dort@example.re", plan="starter", essai_dans=-40, cree_il_y_a=90
    )
    _usage(session, dort, il_y_a_jours=30, combien=5)
    actif, _ = _compte(
        session, "actif@example.re", plan="starter", essai_dans=-40, cree_il_y_a=90
    )
    _usage(session, actif, il_y_a_jours=2)

    etats = {
        client.email: suivi.etat_client(session, client, maintenant=MAINTENANT)["etat"]
        for client in (jamais, recent, dort, actif)
    }
    assert etats["jamais@example.re"] == "jamais_utilise"
    # Un compte cree hier n'a rien a se reprocher : trop tot pour conclure.
    assert etats["recent@example.re"] == suivi.ETAT_OK
    assert etats["dort@example.re"] == "dormant"
    assert etats["actif@example.re"] == suivi.ETAT_OK

    # Les seuils sont des reglages : les deplacer deplace le classement.
    assert (
        suivi.etat_client(
            session, dort, maintenant=MAINTENANT, seuil_dormant_jours=45
        )["etat"]
        == suivi.ETAT_OK
    )
    assert (
        suivi.etat_client(
            session, recent, maintenant=MAINTENANT, seuil_jamais_utilise_jours=1
        )["etat"]
        == "jamais_utilise"
    )


def test_quota_presque_declenche_avant_la_panne(session):
    """Prevenir au seuil publie, pas apres le refus."""
    presque, _ = _compte(session, "presque@example.re", essai_dans=10)
    seuil = accounts.VALIDATIONS_ESSAI * suivi.SEUIL_QUOTA_PCT // 100
    _usage(session, presque, combien=seuil)
    ligne = suivi.etat_client(session, presque, maintenant=MAINTENANT)
    assert ligne["etat"] == "quota_presque"
    assert ligne["pct_quota"] == suivi.SEUIL_QUOTA_PCT
    assert ligne["restant"] == accounts.VALIDATIONS_ESSAI - seuil

    sous, _ = _compte(session, "sous@example.re", essai_dans=10)
    _usage(session, sous, combien=seuil - 1)
    assert suivi.etat_client(session, sous, maintenant=MAINTENANT)["etat"] == suivi.ETAT_OK


def test_ordre_publie_est_l_ordre_d_urgence(session):
    """Les etats sortent dans l'ordre declare, l'echeance la plus proche d'abord."""
    urgent, _ = _compte(session, "zeta@example.re", nom="Zeta", essai_dans=-5)
    urgent2, _ = _compte(session, "alpha@example.re", nom="Alpha", essai_dans=-1)
    epuise, _ = _compte(session, "quota@example.re", nom="Quota", essai_dans=10)
    _usage(session, epuise, combien=accounts.VALIDATIONS_ESSAI)
    _compte(session, "bientot@example.re", nom="Bientot", essai_dans=2)
    pres, _ = _compte(
        session, "pres@example.re", nom="Pres", plan="starter", essai_dans=-40
    )
    # 20 validations sur 2 000 = 1 % : le seuil de recette est de 1 %.
    _usage(session, pres, combien=20)
    _compte(session, "jamais@example.re", nom="Jamais", cree_il_y_a=6)
    dort, _ = _compte(
        session, "dort@example.re", nom="Dort", plan="starter", essai_dans=-40, cree_il_y_a=90
    )
    _usage(session, dort, il_y_a_jours=40)
    sain, _ = _compte(
        session, "sain@example.re", nom="Sain", plan="starter", essai_dans=-40, cree_il_y_a=90
    )
    _usage(session, sain, il_y_a_jours=2)

    lignes = suivi.suivi(session, maintenant=MAINTENANT, seuil_quota_pct=1)
    assert [ligne["etat"] for ligne in lignes] == [
        "essai_fini",
        "essai_fini",
        "quota_atteint",
        "essai_bientot",
        "quota_presque",
        "jamais_utilise",
        "dormant",
        "ok",
    ]
    # A etat egal, l'echeance la plus proche passe devant — et avant le nom.
    assert [ligne["shop_name"] for ligne in lignes[:2]] == ["Zeta", "Alpha"]
    priorites = [ligne["priorite"] for ligne in lignes]
    assert priorites == sorted(priorites)
    assert suivi.resume(lignes)["a_joindre"] == len(lignes) - 1


def test_rapport_ne_contient_jamais_une_cle_complete(session):
    """Un suivi n'a besoin que du prefixe : la cle ne peut pas fuir d'ici."""
    client, cle = _compte(session, "fuite@example.re", essai_dans=-3, nom="Fuite")
    lignes = suivi.suivi(session, maintenant=MAINTENANT)
    texte = suivi.rapport_texte(lignes, MAINTENANT)
    tableau = suivi.rapport_csv(lignes)

    assert client.api_key_prefix in texte
    assert client.api_key_prefix in tableau
    for sortie in (texte, tableau):
        assert cle not in sortie
        assert cle[8:] not in sortie  # meme le secret sans son prefixe
        assert client.api_key_hash not in sortie

    lignes_csv = tableau.strip().split("\n")
    assert lignes_csv[0].split(";") == list(suivi.COLONNES_CSV)
    assert len(lignes_csv) == len(lignes) + 1
    assert "cle_prefixe" in suivi.COLONNES_CSV


def test_suivi_est_en_lecture_seule(session):
    """Un outil de decision qui ecrit changerait l'objet qu'il mesure."""
    client, _ = _compte(session, "lecture@example.re", essai_dans=10)
    _usage(session, client, combien=3)
    avant = (session.query(Customer).count(), session.query(CustomerValidation).count())

    lignes = suivi.suivi(session, maintenant=MAINTENANT)
    suivi.rapport_texte(lignes, MAINTENANT)
    suivi.rapport_csv(lignes)
    session.commit()

    assert not session.new and not session.dirty and not session.deleted
    apres = (session.query(Customer).count(), session.query(CustomerValidation).count())
    assert apres == avant
    assert len(lignes) == 1


def test_etats_publient_une_priorite_unique_et_ordonnee():
    """L'ordre publie est un ordre : deux etats ne peuvent pas partager un rang."""
    priorites = [meta["priorite"] for meta in suivi.ETATS.values()]
    assert priorites == sorted(priorites) == list(range(1, len(priorites) + 1))
    # L'inaction est derniere : ce qui n'appelle personne ne passe devant rien.
    assert suivi.ETATS[suivi.ETAT_OK]["priorite"] == max(priorites)
    assert suivi.plan_au_dessus("trial") == "starter"
    assert suivi.plan_au_dessus("pro") == "enterprise"
    assert suivi.plan_au_dessus("enterprise") is None
    assert suivi.plan_au_dessus("plan-inconnu") == "starter"  # repli comme quota_for


@pytest.mark.parametrize(
    "seuil, valeur",
    [
        ("seuil_quota_pct", 0),
        ("seuil_quota_pct", 101),
        ("seuil_essai_jours", -1),
        ("seuil_dormant_jours", -1),
        ("seuil_jamais_utilise_jours", -3),
    ],
)
def test_seuil_absurde_refuse_au_lieu_de_rendre_un_rapport_vide(session, seuil, valeur):
    """Un rapport vide a l'air d'un « tout va bien » : un seuil impossible leve."""
    with pytest.raises(ValueError, match=seuil):
        suivi.suivi(session, maintenant=MAINTENANT, **{seuil: valeur})


def test_base_vide_dit_qu_elle_n_a_rien_mesure(session):
    """Zero compte est une mesure : le rapport doit le dire, pas le chuchoter."""
    lignes = suivi.suivi(session, maintenant=MAINTENANT)
    assert lignes == []
    assert suivi.resume(lignes)["total"] == 0
    texte = suivi.rapport_texte(lignes, MAINTENANT)
    assert "0 compte(s) dans la base" in texte
    assert "aucun compte" in texte
    assert "rien n'a été mesuré" in texte


def test_action_dit_quoi_faire_avec_les_chiffres_du_code(session):
    """L'action cite le refus reel et le plan au-dessus, quota lu dans `accounts`."""
    client, _ = _compte(session, "action@example.re", essai_dans=-2)
    ligne = suivi.etat_client(session, client, maintenant=MAINTENANT)
    assert accounts.REFUS_COMPTE_EXPIRE in ligne["action"]
    assert str(ligne["refus_http"]) in ligne["action"]
    assert accounts.PLAN_LIBELLES["starter"] in ligne["action"]
    quota_starter = f"{accounts.quota_for('starter'):,}".replace(",", " ")
    assert quota_starter in ligne["action"]

    # Le dernier plan n'a pas de plan au-dessus : l'action ne doit pas en inventer.
    haut, _ = _compte(
        session, "haut@example.re", plan="enterprise", essai_dans=-60, cree_il_y_a=120
    )
    _usage(session, haut, il_y_a_jours=30)
    ligne_haut = suivi.etat_client(session, haut, maintenant=MAINTENANT)
    assert ligne_haut["etat"] == "dormant"
    assert ligne_haut["plan_au_dessus"] is None
    assert ligne_haut["plan_au_dessus_txt"] == ""
    assert "plan " not in ligne_haut["action"]  # aucun plan invente


def _cli():
    """`scripts/suivi_clients.py` importe comme module (le dossier n'est pas paquet)."""
    chemin = ROOT / "scripts" / "suivi_clients.py"
    specification = spec_from_file_location("suivi_clients_sous_test", chemin)
    module = module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def test_cli_ecrit_un_rapport_et_sort_en_zero(tmp_path, capsys):
    """Le chemin nominal : rapport produit, code 0, fichier en UTF-8 avec des LF."""
    base = tmp_path / "cli.sqlite"
    moteur = create_engine(f"sqlite:///{base}")
    Customer.__table__.create(moteur)
    CustomerValidation.__table__.create(moteur)
    with sessionmaker(bind=moteur)() as session:
        _compte(session, "cli@example.re", essai_dans=-2, nom="Boutique CLI")
    sortie = tmp_path / "suivi.txt"
    try:
        code = _cli().main(
            [
                "--url",
                f"sqlite:///{base}",
                "--maintenant",
                "2026-10-13T09:00:00+00:00",
                "--sortie",
                str(sortie),
            ]
        )
    finally:
        moteur.dispose()

    assert code == 0
    contenu = sortie.read_text(encoding="utf-8")
    assert "Boutique CLI" in contenu
    assert "Essai terminé" in contenu  # le libelle, pas le code interne de l'etat
    assert "essai_fini" not in contenu
    assert "\r\n" not in contenu  # fins de ligne LF, comme le reste du depot
    assert "Rapport ecrit" in capsys.readouterr().out


def test_cli_code_2_quand_rien_ne_peut_etre_mesure(tmp_path, capsys):
    """Une base sans table n'est pas un rapport vide : c'est un echec de mesure."""
    vide = tmp_path / "vide.sqlite"
    assert _cli().main(["--url", f"sqlite:///{vide}"]) == 2
    capture = capsys.readouterr()
    assert "SUIVI INDISPONIBLE" in capture.err
    assert "tout va bien" in capture.err
    assert "compte(s) dans la base" not in capture.out
