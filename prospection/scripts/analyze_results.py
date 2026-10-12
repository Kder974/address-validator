#!/usr/bin/env python
"""Analyse des performances de la prospection (Tâche 5.2).

Usage :
    python analyze_results.py
    python analyze_results.py --jours 30
    python analyze_results.py --sortie reports/analyse.html --ascii

Ce que fait le script :

1. croise les fiches et le journal d'événements pour compter, par secteur, par
   objet, par plateforme, par jour de semaine et par tranche horaire : envois,
   ouvertures, réponses, conversions, revenu ;
2. compare les objets deux à deux par un **test à deux proportions** (statistique
   z, seuil |z| > 1,96) : un écart de 1 % sur 12 envois n'est pas un résultat,
   c'est du hasard — le script refuse de conclure sans effectif ;
3. propose des ajustements explicites : quel objet garder, quel secteur
   requalifier, à quelle heure écrire ;
4. écrit un rapport HTML **autonome** (aucune ressource externe, aucun script :
   la page s'ouvre depuis un partage réseau sans réseau) et un résumé ASCII.

Le script ne modifie rien : c'est une lecture.

Codes de retour : 0 réussi, 1 base absente ou illisible, 2 usage invalide.
"""

from __future__ import annotations

import argparse
import html
import json
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))

import common as c  # noqa: E402

#: Jalons qui comptent comme un envoi (l'introduction et ses relances).
JALONS_ENVOI = ("intro", "relance_1", "relance_2")

#: Effectif minimal par variante ou par groupe pour qu'une comparaison soit
#: interprétable. En dessous, le script le dit au lieu de désigner un « gagnant ».
EFFECTIF_MIN_COMPARAISON = 20

#: Niveau de signification usuel (5 %) : |z| > 1,96.
SEUIL_Z = 1.96

JOURS_SEMAINE = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")


def charger(base, jours: int | None = None) -> tuple[list[dict], dict[int, list[dict]]]:
    """Lit les fiches et le journal d'événements, éventuellement borné en jours.

    Args:
        base: connexion SQLite.
        jours: ne garder que les événements des `jours` derniers jours.

    Returns:
        `(prospects, evenements)` où `evenements` est indexé par identifiant de
        prospect : le croisement se fait ensuite en mémoire, ce qui rend les
        agrégations lisibles et testables.
    """
    prospects = [dict(ligne) for ligne in base.execute("SELECT * FROM prospects").fetchall()]
    requete = "SELECT * FROM envois"
    parametres: list = []
    if jours:
        depuis = (
            datetime.now(timezone.utc) - timedelta(days=int(jours))
        ).replace(microsecond=0).isoformat()
        requete += " WHERE date_evenement >= ?"
        parametres.append(depuis)
    evenements: dict[int, list[dict]] = {}
    for ligne in base.execute(requete, parametres).fetchall():
        evenements.setdefault(int(ligne["prospect_id"]), []).append(dict(ligne))
    return prospects, evenements


def compter_jalons(evenements: list[dict]) -> dict:
    """Compte les jalons d'un prospect. Un envoi = un jour d'envoi distinct."""
    return {
        "envois": len(
            {
                str(evenement["date_evenement"])[:10]
                for evenement in evenements
                if evenement["type"] in JALONS_ENVOI
            }
        ),
        "ouvertures": sum(1 for evenement in evenements if evenement["type"] == "ouverture"),
        "reponses": sum(1 for evenement in evenements if evenement["type"] == "reponse"),
        "conversions": sum(1 for evenement in evenements if evenement["type"] == "conversion"),
        "refus": sum(1 for evenement in evenements if evenement["type"] == "refus"),
        "desinscriptions": sum(
            1 for evenement in evenements if evenement["type"] == "desinscription"
        ),
    }


def cumuler(
    prospects: list[dict], evenements: dict[int, list[dict]], cle: str
) -> dict[str, dict]:
    """Cumule les indicateurs par valeur d'un champ de la fiche.

    Args:
        prospects: fiches.
        evenements: journal indexé par identifiant de prospect.
        cle: champ de regroupement (`secteur`, `plateforme`, `objet_retenu`,
            `variante`).

    Returns:
        `{valeur: {"prospects", "envois", "ouvertures", "reponses", "conversions",
        "refus", "desinscriptions", "revenu"}}`.
    """
    resultat: dict[str, dict] = {}
    for prospect in prospects:
        brut = prospect.get(cle)
        if cle == "objet_retenu" and not brut:
            continue  # objet vide : la fiche n'a jamais été préparée, rien à comparer
        valeur = "(non renseigné)" if brut in (None, "") else str(brut)
        if cle == "variante":
            valeur = f"variante {valeur}"
        bloc = resultat.setdefault(
            valeur,
            {
                "prospects": 0,
                "envois": 0,
                "ouvertures": 0,
                "reponses": 0,
                "conversions": 0,
                "refus": 0,
                "desinscriptions": 0,
                "revenu": 0.0,
            },
        )
        bloc["prospects"] += 1
        bloc["revenu"] += float(prospect.get("revenu_usdc") or 0.0)
        for nom, nombre in compter_jalons(evenements.get(int(prospect["id"]), [])).items():
            bloc[nom] += nombre
    return resultat


def cumuler_par_temps(prospects: list[dict], cle: str) -> dict[str, dict]:
    """Cumule les envois par jour de semaine (`jour`) ou par tranche horaire (`heure`).

    L'heure d'envoi est celle de l'événement d'envoi enregistré : une observation,
    pas une intention. Quand elle manque, la fiche est ignorée — et cela se voit
    dans le total, ce qui vaut mieux qu'une valeur par défaut inventée.

    Returns:
        `{libellé: {"prospects": n, "reponses": n, "revenu": montant}}`.
    """
    resultat: dict[str, dict] = {}
    for prospect in prospects:
        horodatage = prospect.get("date_envoi")
        if not horodatage:
            continue
        try:
            moment = datetime.fromisoformat(str(horodatage).replace("Z", "+00:00"))
        except ValueError:
            continue
        if cle == "jour":
            etiquette = JOURS_SEMAINE[moment.weekday()]
        else:
            etiquette = f"{moment.hour:02d}h"
        bloc = resultat.setdefault(etiquette, {"prospects": 0, "reponses": 0, "revenu": 0.0})
        bloc["prospects"] += 1
        bloc["revenu"] += float(prospect.get("revenu_usdc") or 0.0)
        if str(prospect.get("date_reponse") or ""):
            bloc["reponses"] += 1
    return resultat


def z_deux_proportions(reussites1: int, total1: int, reussites2: int, total2: int) -> float:
    """Statistique z comparant deux taux de réussite (test à deux proportions).

    Args:
        reussites1: succès du premier groupe (par exemple les réponses).
        total1: envois du premier groupe.
        reussites2: succès du second groupe.
        total2: envois du second groupe.

    Returns:
        `z`. `|z| > 1,96` correspond au seuil usuel de 5 %. Renvoie `0.0` quand un
        effectif est nul ou qu'aucune variance n'est estimable : sans données, il
        n'y a pas de différence — et surtout pas une différence démontrée.
    """
    if not total1 or not total2:
        return 0.0
    proportion = (reussites1 + reussites2) / (total1 + total2)
    if proportion <= 0.0 or proportion >= 1.0:
        return 0.0
    erreur = math.sqrt(proportion * (1 - proportion) * (1 / total1 + 1 / total2))
    if not erreur:
        return 0.0
    return (reussites1 / total1 - reussites2 / total2) / erreur


def analyser(base, jours: int | None = None) -> dict:
    """Assemble toutes les agrégations en une structure unique et lisible.

    Returns:
        `{"jours", "totaux", "taux", "par_secteur", "par_objet", "par_variante",
        "par_plateforme", "par_jour", "par_heure"}`.
    """
    prospects, evenements = charger(base, jours)
    totaux = {
        "prospects": 0,
        "envois": 0,
        "ouvertures": 0,
        "reponses": 0,
        "conversions": 0,
        "refus": 0,
        "desinscriptions": 0,
        "revenu": 0.0,
    }
    for prospect in prospects:
        totaux["prospects"] += 1
        totaux["revenu"] += float(prospect.get("revenu_usdc") or 0.0)
        for nom, nombre in compter_jalons(evenements.get(int(prospect["id"]), [])).items():
            totaux[nom] += nombre
    return {
        "jours": jours,
        "totaux": totaux,
        "taux": {
            "ouverture": c.pourcent(totaux["ouvertures"], totaux["envois"]),
            "reponse": c.pourcent(totaux["reponses"], totaux["envois"]),
            "conversion": c.pourcent(totaux["conversions"], totaux["envois"]),
            "desinscription": c.pourcent(totaux["desinscriptions"], totaux["envois"]),
        },
        "par_secteur": cumuler(prospects, evenements, "secteur"),
        "par_objet": cumuler(prospects, evenements, "objet_retenu"),
        "par_variante": cumuler(prospects, evenements, "variante"),
        "par_plateforme": cumuler(prospects, evenements, "plateforme"),
        "par_jour": cumuler_par_temps(prospects, "jour"),
        "par_heure": cumuler_par_temps(prospects, "heure"),
    }


def recommandations(agr: dict) -> list[str]:
    """Déduit des actions du croisement mesuré — et dit quand il n'y a rien à en déduire.

    Chaque conseil s'appuie sur un effectif explicite. Une analyse qui désigne un
    « meilleur objet » sur huit envois fait changer de gabarit pour rien : le
    script préfère écrire qu'il ne sait pas encore.
    """
    conseils: list[str] = []
    totaux = agr["totaux"]

    objets = sorted(
        agr["par_objet"].items(), key=lambda item: (-item[1]["reponses"], -item[1]["envois"])
    )
    if len(objets) >= 2:
        (nom1, bloc1), (nom2, bloc2) = objets[0], objets[1]
        if min(bloc1["envois"], bloc2["envois"]) >= EFFECTIF_MIN_COMPARAISON:
            z = z_deux_proportions(
                bloc1["reponses"], bloc1["envois"], bloc2["reponses"], bloc2["envois"]
            )
            if abs(z) >= SEUIL_Z:
                conseils.append(
                    f"Objet à privilégier : « {nom1} » ({bloc1['reponses']}/{bloc1['envois']} "
                    f"réponses), écart significatif avec « {nom2} » (z={z:+.2f}, seuil 1,96)."
                )
                conseils.append(f"Objet à retirer : « {nom2} ».")
            else:
                conseils.append(
                    f"Aucun objet ne se détache (z={z:+.2f} < 1,96) : garder les variantes "
                    f"et attendre {EFFECTIF_MIN_COMPARAISON} envois par objet."
                )
        else:
            conseils.append(
                f"A/B en cours : {bloc1['envois']} et {bloc2['envois']} envois pour les deux "
                f"meilleurs objets, il en faut {EFFECTIF_MIN_COMPARAISON} par variante."
            )

    secteurs = {nom: bloc for nom, bloc in agr["par_secteur"].items() if bloc["envois"]}
    if secteurs:
        nom_meilleur, bloc_meilleur = max(
            secteurs.items(), key=lambda item: item[1]["reponses"] / item[1]["envois"]
        )
        conseils.append(
            f"Secteur le plus réactif : {nom_meilleur} "
            f"({bloc_meilleur['reponses']}/{bloc_meilleur['envois']} réponses, "
            f"{c.pourcent(bloc_meilleur['reponses'], bloc_meilleur['envois'])})."
        )
        for nom, bloc in secteurs.items():
            if not bloc["reponses"] and bloc["envois"] >= EFFECTIF_MIN_COMPARAISON:
                conseils.append(
                    f"Secteur {nom} : {bloc['envois']} envois sans réponse — requalifier le "
                    "ciblage ou changer d'argument avant d'en envoyer d'autres."
                )

    jours_actifs = {nom: bloc for nom, bloc in agr["par_jour"].items() if bloc["prospects"]}
    if jours_actifs:
        nom_jour, bloc_jour = max(
            jours_actifs.items(), key=lambda item: item[1]["reponses"] / item[1]["prospects"]
        )
        if bloc_jour["prospects"] >= EFFECTIF_MIN_COMPARAISON:
            conseils.append(
                f"Jour le plus réactif : {nom_jour} "
                f"({bloc_jour['reponses']}/{bloc_jour['prospects']} réponses)."
            )
        else:
            conseils.append(
                "Répartition par jour trop faible pour conclure (le mieux placé : "
                f"{nom_jour}, {bloc_jour['prospects']} envois)."
            )

    if not totaux["envois"]:
        conseils.append(
            "Aucun envoi consigné : générer une campagne (generate_emails.py), l'envoyer, "
            "puis importer l'export de l'outil d'envoi (track_responses.py)."
        )
        return conseils

    if totaux["ouvertures"] / totaux["envois"] < 0.20:
        conseils.append(
            "Taux d'ouverture sous 20 % : soigner la délivrabilité (SPF/DKIM/DMARC, volume "
            "quotidien, objets) avant de retoucher le corps des emails."
        )
    if totaux["desinscriptions"] * 100 > totaux["envois"]:
        conseils.append(
            f"Plus de 1 % de désinscriptions ({totaux['desinscriptions']} pour "
            f"{totaux['envois']} envois) : interrompre et revoir le ciblage."
        )
    if totaux["conversions"]:
        conseils.append(
            f"{totaux['conversions']} conversion(s) pour {totaux['revenu']:.2f} USDC "
            "déclarés : chiffrer face au coût d'un échec de livraison évité (17 à 30 $) "
            "dans le message suivant."
        )
    return conseils


#: Feuille de style embarquée, sur le modèle de `/dashboard` (D32) : aucune
#: ressource externe. Le rapport doit s'ouvrir depuis un partage réseau, hors
#: ligne, sans envoyer une requête à un tiers.
STYLE = """
  :root { color-scheme: dark; }
  body { margin: 24px; background: #0f1115; color: #e6e9ef;
         font-family: system-ui, "Segoe UI", Roboto, sans-serif; }
  h1 { font-size: 20px; margin: 0 0 4px; }
  h2 { font-size: 15px; margin: 22px 0 6px; }
  .muted { color: #9aa4b2; font-size: 12px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
          gap: 10px; margin-top: 14px; }
  .card { background: #171a21; border: 1px solid #242833; border-radius: 8px;
          padding: 10px 12px; }
  .card .k { font-size: 11px; text-transform: uppercase; letter-spacing: .04em;
             color: #9aa4b2; }
  .card .v { font-size: 20px; font-weight: 600; }
  table { width: 100%; border-collapse: collapse; margin-top: 8px; }
  th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid #242833;
           font-size: 13px; }
  th { font-size: 11px; text-transform: uppercase; color: #9aa4b2; }
  td.num, th.num { text-align: right; }
  .track { display: inline-block; width: 110px; height: 10px; background: #242833;
           border-radius: 5px; vertical-align: middle; }
  .fill { display: block; height: 10px; border-radius: 5px; background: #4f8cff; }
  ul { margin: 6px 0 0 18px; }
  li { margin: 4px 0; }
  footer { margin-top: 26px; }
"""


def _barre_html(valeur: float, maximum: float) -> str:
    """Barre proportionnelle en CSS pur (ni image, ni script, ni police distante)."""
    largeur = 0 if maximum <= 0 else max(2, round(100 * float(valeur) / float(maximum)))
    return (
        f'<span class="track"><span class="fill" style="width:{largeur}%"></span></span>'
    )


def _tableau_html(
    titre: str, groupe: dict[str, dict], colonnes: Sequence[tuple[str, str]]
) -> str:
    """Rend un groupe d'indicateurs en tableau HTML.

    Args:
        titre: titre de section.
        groupe: `{valeur: indicateurs}`.
        colonnes: `(clé, en-tête)` à afficher en plus, dans l'ordre.

    Returns:
        Le fragment HTML, ou une chaîne vide quand le groupe est vide.
    """
    if not groupe:
        return ""
    lignes = sorted(groupe.items(), key=lambda item: -item[1].get("envois", 0))
    maximum = max(bloc.get("envois", 0) for _, bloc in lignes) or 1
    pieces = [f"<h2>{html.escape(titre)}</h2>", "<table><thead><tr><th>Valeur</th>"]
    pieces += [f'<th class="num">{html.escape(entete)}</th>' for _, entete in colonnes]
    pieces.append("<th>Volume</th></tr></thead><tbody>")
    for nom, bloc in lignes:
        pieces.append("<tr>")
        pieces.append(f"<td>{html.escape(str(nom))}</td>")
        for cle, _ in colonnes:
            valeur = bloc.get(cle, 0)
            texte = f"{valeur:.2f}" if isinstance(valeur, float) else str(valeur)
            pieces.append(f'<td class="num">{html.escape(texte)}</td>')
        pieces.append(f'<td>{_barre_html(bloc.get("envois", 0), maximum)}</td>')
        pieces.append("</tr>")
    pieces.append("</tbody></table>")
    return "\n".join(pieces)


def _tableau_temps_html(titre: str, groupe: dict[str, dict]) -> str:
    """Rend `par_jour` ou `par_heure` : envois, réponses, taux de réponse."""
    if not groupe:
        return ""
    pieces = [
        f"<h2>{html.escape(titre)}</h2>",
        "<table><thead><tr><th>Période</th><th class=\"num\">Envois</th>"
        "<th class=\"num\">Réponses</th><th class=\"num\">Taux</th></tr></thead><tbody>",
    ]
    for libelle, bloc in sorted(groupe.items()):
        pieces.append(
            f"<tr><td>{html.escape(libelle)}</td>"
            f'<td class="num">{bloc["prospects"]}</td>'
            f'<td class="num">{bloc["reponses"]}</td>'
            f'<td class="num">{c.pourcent(bloc["reponses"], bloc["prospects"])}</td></tr>'
        )
    pieces.append("</tbody></table>")
    return "\n".join(pieces)


def rapport_html(agr: dict, conseils: list[str], sortie: str | None = None) -> Path:
    """Écrit le rapport visuel autonome dans `reports/`.

    Returns:
        Le chemin du fichier écrit.
    """
    chemin = (
        Path(sortie)
        if sortie
        else c.DOSSIER_RAPPORTS / f"analyse_{c.horodatage_jour()}.html"
    )
    chemin.parent.mkdir(parents=True, exist_ok=True)
    totaux = agr["totaux"]
    fenetre = (
        f"{agr['jours']} derniers jours" if agr["jours"] else "depuis le début (aucune borne)"
    )
    cartes = [
        ("Prospects", str(totaux["prospects"])),
        ("Envois", str(totaux["envois"])),
        ("Ouvertures", str(totaux["ouvertures"])),
        ("Réponses", str(totaux["reponses"])),
        ("Conversions", str(totaux["conversions"])),
        ("Revenu USDC", f"{totaux['revenu']:.2f}"),
        ("Taux d'ouverture", agr["taux"]["ouverture"]),
        ("Taux de réponse", agr["taux"]["reponse"]),
        ("Taux de conversion", agr["taux"]["conversion"]),
        ("Taux de désinscription", agr["taux"]["desinscription"]),
    ]
    pieces = [
        "<!DOCTYPE html>",
        '<html lang="fr">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        "<title>Analyse de la prospection — Address Validator</title>",
        f"<style>{STYLE}</style>",
        "</head>",
        "<body>",
        "<h1>Analyse de la prospection — Address Validator</h1>",
        f'<p class="muted">Généré le {html.escape(c.maintenant())} · fenêtre : '
        f"{html.escape(fenetre)}</p>",
        '<div class="grid">',
    ]
    for etiquette, valeur in cartes:
        pieces.append(
            f'<div class="card"><div class="k">{html.escape(etiquette)}</div>'
            f'<div class="v">{html.escape(valeur)}</div></div>'
        )
    pieces.append("</div>")
    pieces.append(
        _tableau_html(
            "Par secteur",
            agr["par_secteur"],
            (("prospects", "Prospects"), ("reponses", "Réponses"), ("conversions", "Conv.")),
        )
    )
    pieces.append(
        _tableau_html(
            "Par objet (A/B testing)",
            agr["par_objet"],
            (
                ("envois", "Envois"),
                ("ouvertures", "Ouvert."),
                ("reponses", "Réponses"),
                ("conversions", "Conv."),
            ),
        )
    )
    pieces.append(
        _tableau_html(
            "Par variante d'objet",
            agr["par_variante"],
            (("envois", "Envois"), ("reponses", "Réponses"), ("conversions", "Conv.")),
        )
    )
    pieces.append(
        _tableau_html(
            "Par plateforme",
            agr["par_plateforme"],
            (("prospects", "Prospects"), ("reponses", "Réponses")),
        )
    )
    pieces.append(_tableau_temps_html("Par jour de semaine (envoi)", agr["par_jour"]))
    pieces.append(_tableau_temps_html("Par heure d'envoi", agr["par_heure"]))
    pieces.append("<h2>Recommandations</h2>")
    if conseils:
        pieces.append("<ul>")
        pieces += [f"<li>{html.escape(conseil)}</li>" for conseil in conseils]
        pieces.append("</ul>")
    else:
        pieces.append('<p class="muted">Aucune recommandation : données insuffisantes.</p>')
    pieces.append(
        '<footer class="muted">Page autonome : aucune ressource externe, aucun script. '
        "Source : <code>prospection/data/prospects.db</code> — générée par "
        "<code>scripts/analyze_results.py</code>.</footer>"
    )
    pieces += ["</body>", "</html>", ""]
    chemin.write_text("\n".join(pieces), encoding="utf-8")
    return chemin


def afficher_ascii(agr: dict, conseils: list[str]) -> None:
    """Résumé lisible dans un terminal (le HTML est pour le navigateur)."""
    totaux = agr["totaux"]
    print(
        c.tableau(
            ["indicateur", "valeur"],
            [
                ("prospects", totaux["prospects"]),
                ("envois", totaux["envois"]),
                ("ouvertures", totaux["ouvertures"]),
                ("réponses", totaux["reponses"]),
                ("conversions", totaux["conversions"]),
                ("revenu (USDC)", f"{totaux['revenu']:.2f}"),
                ("taux d'ouverture", agr["taux"]["ouverture"]),
                ("taux de réponse", agr["taux"]["reponse"]),
                ("taux de conversion", agr["taux"]["conversion"]),
            ],
        )
    )
    sections = (
        ("Par secteur", agr["par_secteur"]),
        ("Par objet", agr["par_objet"]),
        ("Par variante", agr["par_variante"]),
        ("Par plateforme", agr["par_plateforme"]),
    )
    for titre_section, groupe in sections:
        blocs = [(nom, bloc) for nom, bloc in groupe.items() if bloc.get("envois")]
        if not blocs:
            continue
        maximum = max(bloc["envois"] for _, bloc in blocs)
        print(f"\n{titre_section} :")
        for nom, bloc in sorted(blocs, key=lambda item: -item[1]["envois"]):
            etiquette = nom if len(nom) <= 36 else nom[:33] + "..."
            print(
                f"  {etiquette:<38} {c.barre(bloc['envois'], maximum)} "
                f"{bloc['envois']:>3} envois · {bloc['reponses']} rép. · "
                f"{bloc['conversions']} conv."
            )
    if agr["par_jour"]:
        print("\nPar jour d'envoi :")
        for libelle, bloc in sorted(agr["par_jour"].items()):
            print(
                f"  {libelle:<10} {bloc['prospects']:>3} envois · {bloc['reponses']} rép."
            )
    print("\nRecommandations :")
    for conseil in conseils:
        print(f"  * {conseil}")


def construire_arguments() -> argparse.ArgumentParser:
    """Ligne de commande de l'analyse (lecture seule)."""
    analyseur = argparse.ArgumentParser(
        description="Analyse les performances de la prospection (lecture seule).",
        epilog="Exemple : python analyze_results.py --jours 30 --ascii",
    )
    analyseur.add_argument(
        "--jours", type=int, help="ne considérer que les N derniers jours d'événements"
    )
    analyseur.add_argument("--db", help=f"base SQLite (défaut : {c.CHEMIN_DB})")
    analyseur.add_argument(
        "--sortie", help="rapport HTML (défaut : reports/analyse_<date>.html)"
    )
    analyseur.add_argument("--json", dest="json_sortie", help="écrit aussi l'analyse brute en JSON")
    analyseur.add_argument(
        "--ascii", action="store_true", help="n'écrit aucun HTML : résumé terminal seulement"
    )
    return analyseur


def main(argv: Sequence[str] | None = None) -> int:
    """Point d'entrée : 0 réussi, 1 base absente ou vide, 2 usage invalide."""
    args = construire_arguments().parse_args(argv)
    c.securiser_sortie()
    c.titre("Analyse de la prospection — Address Validator")

    try:
        base = c.connexion(args.db, creer=False)
    except FileNotFoundError as erreur:
        print(f"ANALYSE FAIL {erreur}")
        return 1

    try:
        agregats = analyser(base, args.jours)
        conseils = recommandations(agregats)
        afficher_ascii(agregats, conseils)
        if args.json_sortie:
            chemin_json = Path(args.json_sortie)
            chemin_json.parent.mkdir(parents=True, exist_ok=True)
            chemin_json.write_text(
                json.dumps(
                    {"agregats": agregats, "recommandations": conseils},
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            c.resultat(f"JSON écrit : {chemin_json}")
        if args.ascii:
            c.alerte("mode --ascii : aucun rapport HTML écrit.")
        else:
            chemin = rapport_html(agregats, conseils, args.sortie)
            c.resultat(f"rapport HTML écrit : {chemin}")
    finally:
        base.close()

    return 0 if agregats["totaux"]["prospects"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
