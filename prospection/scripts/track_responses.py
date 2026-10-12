#!/usr/bin/env python
"""Suivi des réponses, des refus et des conversions (Tâche 5.1).

Usage :
    python track_responses.py --input data/reponses_instantly.csv
    python track_responses.py --marquer contact@ma-boutique-montres.fr:repondu
    python track_responses.py --marquer hello@techgadget.fr:converti --revenu 0.50
    python track_responses.py --synthese
    python track_responses.py --purger-avant 2023-10-10 --confirmer   # conservation RGPD

Ce que fait le script :

1. lit un export d'outil d'envoi (Instantly, Lemlist, Mailshake, export maison)
   et traduit son vocabulaire (`sent`, `opened`, `replied`, `bounced`…) dans les
   statuts du CRM ;
2. fait **progresser** les fiches sans jamais reculer : un prospect `repondu` ne
   redevient pas `envoye` parce qu'un export plus ancien est importé après coup ;
3. horodate les étapes (envoi, ouverture, réponse) et consigne chaque fait dans
   `envois` — le journal n'est jamais réécrit ;
4. convertit `bounced` en refus définitif, et `unsubscribed` en désinscription :
   ces deux-là ne sont plus jamais démarchés ;
5. calcule les taux — ouverture, réponse, conversion — et écrit un rapport
   quotidien en markdown dans `reports/` ;
6. applique la durée de conservation annoncée (`--purger-avant AAAA-MM-JJ`), avec
   le même garde-fou que l'envoi : sans `--confirmer`, rien n'est supprimé.

Rien n'est envoyé, rien n'est téléchargé : ce script lit des fichiers.

Codes de retour : 0 réussi, 1 aucun suivi appliqué (fichier illisible ou aucun
email reconnu), 2 usage invalide.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))

import common as c  # noqa: E402

#: Vocabulaire des outils d'envoi → statut du CRM. Les clés sont normalisées
#: (minuscules, sans accent) avant recherche : `Not interested` et
#: `not_interested` tombent sur la même entrée.
CORRESPONDANCES = {
    # envoi
    "sent": "envoye",
    "envoye": "envoye",
    "email sent": "envoye",
    "delivered": "envoye",
    "livre": "envoye",
    "contacted": "envoye",
    "sequence started": "envoye",
    # ouverture et clics (un clic prouve une lecture ; on ne le compte pas plus)
    "opened": "ouvert",
    "open": "ouvert",
    "ouvert": "ouvert",
    "clicked": "ouvert",
    "click": "ouvert",
    "clic": "ouvert",
    "read": "ouvert",
    # réponses
    "replied": "repondu",
    "reply": "repondu",
    "repondu": "repondu",
    "interested": "repondu",
    "reponse": "repondu",
    "answer": "repondu",
    # conversion
    "converted": "converti",
    "converti": "converti",
    "client": "converti",
    "closed won": "converti",
    "won": "converti",
    "payant": "converti",
    # refus
    "refused": "refuse",
    "refuse": "refuse",
    "not interested": "refuse",
    "declined": "refuse",
    "unqualified": "refuse",
    "perdu": "refuse",
    # rebonds : l'adresse n'existe pas ou plus -> plus jamais contactée
    "bounced": "refuse",
    "bounce": "refuse",
    "hard bounce": "refuse",
    "undeliverable": "refuse",
    "invalide": "refuse",
    # désinscription
    "unsubscribed": "desinscrit",
    "unsubscribe": "desinscrit",
    "unsub": "desinscrit",
    "optout": "desinscrit",
    "opt out": "desinscrit",
    "desinscrit": "desinscrit",
    "stop": "desinscrit",
    "spam": "desinscrit",
}

#: Ordre de progression d'un prospect : un export ne fait jamais reculer un
#: statut. `refuse` et `desinscrit` sont hors de cette échelle : ils sont
#: définitifs, et l'emportent toujours.
ORDRE_STATUTS = ("nouveau", "envoye", "ouvert", "repondu", "converti")

#: Statut → fait journalisé dans `envois`.
EVENEMENT_POUR = {
    "envoye": "intro",
    "ouvert": "ouverture",
    "repondu": "reponse",
    "converti": "conversion",
    "refuse": "refus",
    "desinscrit": "desinscription",
}


def normaliser_statut(valeur: str) -> str:
    """Traduit le vocabulaire d'un outil d'envoi en statut du CRM.

    Args:
        valeur: texte brut de l'export (`Sent`, `NOT_INTERESTED`, `bounced`…).

    Returns:
        Un statut de `common.STATUTS`, ou `""` si le texte n'est pas reconnu
        (l'appelant décide alors de l'ignorer **en le comptant**, jamais en
        devinant).
    """
    cle = c.normaliser(str(valeur or "")).replace("_", " ").replace("-", " ").strip()
    if not cle:
        return ""
    if cle in CORRESPONDANCES:
        return CORRESPONDANCES[cle]
    if cle in c.STATUTS:
        return cle
    return ""


def statut_cible(actuel: str, propose: str) -> str:
    """Statut à retenir après un nouvel événement.

    Règles, dans l'ordre :

    1. un refus ou une désinscription s'applique toujours (un « stop » vaut
       définitivement, même si l'export dit « sent ») ;
    2. un statut déjà terminal ne change plus ;
    3. sinon, on ne garde que la progression : `ouvert` ne redevient pas
       `envoye`. Sans cette règle, importer un vieil export après un récent
       effacerait les réponses déjà obtenues.

    Returns:
        Le statut à écrire (identique à `actuel` si rien ne doit changer).
    """
    if propose in ("refuse", "desinscrit"):
        return propose
    if actuel in ("refuse", "desinscrit"):
        return actuel
    if propose == "converti":
        return "converti"
    if actuel == "converti":
        return actuel
    rang_actuel = ORDRE_STATUTS.index(actuel) if actuel in ORDRE_STATUTS else 0
    rang_propose = ORDRE_STATUTS.index(propose) if propose in ORDRE_STATUTS else 0
    return propose if rang_propose > rang_actuel else actuel


#: Colonnes reconnues dans un export d'outil d'envoi (le script essaie dans
#: l'ordre, en s'arrêtant à la première remplie). Les noms varient d'un outil à
#: l'autre : plutôt que d'imposer un format, on lit ceux qui existent.
COLONNES_STATUT = ("statut", "status", "state", "event", "evenement", "type")
COLONNES_DATE = (
    "date", "date_evenement", "timestamp", "time", "sent_at", "opened_at",
    "replied_at", "date_envoi",
)
COLONNES_REVENU = ("revenu", "revenue", "montant", "amount")

#: Colonnes obligatoires d'un export : seule l'adresse l'est. Un export sans
#: adresse n'est pas exploitable ; un export sans statut sera compté et signalé.
COLONNES_EXPORT = ("email",)


def normaliser_date(valeur: str) -> str:
    """Ramène une date d'export au format ISO 8601 UTC ; vide si illisible.

    Formats acceptés : ISO (`2026-10-10`, `2026-10-10T09:00:00Z`) et formats
    français ou à séparateurs inversés (`10/10/2026`, `10-10-2026 09:00`). Une
    date illisible n'est pas inventée : l'événement est alors horodaté au moment
    de l'import, ce qui est visible dans le rapport.
    """
    texte = str(valeur or "").strip().replace("Z", "+00:00")
    if not texte:
        return ""
    for modele in (
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
        "%d/%m/%Y %H:%M",
        "%d/%m/%Y",
        "%d-%m-%Y",
    ):
        try:
            moment = datetime.strptime(texte, modele)
        except ValueError:
            continue
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return moment.replace(microsecond=0).isoformat()
    return ""


def statut_de_ligne(ligne: dict) -> tuple[str, str, float, str]:
    """Extrait d'une ligne d'export ce qui est exploitable.

    Returns:
        `(statut, date_iso, revenu, valeur_brute)`. `statut` est vide quand le
        vocabulaire n'est pas reconnu : la ligne sera signalée, pas interprétée.
        Le revenu est lu comme une **valeur déclarée** (pas un cumul) : le
        réimporter ne le double jamais.
    """
    brut = ""
    for colonne in COLONNES_STATUT:
        if ligne.get(colonne):
            brut = str(ligne[colonne])
            break
    raw_date = ""
    for colonne in COLONNES_DATE:
        if ligne.get(colonne):
            raw_date = str(ligne[colonne])
            break
    revenu = 0.0
    for colonne in COLONNES_REVENU:
        valeur = str(ligne.get(colonne) or "").replace(",", ".")
        for symbole in ("usdc", "$", "€", "usd", " "):
            valeur = valeur.replace(symbole, "")
        if valeur:
            try:
                revenu = float(valeur)
            except ValueError:
                revenu = 0.0
            break
    return normaliser_statut(brut), normaliser_date(raw_date), revenu, brut


def type_envoi(base, prospect_id: int) -> str:
    """Rang d'un envoi : `intro`, puis `relance_1`, `relance_2`.

    Le rang se lit dans le journal (nombre de jours d'envoi distincts déjà
    consignés), pas dans une colonne : relancer se constate, ça ne se déclare pas.
    """
    lignes = base.execute(
        "SELECT DISTINCT substr(date_evenement, 1, 10) AS jour FROM envois"
        " WHERE prospect_id = ? AND type IN ('intro','relance_1','relance_2')",
        (int(prospect_id),),
    ).fetchall()
    jours = len({ligne["jour"] for ligne in lignes})
    if jours == 0:
        return "intro"
    return "relance_1" if jours == 1 else "relance_2"


def appliquer(
    base,
    lignes: list[dict],
    etape_force: str | None = None,
    date_force: str | None = None,
    revenu_force: float | None = None,
    dry_run: bool = False,
    canal: str = "email",
) -> dict:
    """Applique un export d'événements au CRM, sans jamais reculer un statut.

    Args:
        base: connexion SQLite.
        lignes: lignes lues par `common.lire_csv`.
        etape_force: type d'événement imposé (un seul export peut alors porter
            une relance explicite plutôt qu'un « envoyé » ambigu).
        date_force: date à utiliser quand l'export n'en porte pas.
        revenu_force: montant déclaré pour une conversion.
        dry_run: n'écrit ni statut, ni événement.
        canal: `email` ou `linkedin` (les deux sont suivis dans le même journal).

    Returns:
        Rapport : `lues`, `appliquees`, `inchangees`, `sans_date`, `inconnues`
        (adresses hors base), `non_reconnues` (vocabulaire inconnu),
        `transitions` (`nouveau->envoye`), `revenu_declare`.
    """
    rapport: dict = {
        "lues": 0,
        "appliquees": 0,
        "inchangees": 0,
        "sans_date": 0,
        "inconnues": [],
        "non_reconnues": [],
        "transitions": {},
        "revenu_declare": 0.0,
    }
    for ligne in lignes:
        rapport["lues"] += 1
        email = str(ligne.get("email") or "").strip()
        statut, date_iso, revenu, brut = statut_de_ligne(ligne)
        if not statut:
            rapport["non_reconnues"].append(
                {"ligne": ligne.get("_ligne"), "email": email, "valeur": brut}
            )
            continue
        fiche = c.prospect_par_email(base, email)
        if fiche is None:
            # Une réponse d'une adresse inconnue est **signalée**, jamais importée
            # d'office : sans nom de boutique, la fiche serait inutilisable.
            rapport["inconnues"].append({"ligne": ligne.get("_ligne"), "email": email})
            continue

        quand = date_force or date_iso or c.maintenant()
        if not (date_force or date_iso):
            rapport["sans_date"] += 1
        prospect_id = int(fiche["id"])
        actuel = str(fiche["statut"])
        cible = statut_cible(actuel, statut)
        changement = cible != actuel

        champs: dict = {}
        if changement:
            champs["statut"] = cible
            cle = f"{actuel}->{cible}"
            rapport["transitions"][cle] = rapport["transitions"].get(cle, 0) + 1
        if statut == "envoye" and not fiche["date_envoi"]:
            champs["date_envoi"] = quand
        if statut in ("ouvert", "repondu", "converti") and not fiche["date_ouverture"]:
            champs["date_ouverture"] = quand
        if statut in ("repondu", "converti") and not fiche["date_reponse"]:
            champs["date_reponse"] = quand

        montant = float(revenu_force) if revenu_force is not None else float(revenu or 0.0)
        if statut == "converti" and montant > float(fiche["revenu_usdc"] or 0.0):
            # Revenu **déclaré**, pas cumulé : réimporter le même export ne
            # double pas le chiffre d'affaires du CRM.
            champs["revenu_usdc"] = montant
            rapport["revenu_declare"] += montant

        if statut in ("refuse", "desinscrit") and brut:
            marque = f"motif:{c.normaliser(brut)}"
            if marque not in str(fiche["notes"]):
                champs["notes"] = (str(fiche["notes"]) + "; " + marque).strip("; ")

        if not dry_run:
            if champs:
                c.maj_prospect(base, prospect_id, **champs)
            type_evenement = etape_force or (
                type_envoi(base, prospect_id) if statut == "envoye" else EVENEMENT_POUR[statut]
            )
            # Un fait n'est consigné qu'une fois par type et par jour : réimporter
            # le même export ne gonfle pas le journal.
            deja = base.execute(
                "SELECT 1 FROM envois WHERE prospect_id = ? AND type = ?"
                " AND substr(date_evenement, 1, 10) = ? LIMIT 1",
                (prospect_id, type_evenement, quand[:10]),
            ).fetchone()
            if changement or deja is None:
                c.enregistrer_evenement(
                    base,
                    prospect_id,
                    type_evenement,
                    objet=str(fiche["objet_retenu"] or ""),
                    variante=fiche["variante"],
                    canal=canal,
                    detail=brut,
                    quand=quand,
                )
        if changement:
            rapport["appliquees"] += 1
        else:
            rapport["inchangees"] += 1
    return rapport


def synthese(base) -> dict:
    """Compte les statuts, les jalons datés et les taux de la campagne.

    Les taux sont calculés sur la table `envois` (des faits datés), jamais sur
    des colonnes de confort : ainsi un export mal mappé ne peut pas embellir un
    taux de réponse.

    Returns:
        `prospects`, `statuts`, `jalons`, `envois`, `taux` (ouverture, réponse,
        conversion sur réponses, conversion sur envois), `revenu_usdc`,
        `par_secteur` (`secteur`, `prospects`, `envoyes`, `revenus`).
    """
    statuts = {
        ligne["statut"]: ligne["nb"]
        for ligne in base.execute("SELECT statut, COUNT(*) AS nb FROM prospects GROUP BY statut")
    }
    jalons = {
        ligne["type"]: ligne["nb"]
        for ligne in base.execute("SELECT type, COUNT(*) AS nb FROM envois GROUP BY type")
    }
    revenu = float(
        base.execute("SELECT COALESCE(SUM(revenu_usdc), 0) AS total FROM prospects").fetchone()[
            "total"
        ]
    )
    envois = sum(jalons.get(nom, 0) for nom in ("intro", "relance_1", "relance_2"))
    ouvertures = jalons.get("ouverture", 0)
    reponses = jalons.get("reponse", 0)
    conversions = jalons.get("conversion", 0)
    par_secteur = [
        {
            "secteur": ligne["secteur"] or "(non qualifié)",
            "prospects": ligne["nb"],
            "envoyes": ligne["envoyes"] or 0,
            "revenus": float(ligne["revenus"] or 0.0),
        }
        for ligne in base.execute(
            "SELECT secteur, COUNT(*) AS nb,"
            " SUM(CASE WHEN date_envoi IS NOT NULL THEN 1 ELSE 0 END) AS envoyes,"
            " SUM(revenu_usdc) AS revenus"
            " FROM prospects GROUP BY secteur ORDER BY nb DESC"
        )
    ]
    return {
        "prospects": sum(statuts.values()),
        "statuts": statuts,
        "jalons": jalons,
        "envois": envois,
        "taux": {
            "ouverture": c.pourcent(ouvertures, envois),
            "reponse": c.pourcent(reponses, envois),
            "conversion_sur_reponses": c.pourcent(conversions, reponses),
            "conversion_sur_envois": c.pourcent(conversions, envois),
        },
        "revenu_usdc": revenu,
        "par_secteur": par_secteur,
    }


def rapport_markdown(
    syn: dict, transitions: dict, source: str, sortie: str | None = None
) -> Path:
    """Écrit le rapport quotidien en markdown dans `reports/`.

    Le rapport dit ce qui a été mesuré **et** ce qui manque : un taux de réponse
    calculé sur quatre emails n'est pas une information, c'est un frémissement.
    """
    chemin = (
        Path(sortie) if sortie else c.DOSSIER_RAPPORTS / f"suivi_{c.horodatage_jour()}.md"
    )
    chemin.parent.mkdir(parents=True, exist_ok=True)
    lignes = [
        f"# Suivi de prospection — {c.horodatage_jour()}",
        "",
        f"Source : {source or 'aucune (lecture seule)'}",
        f"Généré le {c.maintenant()} par `track_responses.py`.",
        "",
        "## Chiffres clés",
        "",
        "| Indicateur | Valeur |",
        "|---|---|",
        f"| Prospects en base | {syn['prospects']} |",
        f"| Envois (intro + relances) | {syn['envois']} |",
        f"| Taux d'ouverture | {syn['taux']['ouverture']} |",
        f"| Taux de réponse | {syn['taux']['reponse']} |",
        f"| Conversion / réponses | {syn['taux']['conversion_sur_reponses']} |",
        f"| Conversion / envois | {syn['taux']['conversion_sur_envois']} |",
        f"| Revenu déclaré (USDC) | {syn['revenu_usdc']:.2f} |",
        "",
        "## Répartition par statut",
        "",
        "| Statut | Prospects |",
        "|---|---|",
    ]
    for statut in c.STATUTS:
        lignes.append(f"| {statut} | {syn['statuts'].get(statut, 0)} |")
    lignes += ["", "## Jalons consignés", "", "| Jalon | Nombre |", "|---|---|"]
    for nom, nombre in sorted(syn["jalons"].items()):
        lignes.append(f"| {nom} | {nombre} |")
    if transitions:
        lignes += [
            "",
            "## Transitions appliquées par ce passage",
            "",
            "| Transition | Nombre |",
            "|---|---|",
        ]
        for nom, nombre in sorted(transitions.items()):
            lignes.append(f"| {nom} | {nombre} |")
    if syn["par_secteur"]:
        lignes += ["", "## Par secteur", "", "| Secteur | Prospects | Envoyés | Revenu USDC |", "|---|---|---|---|"]
        for bloc in syn["par_secteur"]:
            lignes.append(
                f"| {bloc['secteur']} | {bloc['prospects']} | {bloc['envoyes']} "
                f"| {bloc['revenus']:.2f} |"
            )
    alertes: list[str] = []
    if syn["envois"] < 30:
        alertes.append(
            f"Échantillon faible ({syn['envois']} envois) : aucun taux n'est encore "
            "interprétable, ne changez pas le gabarit sur ces chiffres."
        )
    if syn["envois"] and not syn["jalons"].get("reponse"):
        alertes.append(
            "Aucune réponse consignée : vérifier le suivi (export non importé ?) avant "
            "de conclure que le message ne fonctionne pas."
        )
    desinscrits = syn["statuts"].get("desinscrit", 0)
    if syn["envois"] and desinscrits * 100 > syn["envois"]:
        alertes.append(
            f"{desinscrits} désinscription(s) pour {syn['envois']} envois : au-delà de "
            "1 %, arrêter la campagne et revoir le ciblage (délivrabilité)."
        )
    lignes += ["", "## Points de vigilance", ""]
    if alertes:
        lignes += [f"- {alerte}" for alerte in alertes]
    else:
        lignes.append("- Aucun.")
    chemin.write_text("\n".join(lignes) + "\n", encoding="utf-8")
    return chemin


def purger(base, avant: str, confirmer: bool = False) -> dict:
    """Applique la durée de conservation annoncée : supprime les fiches périmées.

    Une conservation annoncée mais jamais appliquée est un mensonge — la même
    règle vaut pour le journal de découverte de l'API (D30) et pour ce fichier de
    prospection. La durée vit dans `settings.yaml` (`rgpd.retention_annees`, 3 ans
    à compter du dernier contact) ; cette fonction la rend exécutable.

    Args:
        base: connexion SQLite (clés étrangères actives : les événements suivent
            la fiche, en cascade).
        avant: date ISO `AAAA-MM-JJ` ; toute fiche dont le dernier contact est
            antérieur est périmée.
        confirmer: `False` (défaut) = simulation : la liste est rendue, rien n'est
            supprimé. Une suppression de coordonnées de tiers ne se fait pas par
            accident.

    Returns:
        `{"avant", "candidates", "supprimees", "exemples"}`.
    """
    jour = str(avant)[:10]
    cibles = base.execute(
        "SELECT id, email, nom_boutique,"
        " substr(COALESCE(date_derniere_maj, date_import), 1, 10) AS dernier"
        " FROM prospects WHERE substr(COALESCE(date_derniere_maj, date_import), 1, 10) < ?"
        " ORDER BY dernier",
        (jour,),
    ).fetchall()
    rapport = {
        "avant": jour,
        "candidates": len(cibles),
        "supprimees": 0,
        "exemples": [[ligne["email"], ligne["dernier"]] for ligne in cibles[:10]],
    }
    if not confirmer or not cibles:
        return rapport
    identifiants = [int(ligne["id"]) for ligne in cibles]
    base.executemany(
        "DELETE FROM prospects WHERE id = ?", [(identifiant,) for identifiant in identifiants]
    )
    base.commit()
    rapport["supprimees"] = len(identifiants)
    c.journaliser(
        base, "track_responses", "purge", {"avant": jour, "supprimees": len(identifiants)}
    )
    return rapport


def ligne_marquage(texte: str, date_force: str | None = None) -> dict:
    """Transforme une saisie manuelle `email:statut` en ligne de suivi.

    Exemple : `contact@ma-boutique.fr:repondu`. Utile quand la réponse est arrivée
    dans la boîte mail et qu'aucun outil ne l'a exportée — le suivi manuel est
    souvent le seul moyen de ne pas perdre une réponse.

    Raises:
        ValueError: format ou statut inconnu (l'erreur nomme ce qui est accepté).
    """
    texte = str(texte or "").strip()
    if ":" not in texte:
        raise ValueError(f"marquage attendu « email:statut », reçu : {texte!r}")
    email, _, statut_brut = texte.rpartition(":")
    statut = normaliser_statut(statut_brut) or (
        statut_brut if statut_brut in c.STATUTS else ""
    )
    if not statut:
        raise ValueError(
            f"statut inconnu : {statut_brut!r} (acceptés :"
            f" {', '.join(c.STATUTS)}, ou le vocabulaire des outils d'envoi)"
        )
    if not c.valider_email(email)[0]:
        raise ValueError(f"adresse invalide : {email!r}")
    return {"email": email, "statut": statut, "date": date_force or "", "_ligne": "manuel"}


def afficher_synthese(syn: dict) -> None:
    """Rend l'entonnoir lisible : statuts, jalons, taux, revenu."""
    print("\nProspects par statut :")
    for statut in c.STATUTS:
        print(f"  {statut:<12} {syn['statuts'].get(statut, 0)}")
    if syn["jalons"]:
        maximum = max(syn["jalons"].values())
        print("\nJalons :")
        for jalon, nombre in sorted(syn["jalons"].items()):
            print(f"  {jalon:<15} {c.barre(nombre, maximum)} {nombre}")
    print(
        c.tableau(
            ["taux", "valeur"],
            [
                ("ouverture", syn["taux"]["ouverture"]),
                ("réponse", syn["taux"]["reponse"]),
                ("conversion / réponses", syn["taux"]["conversion_sur_reponses"]),
                ("conversion / envois", syn["taux"]["conversion_sur_envois"]),
                ("revenu déclaré (USDC)", f"{syn['revenu_usdc']:.2f}"),
            ],
        )
    )


def construire_arguments() -> argparse.ArgumentParser:
    """Ligne de commande du suivi."""
    analyseur = argparse.ArgumentParser(
        description="Suivi des réponses, refus et conversions d'une campagne de prospection.",
        epilog=(
            "Exemple : python track_responses.py --marquer contact@boutique.fr:repondu "
            "--revenu 0.02"
        ),
    )
    analyseur.add_argument(
        "--input", help="export CSV de l'outil d'envoi (colonnes : email, statut, date)"
    )
    analyseur.add_argument(
        "--marquer",
        action="append",
        metavar="EMAIL:STATUT",
        help="suivi manuel, répétable (ex. contact@boutique.fr:converti)",
    )
    analyseur.add_argument(
        "--etape",
        choices=c.TYPES_EVENEMENT,
        help="type d'événement imposé pour ce passage (ex. relance_1)",
    )
    analyseur.add_argument("--date", help="date ISO appliquée aux événements de ce passage")
    analyseur.add_argument(
        "--revenu", type=float, help="revenu déclaré pour les conversions de ce passage (USDC)"
    )
    analyseur.add_argument("--canal", default="email", help="canal suivi (défaut : email)")
    analyseur.add_argument(
        "--synthese",
        action="store_true",
        help="n'applique rien : lit la base et affiche les taux",
    )
    analyseur.add_argument(
        "--purger-avant",
        metavar="AAAA-MM-JJ",
        help="supprime les fiches dont le dernier contact est antérieur (RGPD, conservation)",
    )
    analyseur.add_argument(
        "--confirmer",
        action="store_true",
        help="avec --purger-avant : supprime réellement (sans ce drapeau : liste seule)",
    )
    analyseur.add_argument("--db", help=f"base SQLite (défaut : {c.CHEMIN_DB})")
    analyseur.add_argument("--dry-run", action="store_true", help="simulation : rien n'est écrit")
    analyseur.add_argument(
        "--rapport", help="rapport markdown (défaut : reports/suivi_<date>.md)"
    )
    return analyseur


def main(argv: Sequence[str] | None = None) -> int:
    """Point d'entrée : 0 réussi, 1 rien appliqué, 2 usage invalide."""
    args = construire_arguments().parse_args(argv)
    c.securiser_sortie()
    c.titre("Suivi des réponses — Address Validator")

    if not (args.input or args.marquer or args.synthese or args.purger_avant):
        print(
            "SUIVI FAIL précisez --input (export), --marquer EMAIL:STATUT (manuel), "
            "--synthese (lecture seule) ou --purger-avant (conservation RGPD)"
        )
        return 2

    try:
        base = c.connexion(args.db, creer=False)
    except FileNotFoundError as erreur:
        print(f"SUIVI FAIL {erreur}")
        return 1

    rapport: dict | None = None
    source = ""
    try:
        lignes: list[dict] = []
        if args.input:
            try:
                lignes += [dict(ligne) for ligne in c.lire_csv(args.input, COLONNES_EXPORT)[0]]
            except (FileNotFoundError, ValueError) as erreur:
                print(f"SUIVI FAIL {erreur}")
                return 1
            source = str(args.input)
        if args.marquer:
            try:
                lignes += [ligne_marquage(texte, args.date) for texte in args.marquer]
            except ValueError as erreur:
                print(f"SUIVI FAIL {erreur}")
                return 2
            source = source or "saisie manuelle"

        if lignes:
            c.resultat(f"{len(lignes)} événement(s) à appliquer")
            rapport = appliquer(
                base,
                lignes,
                etape_force=args.etape,
                date_force=args.date,
                revenu_force=args.revenu,
                dry_run=args.dry_run,
                canal=args.canal,
            )
            print(
                c.tableau(
                    ["indicateur", "valeur"],
                    [
                        ("lignes lues", rapport["lues"]),
                        ("statuts appliqués", rapport["appliquees"]),
                        ("inchangés", rapport["inchangees"]),
                        ("adresses hors base", len(rapport["inconnues"])),
                        ("statuts non reconnus", len(rapport["non_reconnues"])),
                        ("revenu déclaré (USDC)", f"{rapport['revenu_declare']:.2f}"),
                    ],
                )
            )
            if rapport["transitions"]:
                print("\nTransitions :")
                for nom, nombre in sorted(rapport["transitions"].items()):
                    print(f"  {nom:<24} {nombre}")
            for item in rapport["inconnues"][:10]:
                c.alerte(
                    f"ligne {item['ligne']} : {item['email']} n'est pas en base — "
                    "importez l'adresse avant de la suivre"
                )
            for item in rapport["non_reconnues"][:10]:
                c.alerte(
                    f"ligne {item['ligne']} : statut {item['valeur']!r} non reconnu (ignoré)"
                )
            if args.dry_run:
                c.alerte("simulation : ni statut, ni événement écrit.")
            else:
                c.journaliser(
                    base,
                    "track_responses",
                    "suivi",
                    {
                        "source": source,
                        "lues": rapport["lues"],
                        "appliquees": rapport["appliquees"],
                        "inchangees": rapport["inchangees"],
                        "hors_base": len(rapport["inconnues"]),
                        "revenu_declare": rapport["revenu_declare"],
                    },
                )

        if args.purger_avant:
            jour = normaliser_date(args.purger_avant)
            if not jour:
                print("SUIVI FAIL --purger-avant attend une date ISO (AAAA-MM-JJ)")
                return 2
            purge = purger(base, jour, confirmer=args.confirmer)
            print(
                c.tableau(
                    ["conservation", "valeur"],
                    [
                        ("fiches antérieures au", purge["avant"]),
                        ("fiches concernées", purge["candidates"]),
                        ("fiches supprimées", purge["supprimees"]),
                    ],
                )
            )
            for email, dernier in purge["exemples"]:
                c.etape(f"{email} (dernier contact {dernier})")
            if purge["candidates"] and not args.confirmer:
                c.alerte(
                    "simulation : aucune suppression. Relancer avec --confirmer pour "
                    "appliquer la durée de conservation."
                )
            elif purge["supprimees"]:
                c.resultat(
                    f"{purge['supprimees']} fiche(s) supprimée(s), événements en cascade."
                )

        c.titre("Synthèse")
        syn = synthese(base)
        afficher_synthese(syn)
        if not args.dry_run:
            chemin = rapport_markdown(
                syn, (rapport or {}).get("transitions", {}), source, args.rapport
            )
            c.resultat(f"rapport écrit : {chemin}")
    finally:
        base.close()

    if rapport is None:
        return 0
    return 0 if (rapport["appliquees"] or rapport["inchangees"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())

