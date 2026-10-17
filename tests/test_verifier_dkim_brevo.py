"""Tests du verificateur DKIM Brevo (`scripts/verifier_dkim_brevo.py`, tache 5).

Trois obligations : **interroger les deux selecteurs** (jamais un seul — Brevo
signe avec les deux), **distinguer un manque d'un ecart** (rien publie n'est pas
la meme chose qu'une cible hors Brevo), et **ne pas rendre de verdict** quand le
DNS n'a pas repondu (un doute n'est pas un manquant).

Les reponses DNS sont des doubles : un test qui interrogerait le vrai DNS
dependrait de l'etat du domaine ce jour-la, donc ne mesurerait rien.
"""

import json
import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: Cible Brevo typique d'un domaine authentifie (forme `b1.<domaine>.dkim.brevo.com`).
CIBLE_1 = "b1.kder-ai-com.dkim.brevo.com"
CIBLE_2 = "b2.kder-ai-com.dkim.brevo.com"

#: Cle DKIM tronquee : la forme suffit a decider si c'est une cle publiee.
CLE_DKIM = "v=DKIM1; k=rsa; p=MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA"

NOM_1 = "mail._domainkey.kder-ai.com"
NOM_2 = "brevo._domainkey.kder-ai.com"


def _module():
    """`scripts/verifier_dkim_brevo.py` importe comme module (pas un paquet)."""
    nom = "verifier_dkim_brevo_sous_test"
    specification = spec_from_file_location(
        nom, ROOT / "scripts" / "verifier_dkim_brevo.py"
    )
    module = module_from_spec(specification)
    sys.modules[nom] = module
    try:
        specification.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(nom, None)
        raise
    return module


def _brancher(module, monkeypatch, cibles=None, valeurs=None):
    """Remplace `cname`/`txt` du script par des reponses figees."""
    cibles = cibles or {}
    valeurs = valeurs or {}
    monkeypatch.setattr(
        module,
        "cname",
        lambda nom, serveur=None, outil=None: list(cibles.get(nom, [])),
    )
    monkeypatch.setattr(
        module,
        "txt",
        lambda nom, serveur=None, outil=None: list(valeurs.get(nom, [])),
    )
    monkeypatch.setattr(module, "outil_dns", lambda: "doublure")


def test_les_deux_selecteurs_sont_toujours_listes_sans_dns(monkeypatch):
    """Rien de publie : deux constats `manquant`, verdict `manquant`, code 1."""
    module = _module()
    _brancher(module, monkeypatch)

    sortie, code = module.rapport(domaine="kder-ai.com", outil="doublure")

    assert [entree["nom"] for entree in sortie["selecteurs"]] == [NOM_1, NOM_2]
    assert {entree["gravite"] for entree in sortie["selecteurs"]} == {"manquant"}
    assert sortie["verdict"]["code"] == "manquant"
    assert sortie["verdict"]["publies"] == 0
    assert sortie["verdict"]["a_publier"] == [NOM_1, NOM_2]
    assert code == 1


def test_deux_cname_brevo_rendent_ok(monkeypatch):
    """Les deux selecteurs publies et heberges par Brevo : verdict `ok`, code 0."""
    module = _module()
    _brancher(module, monkeypatch, cibles={NOM_1: [CIBLE_1], NOM_2: [CIBLE_2]})

    sortie, code = module.rapport(outil="doublure")

    assert [entree["type"] for entree in sortie["selecteurs"]] == ["CNAME", "CNAME"]
    assert sortie["verdict"]["code"] == "ok"
    assert (sortie["verdict"]["publies"], sortie["verdict"]["total"]) == (2, 2)
    assert sortie["verdict"]["a_publier"] == []
    assert code == 0


def test_un_seul_selecteur_publie_est_un_ecart(monkeypatch):
    """Brevo signe avec les deux : un demi-DKIM n'authentifie rien."""
    module = _module()
    _brancher(module, monkeypatch, cibles={NOM_1: [CIBLE_1]})

    sortie, code = module.rapport(outil="doublure")

    assert sortie["verdict"]["code"] == "different"
    assert sortie["verdict"]["publies"] == 1
    assert sortie["verdict"]["a_publier"] == [NOM_2]
    assert code == 2


def test_une_cible_hors_brevo_est_un_ecart(monkeypatch):
    """Un CNAME qui existe mais ne pointe pas chez Brevo ne signe rien."""
    module = _module()
    _brancher(
        module,
        monkeypatch,
        cibles={NOM_1: ["quelquechose.example.com"], NOM_2: [CIBLE_2]},
    )

    sortie, code = module.rapport(outil="doublure")

    assert sortie["verdict"]["code"] == "different"
    assert "hors Brevo" in sortie["verdict"]["detail"]
    assert code == 2

    assert module.est_cible_brevo(CIBLE_1) is True
    assert module.est_cible_brevo("dkim.sendgrid.net") is False


def test_une_cle_txt_est_acceptee_et_une_valeur_quelconque_refusee(monkeypatch):
    """Un compte peut publier la cle en TXT : alors c'est la cle qui tranche."""
    module = _module()
    _brancher(module, monkeypatch, valeurs={NOM_1: [CLE_DKIM], NOM_2: [CLE_DKIM]})

    sortie, code = module.rapport(outil="doublure")
    assert [entree["type"] for entree in sortie["selecteurs"]] == ["TXT", "TXT"]
    assert sortie["verdict"]["code"] == "ok"
    assert code == 0

    _brancher(
        module, monkeypatch, valeurs={NOM_1: ["brevo-code:b9cabf23"], NOM_2: [CLE_DKIM]}
    )
    sortie, code = module.rapport(outil="doublure")
    assert sortie["verdict"]["code"] == "different"
    assert "cle DKIM" in sortie["verdict"]["detail"]
    assert code == 2


def test_attendu_compare_la_valeur_annoncee_par_brevo(monkeypatch):
    """`--attendu` remplace la detection par forme par la valeur de la console."""
    module = _module()
    _brancher(module, monkeypatch, cibles={NOM_1: [CIBLE_1], NOM_2: [CIBLE_2]})

    sortie, code = module.rapport(
        attendus={"mail._domainkey": CIBLE_1}, outil="doublure"
    )
    assert sortie["verdict"]["code"] == "ok"
    assert code == 0

    sortie, code = module.rapport(
        attendus={"mail._domainkey": CIBLE_2}, outil="doublure"
    )
    assert sortie["verdict"]["code"] == "different"
    assert "alors que Brevo annonce" in sortie["verdict"]["detail"]
    assert code == 2


def test_un_dns_muet_ne_rend_pas_de_verdict(monkeypatch):
    """Une panne de resolution est un doute : elle ne devient pas un `manquant`."""
    module = _module()

    def casse(nom, serveur=None, outil=None):
        raise module.ErreurDns("delai d'attente depasse")

    monkeypatch.setattr(module, "cname", casse)
    monkeypatch.setattr(module, "txt", lambda nom, serveur=None, outil=None: [])

    sortie, code = module.rapport(outil="doublure")

    assert [entree["gravite"] for entree in sortie["selecteurs"]] == [
        "non-verifie",
        "non-verifie",
    ]
    assert sortie["verdict"]["code"] == "non-verifie"
    assert len(sortie["selecteurs"]) == 2
    assert code == 3


def test_sans_outil_dns_le_verdict_nest_pas_rendu(monkeypatch):
    """Ni `dig` ni `nslookup` : le rapport sort quand meme, sans verdict."""
    module = _module()

    def aucun():
        raise module.ErreurDns("ni `dig` ni `nslookup` sur cette machine")

    monkeypatch.setattr(module, "outil_dns", aucun)

    sortie, code = module.rapport()

    assert sortie["outil"] is None
    assert sortie["verdict"]["code"] == "non-verifie"
    assert len(sortie["selecteurs"]) == 2
    assert code == 3


def test_main_json_porte_selecteurs_et_verdict(monkeypatch, capsys):
    """La porte T6 : `--json` publie `selecteurs` et un `verdict.code` connu."""
    module = _module()
    _brancher(module, monkeypatch)
    monkeypatch.setattr(module, "_forcer_utf8", lambda: None)

    code = module.main(["--json"])
    charge = json.loads(capsys.readouterr().out)

    assert charge["selecteurs"]
    assert charge["verdict"]["code"] in ("ok", "manquant", "different")
    assert charge["verdict"]["code"] == "manquant"
    assert code == 1


def test_attendu_illisible_sort_en_code_4(monkeypatch, capsys):
    """`--attendu` sans `=` est une erreur d'appel : rien n'est interroge."""
    module = _module()
    monkeypatch.setattr(module, "_forcer_utf8", lambda: None)

    code = module.main(["--attendu", "mail._domainkey"])

    assert code == 4
    assert "erreur d'appel" in capsys.readouterr().out
    assert module._attendus(["mail._domainkey=b1.kder-ai-com.dkim.brevo.com"]) == {
        "mail._domainkey": "b1.kder-ai-com.dkim.brevo.com"
    }
