"""Tests du chargement `.env.smtp` et du test de connexion SMTP (hors reseau).

Objectif : mesurer ce que `scripts/charger_env_smtp.py`,
`scripts/test_smtp_connection.py` et `preparer_environnement()` de
`scripts/envoyer_email_tiitoune.py` **declarent**, sans ouvrir la moindre
connexion. `smtplib.SMTP` / `SMTP_SSL` sont remplaces par une doublure qui
enregistre les appels : la suite prouve donc STARTTLS + authentification, et
l'**absence d'envoi** (la doublure ne sait pas envoyer : un `send_message`
ferait echouer le test).

Ce qui est verifie :

  * le fichier modele livre bien les six variables attendues ;
  * commentaires, lignes vides, guillemets et `export ` sont toleres ;
  * un `#` dans une valeur n'est **pas** un commentaire (un secret peut en
    contenir : la ligne entiere est la valeur) ;
  * une ligne sans `=` est refusee, avec son numero ;
  * un texte encore marque `REMPLACER` est signale et n'est **jamais** pose dans
    l'environnement ;
  * la valeur du fichier fait foi, et l'ecrasement d'une variable
    d'environnement est **signale** ;
  * fichier absent : pas d'exception, et le repli sur l'environnement reste dit ;
  * 587 → STARTTLS puis `login` puis `noop` ; 465 → TLS implicite, sans STARTTLS ;
  * codes de retour de `main()` : 1 si le fichier manque ou si la cle n'est pas
    remplacee, 0 quand la doublure accepte la session.
"""

import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

#: Le fichier livre a l'exploitant (`.env.smtp`), mot de passe encore a remplacer.
FICHIER_MODELE = """# Configuration SMTP Brevo — NE PAS COMMITTER
SMTP_HOST=smtp-relay.brevo.com
SMTP_PORT=587
SMTP_USER=1234567@smtp-brevo.com
SMTP_PASSWORD=REMPLACER_PAR_TA_CLE_SMTP_BREVO
SMTP_FROM=address-validator@kder-ai.com
SMTP_FROM_NAME=Address Validator
"""

#: La meme chose, une fois la cle SMTP Brevo posee (valeur factice).
FICHIER_REMPLI = FICHIER_MODELE.replace(
    "REMPLACER_PAR_TA_CLE_SMTP_BREVO", "xsmtpsib-cle-factice-de-test"
)


def _module(nom: str, chemin: Path):
    """Charge un fichier de `scripts/` comme module (le dossier n'est pas paquet)."""
    specification = spec_from_file_location(nom, chemin)
    module = module_from_spec(specification)
    sys.modules[nom] = module
    try:
        specification.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(nom, None)
        raise
    return module


@pytest.fixture
def chargeur():
    """`scripts/charger_env_smtp.py`."""
    return _module("charger_env_smtp_sous_test", ROOT / "scripts" / "charger_env_smtp.py")


@pytest.fixture
def testeur():
    """`scripts/test_smtp_connection.py`."""
    return _module(
        "test_smtp_connection_sous_test", ROOT / "scripts" / "test_smtp_connection.py"
    )


@pytest.fixture
def envoyer():
    """`scripts/envoyer_email_tiitoune.py` (seule la preparation d'environnement est testee)."""
    return _module(
        "envoyer_email_tiitoune_sous_test", ROOT / "scripts" / "envoyer_email_tiitoune.py"
    )


def _ecrire(tmp_path: Path, contenu: str = FICHIER_MODELE, nom: str = ".env.smtp") -> Path:
    """Ecrit un fichier d'identifiants jetable et rend son chemin."""
    fichier = tmp_path / nom
    fichier.write_text(contenu, encoding="utf-8")
    return fichier


@pytest.fixture
def faux_smtp(testeur, monkeypatch):
    """Remplace `smtplib.SMTP` et `smtplib.SMTP_SSL` : la session est observee, pas ouverte."""
    sessions: list = []

    class FauxServeur:
        def __init__(self, hote, port, timeout=None, context=None):
            self.hote = hote
            self.port = port
            self.appels: list = []
            sessions.append(self)

        def __enter__(self):
            return self

        def __exit__(self, *exception):
            self.appels.append("quit")
            return False

        def ehlo(self):
            self.appels.append("ehlo")

        def starttls(self, context=None):
            self.appels.append("starttls")

        def login(self, utilisateur, mot_de_passe):
            self.appels.append(f"login {utilisateur}")

        def noop(self):
            self.appels.append("noop")

    monkeypatch.setattr(testeur.smtplib, "SMTP", FauxServeur)
    monkeypatch.setattr(testeur.smtplib, "SMTP_SSL", FauxServeur)
    return sessions


# --- L'analyseur du fichier --------------------------------------------------


def test_le_fichier_modele_livre_les_six_variables(chargeur, tmp_path):
    valeurs = chargeur.lire_fichier_env(_ecrire(tmp_path))
    assert set(valeurs) == set(chargeur.VARIABLES)
    assert valeurs["SMTP_HOST"] == "smtp-relay.brevo.com"
    assert valeurs["SMTP_PORT"] == "587"
    assert valeurs["SMTP_USER"] == "1234567@smtp-brevo.com"
    assert valeurs["SMTP_FROM"] == "address-validator@kder-ai.com"


def test_commentaires_lignes_vides_guillemets_et_export(chargeur, tmp_path):
    contenu = '# modele\n\nexport SMTP_HOST="smtp-relay.brevo.com"\nSMTP_PORT="587"\n'
    valeurs = chargeur.lire_fichier_env(_ecrire(tmp_path, contenu))
    assert valeurs == {"SMTP_HOST": "smtp-relay.brevo.com", "SMTP_PORT": "587"}


def test_un_diese_dans_une_valeur_n_est_pas_un_commentaire(chargeur, tmp_path):
    """Une cle peut contenir `#` : tronquer la ligne abimerait le secret."""
    valeurs = chargeur.lire_fichier_env(_ecrire(tmp_path, "SMTP_PASSWORD=xsmtpsib-A#B\n"))
    assert valeurs["SMTP_PASSWORD"] == "xsmtpsib-A#B"


def test_ligne_sans_egal_est_refusee_avec_son_numero(chargeur, tmp_path):
    with pytest.raises(ValueError) as erreur:
        chargeur.lire_fichier_env(_ecrire(tmp_path, "SMTP_HOST\nSMTP_PORT=587\n"))
    assert "ligne 1" in str(erreur.value)


def test_variable_absente_ou_vide_est_signalee(chargeur, tmp_path):
    contenu = "SMTP_HOST=smtp-relay.brevo.com\nSMTP_USER=\nSMTP_PASSWORD=xsmtpsib-reel\n"
    rapport = chargeur.charger_env_smtp(_ecrire(tmp_path, contenu), {})
    assert rapport["trouve"] is True
    problemes = " | ".join(rapport["problemes"])
    for nom in ("SMTP_PORT", "SMTP_FROM", "SMTP_FROM_NAME", "SMTP_USER"):
        assert nom in problemes


# --- Le chargement dans l'environnement -------------------------------------


def test_marqueur_n_est_jamais_pose_dans_l_environnement(chargeur, tmp_path):
    environ: dict = {}
    rapport = chargeur.charger_env_smtp(_ecrire(tmp_path), environ)
    assert "SMTP_PASSWORD" not in environ
    assert environ["SMTP_HOST"] == "smtp-relay.brevo.com"
    assert any("SMTP_PASSWORD" in probleme for probleme in rapport["problemes"])


def test_la_valeur_du_fichier_fait_foi_et_l_ecrasement_est_signale(chargeur, tmp_path):
    environ = {"SMTP_USER": "ancien@exemple.fr", "SMTP_HOST": "smtp-relay.brevo.com"}
    rapport = chargeur.charger_env_smtp(_ecrire(tmp_path), environ)
    assert rapport["ecrasees"] == ["SMTP_USER"]
    assert environ["SMTP_USER"] == "1234567@smtp-brevo.com"


def test_sans_ecrasement_l_environnement_est_conserve(chargeur, tmp_path):
    environ = {"SMTP_USER": "ancien@exemple.fr"}
    rapport = chargeur.charger_env_smtp(_ecrire(tmp_path), environ, ecraser=False)
    assert rapport["ecrasees"] == ["SMTP_USER"]
    assert environ["SMTP_USER"] == "ancien@exemple.fr"


def test_fichier_absent_ne_leve_pas_et_le_dit(chargeur, tmp_path):
    rapport = chargeur.charger_env_smtp(tmp_path / ".env.smtp", {})
    assert rapport["trouve"] is False
    assert rapport["valeurs"] == {}
    assert rapport["problemes"] == [".env.smtp absent"]


# --- La session SMTP (doublure : aucune connexion, aucun envoi) --------------


def test_connexion_587_starttls_puis_login_sans_envoi(testeur, faux_smtp):
    identifiants = {
        "hote": "smtp-relay.brevo.com",
        "port": 587,
        "utilisateur": "1234567@smtp-brevo.com",
        "mot_de_passe": "xsmtpsib-cle-factice-de-test",
    }
    testeur.tester_connexion(identifiants, delai=1.0)
    (serveur,) = faux_smtp
    assert (serveur.hote, serveur.port) == ("smtp-relay.brevo.com", 587)
    assert serveur.appels == [
        "starttls",
        "ehlo",
        "login 1234567@smtp-brevo.com",
        "noop",
        "quit",
    ]


def test_connexion_465_utilise_le_tls_implicite(testeur, faux_smtp):
    identifiants = {
        "hote": "smtp-relay.brevo.com",
        "port": 465,
        "utilisateur": "1234567@smtp-brevo.com",
        "mot_de_passe": "xsmtpsib-cle-factice-de-test",
    }
    testeur.tester_connexion(identifiants, delai=1.0)
    (serveur,) = faux_smtp
    assert serveur.appels == ["login 1234567@smtp-brevo.com", "noop", "quit"]


def test_port_illisible_est_refuse_avec_un_message_clair(testeur):
    with pytest.raises(ValueError) as erreur:
        testeur.port_depuis("port")
    assert "SMTP_PORT illisible" in str(erreur.value)


# --- La ligne de commande ----------------------------------------------------


def test_main_refuse_une_cle_non_remplacee(testeur, tmp_path, capsys, monkeypatch):
    for nom in testeur.VARIABLES:
        monkeypatch.delenv(nom, raising=False)
    code = testeur.main(["--fichier", str(_ecrire(tmp_path))])
    sortie = capsys.readouterr().out
    assert code == 1
    assert "❌ Erreur : SMTP_PASSWORD" in sortie
    assert "REMPLACER" in sortie
    assert "Configuration SMTP valide" not in sortie


def test_main_sans_fichier_dit_quoi_creer(testeur, tmp_path, capsys):
    code = testeur.main(["--fichier", str(tmp_path / "absent.env.smtp")])
    sortie = capsys.readouterr().out
    assert code == 1
    assert "introuvable" in sortie
    assert "creez .env.smtp" in sortie
    assert "SMTP_HOST=..." in sortie


def test_main_succes_annonce_configuration_valide(testeur, faux_smtp, tmp_path, capsys, monkeypatch):
    for nom in testeur.VARIABLES:
        monkeypatch.delenv(nom, raising=False)
    code = testeur.main(["--fichier", str(_ecrire(tmp_path, FICHIER_REMPLI)), "--delai", "1"])
    sortie = capsys.readouterr().out
    assert code == 0
    assert "✅ Configuration SMTP valide" in sortie
    assert "✅ Prêt à envoyer des emails" in sortie
    assert "login : 1234567@smtp-brevo.com" in sortie
    assert "hote  : smtp-relay.brevo.com" in sortie
    assert "port  : 587" in sortie
    assert faux_smtp[0].appels == [
        "starttls",
        "ehlo",
        "login 1234567@smtp-brevo.com",
        "noop",
        "quit",
    ]


# --- La preparation d'environnement du script d'envoi ------------------------


def _purger(monkeypatch, module) -> None:
    """Retire les variables SMTP du processus et enregistre leur retablissement."""
    for nom in (*module.VARIABLES_OBLIGATOIRES, *module.VARIABLES_FACULTATIVES):
        monkeypatch.delenv(nom, raising=False)


def test_preparer_environnement_charge_le_fichier_et_le_dit(envoyer, tmp_path, monkeypatch):
    _purger(monkeypatch, envoyer)
    rapport = envoyer.preparer_environnement(_ecrire(tmp_path, FICHIER_REMPLI))
    assert rapport["trouve"] is True
    assert rapport["a_remplacer"] == []
    assert ".env.smtp charge (6 variables)" in " | ".join(rapport["messages"])


def test_preparer_environnement_sans_fichier_invite_a_le_creer(envoyer, tmp_path):
    rapport = envoyer.preparer_environnement(tmp_path / ".env.smtp")
    assert rapport["trouve"] is False
    assert "Creez .env.smtp avec vos identifiants" in " ".join(rapport["messages"])


def test_preparer_environnement_bloque_sur_un_marqueur(envoyer, tmp_path, monkeypatch):
    _purger(monkeypatch, envoyer)
    rapport = envoyer.preparer_environnement(_ecrire(tmp_path))
    assert rapport["a_remplacer"] == ["SMTP_PASSWORD"]
    assert rapport["trouve"] is True
