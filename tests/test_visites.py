"""Tests du rapport de visites (`scripts/analyser_visites.py`, tache 4).

Trois obligations, chacune avec ses tests : **lire** un journal d'acces Nginx
sans se laisser arreter par une ligne illisible ; **mesurer** sept sections, ni
plus ni moins, sur des mesures d'acces ; **ne pas crier au loup** — une
redirection sur `/.env` n'expose rien, un `200` sur le meme chemin est une fuite.

Les chemins et les User-Agents utilises sont ceux releves dans le §4 de
`AUDIT_TRAFIC_VPS.md` (sondes reelles des 10-11/10/2026) : un test qui inventerait
ses propres chemins ne prouverait rien du trafic reel. Les lignes sont horodatees
**relativement a maintenant**, sinon le test se mettrait a mentir a mesure que la
fenetre `--since` vieillit.
"""

import json
import sys
from datetime import datetime, timedelta, timezone
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

from app import observability

ROOT = Path(__file__).resolve().parents[1]

#: Mois anglais du format Nginx. `%b` depend de la locale du poste : un test qui
#: compterait sur `strftime` passerait sur une machine et echouerait sur l'autre.
MOIS = (
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)

UA_NMAP = "Mozilla/5.0 (compatible; Nmap Scripting Engine)"
UA_GPTBOT = "Mozilla/5.0 AppleWebKit/537.36; compatible; GPTBot/1.2; +https://openai.com/gptbot"
UA_BING = "Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)"
UA_CHROME = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0 Safari/537.36"
)

#: Sept sections attendues, dans l'ordre ou l'exploitant se pose les questions.
SECTIONS = (
    "chiffres_cles",
    "par_jour",
    "top_pages",
    "top_ip",
    "codes_statut",
    "clients",
    "anomalies",
)


def _horodatage(decalage: timedelta = timedelta()) -> str:
    """Instant Nginx (`15/Oct/2026:08:23:45 +0000`) decale dans le passe."""
    moment_lu = datetime.now(timezone.utc) - decalage
    return (
        f"{moment_lu.day:02d}/{MOIS[moment_lu.month - 1]}/{moment_lu.year}:"
        f"{moment_lu.hour:02d}:{moment_lu.minute:02d}:{moment_lu.second:02d} +0000"
    )


def _ligne_nginx(
    ip: str,
    chemin: str,
    statut: int,
    agent: str,
    octets: int = 100,
    decalage: timedelta = timedelta(),
) -> str:
    """Une ligne d'acces Nginx au format `combined`, celle de `nginx.conf`."""
    return (
        f'{ip} - - [{_horodatage(decalage)}] "GET {chemin} HTTP/1.1" '
        f'{statut} {octets} "-" "{agent}"'
    )


#: Le trafic d'une journee : trois sondes de scanners, un agent IA qui paie et
#: qui echoue, un moteur, un humain, et un appel sans User-Agent.
LIGNES = (
    _ligne_nginx("35.209.101.84", "/.env", 404, UA_NMAP, 153),
    _ligne_nginx(
        "187.87.144.234",
        "/vendor/phpunit/phpunit/src/Util/PHP/eval-stdin.php",
        404,
        "libredtail-http",
        162,
    ),
    _ligne_nginx("45.138.12.51", "/.git/HEAD", 301, "curl/8.5.0", 0),
    _ligne_nginx("20.171.207.12", "/validate", 402, UA_GPTBOT, 89),
    _ligne_nginx("20.171.207.12", "/validate", 200, UA_GPTBOT, 74),
    _ligne_nginx("92.130.44.231", "/docs", 200, UA_CHROME, 5120),
    _ligne_nginx("40.77.167.0", "/robots.txt", 200, UA_BING, 212),
    _ligne_nginx("92.130.44.231", "/health", 200, "", 41),
)

MESURES = [observability.parse_nginx_line(ligne) for ligne in LIGNES]


def _section(rapport: dict, identifiant: str) -> dict:
    """La section `identifiant` du rapport (une seule porte d'acces aux chiffres)."""
    return next(section for section in rapport["sections"] if section["id"] == identifiant)


def _module():
    """`scripts/analyser_visites.py` importe comme module (le dossier n'est pas paquet)."""
    nom = "analyser_visites_sous_test"
    specification = spec_from_file_location(
        nom, ROOT / "scripts" / "analyser_visites.py"
    )
    module = module_from_spec(specification)
    sys.modules[nom] = module
    try:
        specification.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(nom, None)
        raise
    return module


def _options(module, **remplacements):
    """Les options du script telles que `parseur()` les rend, avec surcharges."""
    options = module.parseur().parse_args([])
    for cle, valeur in remplacements.items():
        setattr(options, cle, valeur)
    return options


def test_parse_nginx_line_rend_la_mesure_sans_chaine_de_requete():
    """Ce que Nginx ecrit devient une mesure : classe, chemin, statut, octets."""
    assert all(mesure is not None for mesure in MESURES)
    mesure = observability.parse_nginx_line(LIGNES[0])
    assert (mesure["ip"], mesure["chemin"], mesure["statut"], mesure["octets"]) == (
        "35.209.101.84",
        "/.env",
        404,
        153,
    )
    assert mesure["classe"] == "outil"
    assert mesure["agent"] == "Nmap"

    avec_requete = _ligne_nginx("203.0.113.7", "/validate?ref=gptbot", 200, UA_GPTBOT)
    assert observability.parse_nginx_line(avec_requete)["chemin"] == "/validate"


def test_une_ligne_illisible_ou_sans_date_est_ecartee():
    """Sans instant lisible, une ligne n'entre dans aucune fenetre : elle sort."""
    assert observability.parse_nginx_line("ceci n'est pas une ligne combined") is None
    illisible = (
        '203.0.113.7 - - [15/Foo/2026:10:23:45 +0000] "GET / HTTP/1.1" 200 10 '
        '"-" "curl/8.5.0"'
    )
    assert observability.parse_nginx_line(illisible) is None


def test_le_rapport_rend_les_sept_sections_dans_lordre():
    """Sept sections, l'ordre des questions, et aucune section muette."""
    rapport = observability.visites_report(MESURES)
    assert [section["id"] for section in rapport["sections"]] == list(SECTIONS)
    assert rapport["requetes"] == len(LIGNES)
    assert all(section["lignes"] for section in rapport["sections"])


def test_les_chiffres_cles_comptent_les_lignes_lues():
    """Le premier chiffre est celui qu'on verifie : rien de perdu, rien d'invente."""
    rapport = observability.visites_report(MESURES)
    chiffres = _section(rapport, "chiffres_cles")["donnees"]
    assert chiffres["requetes"] == 8
    assert chiffres["classes"]["agent_ia"] == 2
    assert chiffres["classes"]["navigateur"] == 1
    assert sum(chiffres["classes"].values()) == 8
    assert chiffres["octets"] == sum(mesure["octets"] for mesure in MESURES)
    assert chiffres["methodes"] == {"GET": 8}
    assert chiffres["plage"]["debut"] <= chiffres["plage"]["fin"]


def test_les_codes_de_statut_sont_regroupes_par_famille():
    """`402` et `404` ne se lisent pas ensemble : la famille, puis le detail."""
    rapport = observability.visites_report(MESURES)
    donnees = _section(rapport, "codes_statut")["donnees"]
    assert donnees["par_famille"] == {"2xx": 4, "3xx": 1, "4xx": 3, "5xx": 0, "inconnu": 0}
    assert {entree["code"] for entree in donnees["par_code"]} == {"200", "301", "402", "404"}


def test_un_chemin_a_plusieurs_statuts_les_montre_tous():
    """`/validate` repond `200` quand le paiement passe et `402` quand il rate."""
    rapport = observability.visites_report(MESURES)
    validate = next(
        entree
        for entree in _section(rapport, "top_pages")["donnees"]["entrees"]
        if entree["chemin"] == "/validate"
    )
    assert (validate["requetes"], validate["familles"]) == (2, {"2xx": 1, "4xx": 1})
    ligne = next(
        ligne for ligne in _section(rapport, "top_pages")["lignes"] if "/validate" in ligne
    )
    assert "2xx=1" in ligne and "4xx=1" in ligne


def test_une_redirection_nest_pas_une_fuite_mais_un_200_en_est_une():
    """`/.git/HEAD` en `301` renvoie ailleurs ; en `200`, le fichier est servi."""
    redirection = observability.parse_nginx_line(
        _ligne_nginx("45.138.12.51", "/.git/HEAD", 301, "curl/8.5.0", 0)
    )
    anomalies = _section(observability.visites_report([redirection]), "anomalies")["donnees"]
    assert anomalies["chemins_sensibles"]["requetes"] == 1
    assert anomalies["fuites"] == []

    servi = observability.parse_nginx_line(
        _ligne_nginx("45.138.12.51", "/.env", 200, "curl/8.5.0", 12)
    )
    anomalies = _section(observability.visites_report([servi]), "anomalies")["donnees"]
    assert anomalies["fuites"] == [{"chemin": "/.env", "requetes": 1}]


def test_les_sondeurs_sont_comptes_sur_les_chemins_sensibles():
    """Le §4 de `AUDIT_TRAFIC_VPS.md` : trois chemins sondes, tous en `404`/`301`."""
    rapport = observability.visites_report(MESURES)
    sensibles = _section(rapport, "anomalies")["donnees"]["chemins_sensibles"]
    assert sensibles["requetes"] == 3
    assert {entree["chemin"] for entree in sensibles["entrees"]} == {
        "/.env",
        "/.git/HEAD",
        "/vendor/phpunit/phpunit/src/Util/PHP/eval-stdin.php",
    }
    assert sensibles["sondeurs"]
    assert all(
        len(sondeur["ip"]) == 32 for sondeur in sensibles["sondeurs"]
    ), "un rapport publie une adresse pseudonymisee, jamais une IP en clair"


def test_les_adresses_sont_pseudonymisees_sauf_demande_explicite():
    """D30 : le rapport ne publie pas d'adresse en clair, `--ip-en-clair` excepte."""
    pseudonymise = observability.visites_report(MESURES)
    assert pseudonymise["ip_pseudonymised"] is True
    assert "35.209.101.84" not in json.dumps(pseudonymise)
    entrees = _section(pseudonymise, "top_ip")["donnees"]["entrees"]
    assert entrees and all(len(entree["ip"]) == 32 for entree in entrees)

    en_clair = observability.visites_report(MESURES, ip_en_clair=True)
    assert en_clair["ip_pseudonymised"] is False
    assert "35.209.101.84" in json.dumps(en_clair)


def test_le_script_lit_un_journal_fourni(tmp_path):
    """Le script sur un journal reel : huit mesures lues, sept sections, code 0."""
    module = _module()
    fichier = tmp_path / "access.log"
    fichier.write_text("\n".join(LIGNES) + "\n", encoding="utf-8")

    sortie, code = module.rapport(_options(module, acces=str(fichier), since="24h"))

    assert code == 0
    assert [section["id"] for section in sortie["sections"]] == list(SECTIONS)
    assert (sortie["source"]["lu"], sortie["source"]["mesures"]) == (True, 8)
    assert sortie["requetes"] == 8


def test_le_script_rend_sept_sections_sans_journal(capsys, monkeypatch):
    """La porte T5 : sans journal nomme, le rapport sort quand meme, en code 0."""
    module = _module()
    monkeypatch.setattr(module, "_forcer_utf8", lambda: None)

    code = module.main(["--since", "24h", "--json"])
    charge = json.loads(capsys.readouterr().out)

    assert code == 0, "un journal absent par defaut n'est pas une erreur"
    assert charge["source"]["chemin"] == module.ACCES_DEFAUT
    assert [section["id"] for section in charge["sections"]] == list(SECTIONS)


def test_un_journal_nomme_mais_absent_sort_en_code_2(tmp_path, capsys, monkeypatch):
    """Une source nommee par l'operateur qui ne repond pas est une erreur d'appel."""
    module = _module()
    monkeypatch.setattr(module, "_forcer_utf8", lambda: None)

    code = module.main(["--acces", str(tmp_path / "absent.log"), "--since", "24h"])

    assert code == 2
    assert "fichier absent" in capsys.readouterr().out
