"""Tests de la decouvrabilite : fichiers publies et journal des refus 402 (D28/D30)."""

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import observability
from app.config import get_settings
from app.main import app

client = TestClient(app)

ROOT = Path(__file__).resolve().parents[1]

PAYLOAD = {
    "address": {
        "street": "1 Test Way",
        "city": "Springfield",
        "state": "IL",
        "zip": "62701",
        "country": "US",
    }
}

#: Fichiers servis en statique par nginx-discovery.conf.
PUBLISHED = ["llms.txt", "AGENTS.md", ".well-known/security.txt", "nginx-discovery.conf"]


@pytest.fixture
def journal(tmp_path, monkeypatch):
    """Journal de decouverte jetable, detache et nettoye apres le test."""
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


@pytest.mark.parametrize("nom", PUBLISHED)
def test_published_file_is_lf_utf8_and_non_empty(nom):
    """D28 : un fichier annonce est un fichier present, propre et utile."""
    brut = (ROOT / nom).read_bytes()
    assert brut.endswith(b"\n"), f"{nom} ne se termine pas par un saut de ligne"
    assert b"\r" not in brut, f"{nom} contient des fins de ligne Windows"
    assert not brut.startswith(b"\xef\xbb\xbf"), f"{nom} commence par un BOM"
    assert len(brut.strip()) > 200, f"{nom} est trop court pour etre utile"


def test_published_files_only_cite_endpoints_that_answer():
    """D28 : aucun chemin cite ne doit renvoyer 404."""
    connus = {getattr(route, "path", "") for route in app.routes}
    connus |= {"/llms.txt", "/AGENTS.md", "/.well-known/security.txt"}
    cites = set()
    for nom in ("llms.txt", "AGENTS.md", ".well-known/security.txt"):
        texte = (ROOT / nom).read_text(encoding="utf-8")
        cites |= {m.group(1).rstrip(".,;)") for m in re.finditer(r"kder-ai\.com(/[A-Za-z0-9._~/\-]*)", texte)}
    assert cites, "aucun chemin cite : le test ne mesure rien"
    assert not (cites - connus), f"chemins cites sans reponse : {sorted(cites - connus)}"


def test_security_txt_is_rfc9116_and_not_expired():
    """D28 : un security.txt perime est pire qu'aucun."""
    texte = (ROOT / ".well-known" / "security.txt").read_text(encoding="utf-8")
    assert re.search(r"(?m)^Contact:\s*mailto:\S+@\S+\.\S+", texte)
    trouve = re.search(r"(?m)^Expires:\s*(\S+)", texte)
    assert trouve, "champ Expires manquant (RFC 9116 §2.5)"
    expire = datetime.fromisoformat(trouve.group(1).replace("Z", "+00:00"))
    assert expire.tzinfo is not None
    assert expire > datetime.now(timezone.utc), f"security.txt perime depuis {trouve.group(1)}"


def test_mcp_card_is_served_by_the_app_and_not_copied_for_nginx():
    """D28/D37 : la carte decrit un endpoint reel, donc elle vit avec lui.

    Un fichier statique servi par Nginx survivrait a l'arret du service et
    ferait declarer aux agents une URL morte : la carte est donc une route de
    l'application, et `nginx-discovery.conf` ne doit pas l'aliaser.
    """
    assert not (ROOT / ".well-known" / "mcp-server").exists(), (
        "une copie statique de la carte peut survivre a l'endpoint qu'elle decrit"
    )
    nginx = (ROOT / "nginx-discovery.conf").read_text(encoding="utf-8")
    assert re.search(r"(?m)^\s*location\s*=\s*/\.well-known/mcp-server", nginx) is None

    card = client.get("/.well-known/mcp-server")
    assert card.status_code == 200
    assert card.json()["endpoint"] == "https://kder-ai.com/mcp"
    tools = client.post(
        "/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    ).json()["result"]["tools"]
    assert tools[0]["name"] == "validate_address"


def test_unpaid_call_is_logged_with_pseudonymised_ip(journal):
    """D30 : un 402 laisse une trace exploitable, sans IP en clair."""
    reponse = client.post("/validate", json=PAYLOAD, headers={"X-Forwarded-For": "203.0.113.5"})
    assert reponse.status_code == 402

    lignes = [json.loads(l) for l in journal.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(lignes) == 1
    entree = lignes[0]
    assert entree["event"] == "unpaid_request"
    assert (entree["method"], entree["endpoint"]) == ("POST", "/validate")
    assert entree["ip_pseudonymised"] is True
    assert "203.0.113.5" not in json.dumps(entree)
    assert entree["ip"] == observability.pseudonymise("203.0.113.5")


def test_unpaid_log_never_contains_the_validated_address(journal):
    """D30/RGPD : le journal ne doit pas devenir un entrepot d'adresses."""
    client.post("/validate", json=PAYLOAD)
    contenu = journal.read_text(encoding="utf-8")
    assert "1 Test Way" not in contenu
    assert "62701" not in contenu


def test_raw_ip_mode_is_explicit(journal, monkeypatch):
    """D30 : l'IP en clair est une option d'enquete, pas le defaut."""
    monkeypatch.setattr(get_settings(), "discovery_log_raw_ip", True)
    client.post("/validate", json=PAYLOAD, headers={"X-Forwarded-For": "198.51.100.7"})
    lignes = [json.loads(l) for l in journal.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert lignes[-1]["ip"] == "198.51.100.7"
    assert lignes[-1]["ip_pseudonymised"] is False


def test_prune_applies_the_announced_retention(journal):
    """D30 : conservation annoncee = conservation appliquee."""
    ancienne = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat(timespec="seconds")
    recente = datetime.now(timezone.utc).isoformat(timespec="seconds")
    journal.write_text(
        json.dumps({"ts": ancienne, "event": "unpaid_request"}) + "\n"
        + json.dumps({"ts": recente, "event": "unpaid_request"}) + "\n",
        encoding="utf-8",
    )
    assert observability.count_since(journal, datetime.now(timezone.utc) - timedelta(days=1)) == 1
    assert observability.prune(journal, 30) == 1
    restant = [json.loads(l) for l in journal.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(restant) == 1 and restant[0]["ts"] == recente
