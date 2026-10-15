"""Tests de l'observabilite des agents IA (D67) : classer, journaliser, publier.

Trois obligations, chacune avec ses tests : **classer** chaque appelant sans le
confondre avec un moteur ou un navigateur ; **journaliser** les agents IA sans
jamais journaliser les visites humaines ; **publier** les cinq mesures
(`agents`, `top_ip`, `top_agents`, `top_pages`, `conversion`) sur l'endpoint
comme dans le script.

Les User-Agents utilises sont ceux que les editeurs publient
(`docs/AGENTS-IA.md`) : un test de classification qui invente ses propres
chaines ne prouve rien du trafic reel.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import observability
from app.config import get_settings
from app.main import app

client = TestClient(app)

ROOT = Path(__file__).resolve().parents[1]

#: User-Agents reels, jeton volontairement au milieu de la chaine (comme dans la
#: nature) : attendu = (classe, nom publie).
APPELANTS = [
    ("Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; GPTBot/1.2; +https://openai.com/gptbot", "agent_ia", "GPTBot"),
    ("Mozilla/5.0 (compatible; ClaudeBot/1.0; +claudebot@anthropic.com)", "agent_ia", "ClaudeBot"),
    ("Mozilla/5.0 (compatible; PerplexityBot/1.0; +https://perplexity.ai/perplexitybot)", "agent_ia", "PerplexityBot"),
    ("Mozilla/5.0 (compatible; Google-Extended/1.0; +http://www.google.com/bot.html)", "agent_ia", "Google-Extended"),
    ("mcpbeat/0.1 (+https://mcpbeat.com)", "annuaire_mcp", "mcpbeat"),
    ("Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)", "moteur", "Googlebot"),
    ("Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)", "moteur", "Bingbot"),
    ("curl/8.5.0", "outil", "curl"),
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36", "navigateur", "navigateur"),
    ("", "inconnu", "inconnu"),
]

#: Une ligne d'acces Nginx au format `combined`, telle que `nginx.conf` l'ecrit.
LIGNE_NGINX = (
    '203.0.113.7 - - [15/Oct/2026:10:23:45 +0200] "GET /validate?x=1 HTTP/1.1" 402 512 '
    '"-" "Mozilla/5.0 AppleWebKit/537.36; compatible; GPTBot/1.2; +https://openai.com/gptbot"'
)


def _ts(offset_days: float = 0.0) -> str:
    """Horodatage ISO UTC decale, pour vieillir une entree a volonte."""
    return (datetime.now(timezone.utc) - timedelta(days=offset_days)).isoformat(
        timespec="seconds"
    )


def _entree_agent(agent: str, chemin: str, statut: int, ip: str = "a" * 32) -> dict:
    """Entree de journal telle que `log_agent_request` l'ecrit."""
    return {
        "ts": _ts(),
        "event": "agent_ia",
        "classe": "agent_ia",
        "agent": agent,
        "ip": ip,
        "endpoint": chemin,
        "status": statut,
        "user_agent": f"{agent}/1.0",
    }


@pytest.fixture
def journal(tmp_path, monkeypatch):
    """Journal de decouverte jetable (meme montage que tests/test_discovery.py)."""
    settings = get_settings()
    chemin = tmp_path / "discovery.jsonl"
    monkeypatch.setattr(settings, "discovery_log_enabled", True)
    monkeypatch.setattr(settings, "discovery_log_path", str(chemin))
    monkeypatch.setattr(settings, "discovery_log_raw_ip", False)
    monkeypatch.setattr(settings, "discovery_log_salt", "sel-de-test")
    yield chemin
    for handler in list(observability.logger.handlers):
        observability.logger.removeHandler(handler)
        handler.close()
    observability._ATTACHED.clear()


@pytest.mark.parametrize("user_agent,classe,agent", APPELANTS)
def test_chaque_appelant_recoit_sa_classe_et_son_nom(user_agent, classe, agent):
    """Classer juste : un moteur n'est pas un agent IA, un humain n'est pas un robot."""
    assert classe in observability.CLASSES_APPELANTS
    assert observability.agent_class(user_agent) == classe
    assert observability.agent_name(user_agent) == agent


@pytest.mark.parametrize("user_agent,classe,agent", APPELANTS)
def test_seuls_les_agents_ia_sont_des_agents_ia(user_agent, classe, agent):
    """`is_ai_agent` decide de la journalisation : il doit etre exact, pas large."""
    assert observability.is_ai_agent(user_agent) is (classe == "agent_ia")


def test_un_jeton_precis_gagne_sur_son_prefixe():
    """`Applebot-Extended` (entrainement) n'est pas `Applebot` (indexation).

    Le prefixe est commun aux deux chaines : sans tri par longueur du jeton, un
    robot d'entrainement serait compte comme un moteur de recherche et la
    mesure de l'entrainement disparaitrait du rapport.
    """
    entrainement = "Mozilla/5.0 (compatible; Applebot-Extended/1.0)"
    indexation = "Mozilla/5.0 (compatible; Applebot/0.1; +http://www.apple.com/go/applebot)"
    assert observability.agent_class(entrainement) == "agent_ia"
    assert observability.agent_name(entrainement) == "Applebot-Extended"
    assert observability.agent_class(indexation) == "moteur"
    assert observability.agent_name(indexation) == "Applebot"


def test_la_liste_blanche_publiee_decrit_toute_la_table():
    """Une liste blanche partielle serait un mensonge publie."""
    liste = observability.ai_agent_whitelist()
    assert len(liste) == len(observability.AGENTS_IA)
    # Ordering ignore la casse, comme partout ailleurs dans le rapport : une
    # publication doit se lire a l'oeil, pas selon le code ASCII des majuscules.
    assert [ligne["agent"] for ligne in liste] == sorted(
        (ligne["agent"] for ligne in liste), key=str.lower
    )
    for ligne in liste:
        assert set(ligne) == {"jeton", "agent", "editeur", "domaine"}
        assert ligne["jeton"] in observability.AGENTS_IA
        nom, editeur, domaine = observability.AGENTS_IA[ligne["jeton"]]
        assert ligne["editeur"], f"editeur manquant pour {ligne['agent']}"
        assert (nom, editeur, domaine) == (
            ligne["agent"],
            ligne["editeur"],
            ligne["domaine"],
        )


def test_le_rapport_rend_les_cinq_mesures_attendues():
    """Les cinq mesures sont la raison d'etre du chantier : elles doivent sortir."""
    rapport = observability.agents_report(
        [
            _entree_agent("GPTBot", "/pricing", 200),
            _entree_agent("GPTBot", "/validate", 200),
            _entree_agent("ClaudeBot", "/validate", 402, ip="b" * 32),
            {
                "ts": _ts(),
                "event": "annuaire_mcp",
                "classe": "annuaire_mcp",
                "agent": "mcpbeat",
                "ip": "c" * 32,
                "endpoint": "/.well-known/mcp-server",
                "status": 200,
                "user_agent": "mcpbeat/0.1",
            },
            {
                "ts": _ts(),
                "event": "unpaid_request",
                "ip": "d" * 32,
                "endpoint": "/validate",
                "status": 402,
                "user_agent": "Mozilla/5.0 (X11; Linux x86_64) Gecko/20100101 Firefox/133.0",
            },
        ]
    )
    assert set(rapport) == {"agents", "top_ip", "top_agents", "top_pages", "conversion"}
    assert set(rapport["agents"]) == set(observability.CLASSES_APPELANTS)
    assert rapport["agents"]["agent_ia"] == 3
    assert rapport["agents"]["annuaire_mcp"] == 1
    assert rapport["agents"]["navigateur"] == 1
    assert rapport["agents"]["moteur"] == 0

    assert [(l["ip"], l["requetes"]) for l in rapport["top_ip"]] == [
        ("a" * 32, 2),
        ("b" * 32, 1),
        ("c" * 32, 1),
    ]
    assert [(l["agent"], l["requetes"]) for l in rapport["top_agents"]] == [
        ("GPTBot", 2),
        ("ClaudeBot", 1),
        ("mcpbeat", 1),
    ]
    assert rapport["top_agents"][0]["editeur"] == "OpenAI"
    assert [(l["chemin"], l["requetes"]) for l in rapport["top_pages"]] == [
        ("/validate", 2),
        ("/.well-known/mcp-server", 1),
        ("/pricing", 1),
    ]
    assert rapport["conversion"]["requetes"] == 3
    assert rapport["conversion"]["payees"] == 1
    assert rapport["conversion"]["taux"] == round(1 / 3, 4)


def test_les_visites_humaines_ne_polluent_pas_les_tops():
    """Un humain est compte dans `agents`, jamais dans les tops : ils parlent des robots."""
    humain = {
        "ts": _ts(),
        "event": "unpaid_request",
        "ip": "e" * 32,
        "endpoint": "/validate",
        "status": 402,
        "user_agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X) AppleWebKit/605.1 Safari/605.1",
    }
    rapport = observability.agents_report([humain])
    assert rapport["agents"]["navigateur"] == 1
    assert rapport["top_ip"] == []
    assert rapport["top_agents"] == []
    assert rapport["top_pages"] == []
    assert rapport["conversion"]["requetes"] == 0


def test_la_conversion_ne_compte_que_les_paiements_acceptes():
    """Conversion = `200` sur `/validate` **des agents IA**, rien d'autre.

    Compter un `402`, un `200` sur `/health` ou un `200` d'annuaire MCP
    gonflerait un taux qui sert a decider d'investir dans la decouvrabilite : il
    doit refuser de se tromper, meme en notre faveur.
    """
    rapport = observability.agents_report(
        [
            _entree_agent("GPTBot", "/validate", 200),
            _entree_agent("GPTBot", "/validate", 402),
            _entree_agent("GPTBot", "/health", 200),
            {
                "ts": _ts(),
                "classe": "annuaire_mcp",
                "agent": "mcpbeat",
                "ip": "c" * 32,
                "chemin": "/validate",
                "statut": 200,
            },
            {
                "ts": _ts(),
                "classe": "navigateur",
                "agent": "navigateur",
                "ip": "f" * 32,
                "chemin": "/validate",
                "statut": 200,
            },
        ]
    )
    assert rapport["conversion"]["requetes"] == 3
    assert rapport["conversion"]["payees"] == 1


def test_un_rapport_sans_trafic_est_un_rapport_vide_pas_une_erreur():
    """L'absence de trafic est une mesure : elle doit s'afficher, pas lever."""
    rapport = observability.agents_report([])
    assert rapport["agents"] == {
        classe: 0 for classe in observability.CLASSES_APPELANTS
    }
    assert rapport["top_ip"] == []
    assert rapport["top_agents"] == []
    assert rapport["top_pages"] == []
    assert rapport["conversion"]["requetes"] == 0
    assert rapport["conversion"]["payees"] == 0
    assert rapport["conversion"]["taux"] == 0.0
    assert "/validate" in rapport["conversion"]["definition"]


def test_les_tops_sont_limites_et_tries_du_plus_actif_au_moins_actif():
    """Un top de 3 sur 4 agents rend 3 lignes, et une lecture repetee les rend pareil."""
    evenements = []
    for rang, (agent, requetes) in enumerate(
        [("GPTBot", 5), ("ClaudeBot", 3), ("PerplexityBot", 3), ("CCBot", 1)]
    ):
        for _ in range(requetes):
            evenements.append(_entree_agent(agent, "/pricing", 200, ip=f"{rang}" * 32))

    rapport = observability.agents_report(evenements, limite=3)
    assert [l["agent"] for l in rapport["top_agents"]] == [
        "GPTBot",
        "ClaudeBot",
        "PerplexityBot",
    ]
    assert [l["requetes"] for l in rapport["top_agents"]] == [5, 3, 3]
    assert observability.agents_report(evenements, limite=3) == rapport


def test_une_ligne_nginx_combined_devient_une_mesure():
    """Le rapport relu sur disque doit lire le trafic comme l'endpoint en memoire."""
    mesure = observability.parse_nginx_line(LIGNE_NGINX)
    assert mesure is not None
    assert mesure["ip"] == "203.0.113.7"
    assert (mesure["methode"], mesure["chemin"]) == ("GET", "/validate")
    assert mesure["statut"] == 402
    assert mesure["octets"] == 512
    assert (mesure["classe"], mesure["agent"]) == ("agent_ia", "GPTBot")
    assert mesure["moment"] == datetime(2026, 10, 15, 8, 23, 45, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    "ligne",
    [
        "",
        "pas une ligne nginx du tout",
        '1.2.3.4 - - [15/Oct/2026:10:23:45 +0200] "GET / HTTP/1.1"',
        '1.2.3.4 - - [15/Xxx/2026:10:23:45 +0200] "GET / HTTP/1.1" 200 1 "-" "-"',
    ],
)
def test_une_ligne_illisible_ne_casse_pas_la_lecture(ligne):
    """Un journal d'acces contient toujours du bruit : il ne doit rien interrompre."""
    assert observability.parse_nginx_line(ligne) is None


def test_le_journal_dacces_ignore_les_lignes_cassees_et_les_vides(tmp_path):
    fichier = tmp_path / "access.log"
    fichier.write_text("ligne cassee\n" + LIGNE_NGINX + "\n\n", encoding="utf-8")
    mesures = list(observability.lire_access_log(fichier))
    assert len(mesures) == 1
    assert mesures[0]["agent"] == "GPTBot"


def test_le_journal_dacces_applique_la_fenetre(tmp_path):
    """Une fenetre courte ne doit pas rapporter une requete plus vieille qu'elle."""
    fichier = tmp_path / "access.log"
    fichier.write_text(LIGNE_NGINX + "\n", encoding="utf-8")
    # La ligne est datee du 15/10/2026 10:23:45 +0200, soit 08:23:45 UTC.
    avant = datetime(2026, 10, 15, 7, 0, tzinfo=timezone.utc)
    apres = datetime(2026, 10, 15, 9, 0, tzinfo=timezone.utc)
    assert len(list(observability.lire_access_log(fichier, depuis_le=avant))) == 1
    assert list(observability.lire_access_log(fichier, depuis_le=apres)) == []


def test_un_journal_absent_rend_une_liste_vide_pas_une_erreur(tmp_path):
    """Machine sans Nginx : le rapport doit le dire, pas echouer."""
    assert list(observability.lire_access_log(tmp_path / "absent.log")) == []


def test_depuis_lit_une_duree_relative_une_date_et_renonce_sur_le_reste():
    """`--since` accepte `24h` et une date ISO ; l'illisible rend `None`, jamais un faux."""
    maintenant = datetime.now(timezone.utc)
    hier = observability.depuis("24h")
    assert hier is not None
    assert timedelta(hours=23) < maintenant - hier < timedelta(hours=25)
    fixe = observability.depuis("2026-10-15T00:00:00+00:00")
    assert fixe == datetime(2026, 10, 15, tzinfo=timezone.utc)
    assert observability.depuis("2026-10-15T00:00:00") == fixe
    assert observability.depuis("la semaine derniere") is None
    assert observability.depuis("") is None


def test_la_fenetre_ecarte_le_vieux_et_garde_l_inconnu():
    """Un evenement sans horodatage lisible est montre : c'est une anomalie a voir."""
    vieux = {"ts": _ts(offset_days=10)}
    recent = {"ts": _ts()}
    sans_heure = {"event": "unpaid_request"}
    borne = datetime.now(timezone.utc) - timedelta(days=1)
    assert list(observability.filtrer_depuis([vieux, recent, sans_heure], borne)) == [
        recent,
        sans_heure,
    ]
    assert list(observability.filtrer_depuis([vieux], None)) == [vieux]


def test_lendpoint_metrics_agents_publie_les_cinq_mesures():
    """`GET /metrics/agents` est la porte en direct des cinq mesures (D67)."""
    reponse = client.get("/metrics/agents", headers={"User-Agent": "curl/8.5.0"})
    assert reponse.status_code == 200
    charge = reponse.json()
    assert {"agents", "top_ip", "top_agents", "top_pages", "conversion"} <= set(charge)
    assert set(charge["agents"]) == set(observability.CLASSES_APPELANTS)
    assert "compteurs du processus" in charge["source"]
    assert len(charge["whitelist"]) == len(observability.AGENTS_IA)


def test_un_agent_ia_est_compte_publie_et_pseudonymise(journal):
    """Compter, publier, pseudonymiser : les trois tiennent dans la meme reponse."""
    client.get(
        "/pricing", headers={"User-Agent": "Mozilla/5.0; compatible; GPTBot/1.2"}
    )
    charge = client.get("/metrics/agents").json()
    assert charge["agents"]["agent_ia"] >= 1
    assert {"agent": "GPTBot", "editeur": "OpenAI"} in [
        {"agent": ligne["agent"], "editeur": ligne["editeur"]}
        for ligne in charge["top_agents"]
    ]
    assert "/pricing" in [ligne["chemin"] for ligne in charge["top_pages"]]
    assert "testclient" not in json.dumps(charge)
    assert all(len(ligne["ip"]) == 32 for ligne in charge["top_ip"])


def test_le_journal_ne_garde_que_les_agents_ia(journal):
    """Un humain est compte, jamais journalise : le journal doit rester lisible."""
    client.get(
        "/pricing", headers={"User-Agent": "Mozilla/5.0; compatible; GPTBot/1.2"}
    )
    client.get(
        "/pricing",
        headers={
            "User-Agent": "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko)"
            " Chrome/131.0.0.0 Safari/537.36"
        },
    )
    lignes = [
        json.loads(ligne)
        for ligne in journal.read_text(encoding="utf-8").splitlines()
        if ligne.strip()
    ]
    assert len(lignes) == 1
    entree = lignes[0]
    assert (entree["event"], entree["classe"], entree["agent"]) == (
        "agent_ia",
        "agent_ia",
        "GPTBot",
    )
    assert entree["editeur"] == "OpenAI"
    assert entree["ip_pseudonymised"] is True
    assert "testclient" not in json.dumps(entree)


def test_le_journal_des_agents_ne_garde_aucune_adresse_validee(journal):
    """RGPD : le journal des appelants n'est pas un entrepot d'adresses."""
    client.post(
        "/validate",
        json={
            "address": {
                "street": "1 Test Way",
                "city": "Springfield",
                "state": "IL",
                "zip": "62701",
                "country": "US",
            }
        },
        headers={"User-Agent": "Mozilla/5.0; compatible; GPTBot/1.2"},
    )
    contenu = journal.read_text(encoding="utf-8")
    assert "agent_ia" in contenu
    assert "1 Test Way" not in contenu
    assert "62701" not in contenu


def test_une_panne_de_mesure_ne_casse_pas_la_reponse(monkeypatch):
    """Une observabilite qui ferait echouer une requete serait pire que pas d'observabilite."""

    def explose(_user_agent):
        raise RuntimeError("classification cassee")

    monkeypatch.setattr(observability, "agent_class", explose)
    assert client.get("/metrics").status_code == 200



