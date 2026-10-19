"""Tests de la purge du cache France/outre-mer (D79).

Le correctif BAN ne suffit pas : les résultats faux calculés avant D79 sont dans
Redis pour 30 jours (mesuré : « 8 rue madelaine, Lyon 75005 » y est encore avec
`validated: true`). Le script de purge ne doit toucher **que** les entrées
France/outre-mer, et ne rien supprimer sans `--apply`.
"""

import json
import sys
from pathlib import Path

from scripts.purger_cache_ban import entrees_francaises, purger


class FauxRedis:
    """Redis minimal : `scan_iter`, `get`, `delete`, et la trace des suppressions."""

    def __init__(self, entrees: dict[str, str]):
        self.donnees = dict(entrees)
        self.supprimees: list[str] = []

    def scan_iter(self, motif: str, count: int = 500):
        for cle in list(self.donnees):
            if cle.startswith("addr:"):
                yield cle

    def get(self, cle: str):
        return self.donnees.get(cle)

    def delete(self, *cles: str) -> int:
        for cle in cles:
            if cle in self.donnees:
                self.donnees.pop(cle)
                self.supprimees.append(cle)
        return len(cles)


def _valeur(pays: str, valide: bool = True) -> str:
    return json.dumps(
        {
            "validated": valide,
            "standardized": {"line1": "1 Rue", "city": "Ville", "zip": "00000", "country": pays},
        }
    )


def _redis_de_test() -> FauxRedis:
    return FauxRedis(
        {
            "addr:fr": _valeur("FR"),
            "addr:re": _valeur("RE"),
            "addr:us": _valeur("US"),
            "addr:de": _valeur("DE"),
            "addr:illisible": "pas du json",
            "visite:1": _valeur("FR"),  # clé hors `addr:*` : ignorée par le motif
        }
    )


def test_seules_les_entrees_france_et_outre_mer_sont_listees():
    trouvees = list(entrees_francaises(_redis_de_test()))

    assert {cle for cle, _valeur in trouvees} == {"addr:fr", "addr:re"}


def test_la_simulation_ne_supprime_rien():
    redis = _redis_de_test()

    trouvees = purger(redis, appliquer=False)

    assert len(trouvees) == 2
    assert redis.supprimees == []
    assert "addr:fr" in redis.donnees and "addr:re" in redis.donnees


def test_apply_supprime_la_france_et_laisse_le_reste():
    redis = _redis_de_test()

    purger(redis, appliquer=True)

    assert sorted(redis.supprimees) == ["addr:fr", "addr:re"]
    assert "addr:us" in redis.donnees and "addr:de" in redis.donnees
    assert "addr:illisible" in redis.donnees


def test_le_script_se_lance_comme_sur_le_vps():
    """D79 : `venv/bin/python scripts/purger_cache_ban.py` doit trouver `app`.

    C'est la commande réellement jouée sur le VPS après déploiement ; sans le
    `sys.path` ajouté, elle échouait en `ModuleNotFoundError` (mesuré le 17/10/2026).
    """
    import subprocess

    racine = Path(__file__).resolve().parents[1]
    resultat = subprocess.run(
        [sys.executable, "scripts/purger_cache_ban.py"],
        cwd=racine,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert "ModuleNotFoundError" not in resultat.stderr, resultat.stderr
    # Redis n'est pas joignable dans la suite -> code 2 (« cache injoignable »),
    # jamais un plantage d'import.
    assert resultat.returncode in (0, 2), resultat.stderr


def test_tous_pays_purge_toutes_les_adresses_et_rien_d_autre():
    """D84 : les résultats d'avant la correction sont purgés, quel que soit le pays."""
    redis = _redis_de_test()

    purger(redis, appliquer=True, tous_pays=True)

    assert set(redis.supprimees) == {"addr:fr", "addr:re", "addr:us", "addr:de"}
    assert "visite:1" in redis.donnees
