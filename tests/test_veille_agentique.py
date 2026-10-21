"""Tests de la veille agentique (script d'exploitation, D96).

Aucun acces reseau : les fonctions pures sont testees sur des entrees fabriquees ;
les sources distantes sont hors du perimetre de ces tests (elles sont lues en
production, et leur absence est ecrite dans le rapport).
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import veille_agentique as veille  # noqa: E402


def _maintenant() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


# --------------------------------------------------------------------------- #
# Fenetres
# --------------------------------------------------------------------------- #


def test_depuis_texte_comprend_heures_et_jours():
    assert (_maintenant() - veille.depuis_texte("24h")).total_seconds() == pytest.approx(86400, abs=5)
    assert (_maintenant() - veille.depuis_texte("7d")).total_seconds() == pytest.approx(7 * 86400, abs=5)
    with pytest.raises(ValueError):
        veille.depuis_texte("hier")


# --------------------------------------------------------------------------- #
# Journal de decouverte
# --------------------------------------------------------------------------- #


def test_analyser_journal_filtre_la_fenetre_et_compte_les_agents():
    moment = _maintenant()
    evenements = [
        {"ts": moment.isoformat(), "endpoint": "/validate", "user_agent": "Mozilla/5.0 (compatible; GPTBot/1.0)"},
        {"ts": moment.isoformat(), "endpoint": "/validate", "user_agent": "mako-pulse-prober/0.1"},
        {"ts": (moment - timedelta(days=2)).isoformat(), "endpoint": "/validate", "user_agent": "curl/8.0"},
    ]
    resultat = veille.analyser_journal(evenements, moment - timedelta(hours=24))
    assert resultat["refus_402_fenetre"] == 2
    assert resultat["endpoints"] == {"/validate": 2}
    assert resultat["agents_distincts"] == 2
    assert resultat["classes"], "les classes d'agents sont toujours renseignees"


def test_journal_decouverte_sans_chemin_est_une_mesure_absente(monkeypatch):
    class Reglages:
        discovery_log_path = ""

    monkeypatch.setattr(veille, "get_settings", lambda: Reglages())
    resultat = veille.journal_decouverte(_maintenant())
    assert resultat["disponible"] is False
    assert "DISCOVERY_LOG_PATH" in resultat["erreur"]


# --------------------------------------------------------------------------- #
# Journal d'acces Nginx
# --------------------------------------------------------------------------- #


def test_analyser_acces_compte_validate_et_mcp(tmp_path):
    moment = _maintenant()
    horodatage = moment.strftime("%d/%b/%Y:%H:%M:%S +0000")
    lignes = [
        f'1.2.3.4 - - [{horodatage}] "POST /validate HTTP/1.1" 402 147 "-" "curl/8.0"',
        f'1.2.3.4 - - [{horodatage}] "POST /mcp HTTP/1.1" 200 1234 "-" "agent/1.0"',
        f'1.2.3.4 - - [{horodatage}] "POST /mcp HTTP/1.1" 202 55 "-" "agent/1.0"',
        '9.9.9.9 - - [01/Jan/2020:00:00:00 +0000] "POST /validate HTTP/1.1" 402 1 "-" "vieux"',
    ]
    chemin = tmp_path / "access.log"
    chemin.write_text("\n".join(lignes) + "\n", encoding="utf-8")
    resultat = veille.analyser_acces(chemin, moment - timedelta(hours=1))
    assert resultat["disponible"] is True
    assert resultat["compteurs"]["/validate 402"] == 1
    assert resultat["compteurs"]["/mcp 200"] == 1
    assert resultat["compteurs"]["/mcp 202"] == 1


# --------------------------------------------------------------------------- #
# Alertes et etat
# --------------------------------------------------------------------------- #


def test_diff_etat_signale_paiement_release_classe_et_transfert():
    ancien = {
        "reels_cumul": 4,
        "revenu_cumul_usdc": 0.08,
        "dernier_paiement": "2026-10-19T21:32:19Z",
        "releases": {"coinbase/x402": {"version": "v1.0.0"}},
        "classes_agents": {"agent_ia": 10},
    }
    nouveau = {
        "reels_cumul": 5,
        "revenu_cumul_usdc": 0.10,
        "dernier_paiement": "2026-10-21T09:00:00Z",
        "releases": {"coinbase/x402": {"disponible": True, "version": "v1.1.0"}},
        "journal": {"classes": {"agent_ia": 10, "moteur": 2}},
        "onchain": {"nombre": 1, "montant_usdc": 0.02},
    }
    alertes = " | ".join(veille.diff_etat(ancien, nouveau))
    assert "nouveau paiement" in alertes
    assert "coinbase/x402" in alertes and "v1.1.0" in alertes
    assert "classe" in alertes
    assert "on-chain" in alertes


def test_diff_etat_sans_changement_ne_crie_pas():
    etat = {
        "reels_cumul": 4,
        "dernier_paiement": "2026-10-19T21:32:19Z",
        "releases": {"coinbase/x402": {"version": "v1"}},
        "classes_agents": {"agent_ia": 3},
        "onchain": {"nombre": 0},
    }
    nouveau = {
        "reels_cumul": 4,
        "dernier_paiement": "2026-10-19T21:32:19Z",
        "releases": {"coinbase/x402": {"disponible": True, "version": "v1"}},
        "journal": {"classes": {"agent_ia": 3}},
        "onchain": {"nombre": 0},
    }
    assert veille.diff_etat(etat, nouveau) == []


def test_sauver_et_puis_charger_l_etat(tmp_path):
    chemin = tmp_path / "etat.json"
    assert veille.charger_etat(chemin) == {}
    veille.sauver_etat(chemin, {"reels_cumul": 4, "classes_agents": {"agent_ia": 1}})
    assert veille.charger_etat(chemin)["reels_cumul"] == 4


def test_preparer_etat_ne_garde_que_les_releases_disponibles():
    donnees = {
        "genere_le": "2026-10-21T07:10:00+00:00",
        "revenu": {"reels_cumul": 4, "revenu_cumul_usdc": 0.08, "dernier_paiement": "2026-10-19T21:32:19Z"},
        "journal": {"classes": {"agent_ia": 3}, "refus_402_total": 1051},
        "ecosysteme": {
            "releases": {
                "coinbase/x402": {"disponible": True, "version": "v1"},
                "absent": {"disponible": False, "erreur": "HTTP 404"},
            }
        },
        "onchain": {"bloc_courant": 12345},
    }
    etat = veille.preparer_etat(donnees, {})
    assert etat["classes_agents"] == {"agent_ia": 3}
    assert "coinbase/x402" in etat["releases"]
    assert "absent" not in etat["releases"]
    assert etat["dernier_bloc_base"] == 12345


# --------------------------------------------------------------------------- #
# Rapport et email
# --------------------------------------------------------------------------- #


def test_formater_markdown_rend_les_sections_meme_si_sources_absentes():
    donnees = {
        "genere_le": "2026-10-21T07:10:00+00:00",
        "revenu": {"disponible": False, "erreur": "DatabaseError"},
        "journal": {"disponible": False, "erreur": "journal absent"},
        "acces": {"disponible": False, "erreur": "non demande"},
        "ecosysteme": {"releases": {}, "index": {"disponible": False, "erreur": "HTTP 429"}, "bazaar": {"disponible": False, "erreur": "HTTP 401"}},
        "onchain": {"disponible": False, "erreur": "RPC"},
        "alertes": [],
    }
    texte = veille.formater_markdown(donnees)
    assert "Veille agentique" in texte
    assert "registre indisponible" in texte
    assert "journal indisponible" in texte
    assert "aucun changement notable" in texte
    assert "SMTP" not in texte


def test_envoyer_email_sans_configuration_ne_ment_pas(monkeypatch):
    for variable in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM", "VEILLE_MAIL_TO", "CONTACT_EMAIL"):
        monkeypatch.delenv(variable, raising=False)
    envoye, detail = veille.envoyer_email("sujet", "corps")
    assert envoye is False
    assert "SMTP" in detail
