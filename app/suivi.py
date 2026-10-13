"""Suivi des clients (D50) : qui joindre, pourquoi, dans quel ordre.

D44 a créé les comptes, D45 a écrit les règles qui décident de servir un appel,
D46 a ouvert l'essai au public et D49 a écrit l'email d'accueil. Ce module répond
à la question qui suit, celle qui décide du revenu : **parmi les comptes qui
existent, lesquels sont en train de se perdre en silence, et que faut-il leur
dire ?**

Quatre règles, chacune mesurable :

1. **L'état affiché est l'état servi.** Un état n'est pas deviné : il est déduit du
   motif de refus que la route payante applique (`accounts.refusal_reason`) et du
   statut qu'elle lit (`accounts.plan_status`). Les codes HTTP affichés viennent
   de `REFUS_HTTP`, celui de la route — ils ne sont pas recopiés ici. Un rapport
   qui aurait ses propres règles dirait « actif » à un client dont les appels
   reçoivent `403` : il rassurerait au lieu de décider, ce qui est pire que pas de
   rapport du tout.
2. **Aucun secret.** Un suivi lit des comptes, pas des clés : il n'a besoin que du
   **préfixe** (`sk_live_ab12`), qui suffit à reconnaître un compte au téléphone.
   Une clé ne peut pas fuir d'un rapport qui ne la lit jamais. Aucune adresse
   validée n'est lue non plus : le suivi compte des lignes, il ne les ouvre pas.
3. **Lecture seule.** Les seules requêtes du module sont des `query` : jamais
   `add`, `commit`, `flush` ni `delete`. Un outil de décision qui écrirait dans la
   base modifierait l'objet qu'il mesure.
4. **Un rapport vide n'est pas un « tout va bien ».** Une base injoignable ou sans
   table ne donne pas une liste vide : c'est un échec de mesure, et c'est
   `scripts/suivi_clients.py` qui le porte (message explicite, code de retour
   `2`). Un seuil absurde, lui, lève une `ValueError` — jamais un rapport vide.

L'ordre publié est un **ordre d'urgence**, pas un ordre alphabétique : d'abord les
comptes refusés *maintenant*, puis ceux dont l'échéance tombe dans quelques jours,
puis ceux qui ne tirent rien du service. Chaque état dit **quoi faire**, pas
seulement ce qui se passe.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime, timezone

from sqlalchemy import func

from app import accounts
from app.models import Customer, CustomerValidation

# Importé de la route elle-même : un rapport qui annoncerait un code HTTP que la
# route n'émet pas serait à corriger avant le client, pas après lui.
from app.routes.validate import REFUS_HTTP

#: Un essai dont la fin tombe dans cet horizon est encore convertible : c'est le
#: dernier moment utile pour appeler, après la date il est déjà refusé.
SEUIL_ESSAI_BIENTOT_JOURS = 3
#: Un compte qui n'a servi aucune validation depuis sa création n'est pas un
#: compte récent : c'est une intégration qui n'a pas abouti (motif d'appel).
SEUIL_JAMAIS_UTILISE_JOURS = 3
#: Un abonné sans aucune validation depuis trois semaines paie sans consommer :
#: soit son intégration est cassée, soit il est en train de résilier.
SEUIL_DORMANT_JOURS = 21
#: Part du quota mensuel à partir de laquelle il faut prévenir *avant* la panne.
SEUIL_QUOTA_PCT = 80

#: Un état de suivi par situation actionnable, du plus urgent au moins urgent.
#: `priorite` est l'ordre **publié** par le rapport (1 = à joindre en premier) :
#: il est déclaré ici et vérifié par la porte, plutôt que trié au hasard des
#: appels. `libelle` est destiné à l'humain, `action` dit quoi faire — les
#: gabarits sont remplis avec les faits de la ligne (`{refus_http}`, `{quota}`…).
ETATS: dict[str, dict] = {
    "essai_fini": {
        "priorite": 1,
        "libelle": "Essai terminé — appels refusés",
        "action": (
            "Appeler : ses appels reçoivent un refus {refus_http} ({refus}). "
            "Proposer l'abonnement{plan_au_dessus_txt} — le tarif public est sur "
            "/pricing, la marche à suivre sur /guide."
        ),
    },
    "quota_atteint": {
        "priorite": 2,
        "libelle": "Quota du mois consommé — appels refusés",
        "action": (
            "Appeler : ses appels reçoivent un refus {refus_http} ({refus}) "
            "jusqu'au 1er du mois suivant. Proposer{plan_au_dessus_txt} plutôt "
            "que de le laisser arrêté."
        ),
    },
    "essai_bientot": {
        "priorite": 3,
        "libelle": "Essai qui se termine",
        "action": (
            "L'essai se termine dans {jours_essai} j ({essai_fin_le}) : c'est le "
            "dernier moment utile pour convertir. Vérifier que l'email de "
            "bienvenue (D49) est parti, sinon l'envoyer."
        ),
    },
    "quota_presque": {
        "priorite": 4,
        "libelle": "Quota du mois presque consommé",
        "action": (
            "{pct_quota} % du quota mensuel consommé ({valide_mois}/{quota}) : "
            "au-delà, les appels seront refusés jusqu'au 1er du mois suivant. "
            "Prévenir maintenant et proposer{plan_au_dessus_txt}."
        ),
    },
    "jamais_utilise": {
        "priorite": 5,
        "libelle": "Compte créé, aucune validation servie",
        "action": (
            "Aucune validation servie depuis la création ({jours_creation} j) : "
            "l'intégration n'a probablement pas abouti. Proposer la recette pas à "
            "pas (/guide) et vérifier la clé ({cle_prefixe}) avec lui."
        ),
    },
    "dormant": {
        "priorite": 6,
        "libelle": "Abonné sans appel",
        "action": (
            "Abonné sans aucune validation depuis {jours_sans_appel} j : il paie "
            "sans consommer. Vérifier que son intégration fonctionne encore avant "
            "qu'il ne résilie."
        ),
    },
    "ok": {
        "priorite": 7,
        "libelle": "Rien à signaler",
        "action": "Rien à faire : le compte est servi et son usage est normal.",
    },
}

#: L'état sans action : tout ce qui n'appelle personne. Nommé, parce que le
#: rapport et les compteurs s'y réfèrent (« à joindre » = tout le reste).
ETAT_OK = "ok"


def _conscient(moment: datetime | None) -> datetime | None:
    """Horodatage relu dans la convention des colonnes `TIMESTAMP` (UTC sans fuseau).

    Même convention qu'`accounts._conscient` : les horodatages sont écrits en UTC
    sans fuseau. Comparer un `datetime` naïf à un `datetime` conscient lève une
    `TypeError`, donc toute valeur relue de la base passe par ici avant d'être
    comparée à `maintenant`.
    """
    if moment is None:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _jours(depuis: datetime | None, jusqua: datetime) -> int | None:
    """Jours entiers écoulés entre deux instants, bornés à zéro.

    Borné à zéro parce qu'une horloge qui recule (ou une ligne écrite avec une
    seconde d'avance) ne doit pas produire un « depuis -1 jour » dans un rapport
    qui sert à décider.
    """
    if depuis is None:
        return None
    return max(0, (jusqua - depuis).days)


def _jour_lisible(moment: datetime | None) -> str:
    """La date d'une ligne, sans l'heure : c'est l'échelle d'un suivi client."""
    conscient = _conscient(moment)
    return "date inconnue" if conscient is None else conscient.strftime("%Y-%m-%d")


def dernier_service(session, client_id: int) -> datetime | None:
    """Dernière validation **servie** au compte, ou `None` s'il n'en a aucune.

    C'est la seule mesure d'usage dont un suivi a besoin : une date. Le contenu
    des lignes n'est jamais lu — ni adresse, ni résultat (`PRIVACY.md`).
    """
    moment = (
        session.query(func.max(CustomerValidation.created_at))
        .filter(CustomerValidation.customer_id == client_id)
        .scalar()
    )
    return _conscient(moment)


def plan_au_dessus(plan: str) -> str | None:
    """Plan suivant dans l'ordre publié (`accounts.PLANS`), `None` au dernier.

    L'ordre de `accounts.PLANS` est celui des offres (essai → starter → pro →
    enterprise) : le plan au-dessus est donc lu dans la source qui applique les
    quotas, jamais recopié ici. Un plan inconnu retombe sur le défaut, comme
    `accounts.quota_for`.
    """
    codes = list(accounts.PLANS)
    position = codes.index(plan) if plan in codes else codes.index(accounts.PLAN_PAR_DEFAUT)
    return codes[position + 1] if position + 1 < len(codes) else None


def _nombre(valeur: int) -> str:
    """« 10 000 » plutôt que « 10000 » : un rapport se lit, il ne se décode pas."""
    return f"{valeur:,}".replace(",", " ")


def _plan_au_dessus_texte(plan: str) -> str:
    """« le plan Starter (2 000 validations/mois) », ou vide s'il n'y en a pas.

    Le quota annoncé vient d'`accounts.quota_for`, pas d'une phrase commerciale :
    un rapport de suivi n'a pas à citer de prix (le tarif public vit sur
    `/pricing`), mais il ne doit pas non plus annoncer un quota qui ne serait pas
    celui appliqué.
    """
    suivant = plan_au_dessus(plan)
    if suivant is None:
        return ""
    libelle = accounts.PLAN_LIBELLES.get(suivant, suivant)
    quota = accounts.quota_for(suivant)
    if quota is None:
        return f" le plan {libelle} (quota illimité)"
    return f" le plan {libelle} ({_nombre(quota)} validations/mois)"


def _detail(ligne: dict) -> str:
    """Les faits d'une ligne en une phrase : rien qui ne soit mesuré.

    Sert de trace imprimable et de colonne CSV. Chaque morceau vient de la base ou
    de `accounts.plan_status` — ce texte ne doit jamais inventer un chiffre.
    """
    morceaux = [f"plan {ligne['plan_libelle']}"]
    if ligne["dernier_service"] is None:
        morceaux.append(
            f"aucune validation servie depuis la création ({ligne['creation_le']})"
        )
    else:
        morceaux.append(
            f"dernier service {ligne['dernier_service_le']} "
            f"(il y a {ligne['jours_sans_appel']} j)"
        )
    if ligne["quota"] is None:
        morceaux.append(
            f"{_nombre(ligne['valide_mois'])} validations ce mois (quota illimité)"
        )
    else:
        morceaux.append(
            f"{_nombre(ligne['valide_mois'])}/{_nombre(ligne['quota'])} validations ce mois"
        )
    if ligne["refus"] is not None:
        morceaux.append(f"refus {ligne['refus']} (HTTP {ligne['refus_http']})")
    # La date d'essai n'est citée que quand elle décide encore quelque chose : pour
    # un abonné, c'est une date passée qui n'explique rien de son état.
    if ligne["essai_fin"] is not None and ligne["plan"] == accounts.PLAN_PAR_DEFAUT:
        morceaux.append(f"essai jusqu'au {ligne['essai_fin_le']}")
    return " ; ".join(morceaux)


def etat_client(
    session,
    client: Customer,
    *,
    maintenant: datetime | None = None,
    seuil_essai_jours: int = SEUIL_ESSAI_BIENTOT_JOURS,
    seuil_jamais_utilise_jours: int = SEUIL_JAMAIS_UTILISE_JOURS,
    seuil_dormant_jours: int = SEUIL_DORMANT_JOURS,
    seuil_quota_pct: int = SEUIL_QUOTA_PCT,
) -> dict:
    """État de suivi d'un compte : ce qui se passe, et ce qu'il faut en faire.

    Les deux premiers cas sont lus dans `accounts.refusal_reason`, la fonction que
    la route payante appelle : un compte affiché « essai terminé » est donc
    *exactement* un compte dont la clé reçoit `403` — il n'existe pas de chemin qui
    produise l'un sans l'autre. Les cas suivants sont des seuils déclarés
    (`SEUIL_*`), réglables par l'appelant.
    """
    moment = _conscient(maintenant) or accounts.now_utc()
    statut = accounts.plan_status(session, client, moment)
    refus = accounts.refusal_reason(session, client, moment)
    dernier = dernier_service(session, client.id)
    creation = _conscient(client.created_at)
    quota = statut["quota"]
    valide_mois = statut["validations_mois"]
    jours_creation = _jours(creation, moment)
    jours_sans_appel = _jours(dernier, moment)
    jours_essai = statut["trial_jours_restants"]
    pct_quota = None if not quota else (valide_mois * 100) // quota

    if refus == accounts.REFUS_COMPTE_EXPIRE:
        etat = "essai_fini"
    elif refus == accounts.REFUS_QUOTA_ATTEINT:
        etat = "quota_atteint"
    elif (
        client.plan == accounts.PLAN_PAR_DEFAUT
        and not statut["trial_expire"]
        and jours_essai is not None
        and jours_essai <= seuil_essai_jours
    ):
        etat = "essai_bientot"
    elif (
        quota is not None
        and not statut["quota_atteint"]
        and valide_mois * 100 >= seuil_quota_pct * quota
    ):
        etat = "quota_presque"
    elif (
        dernier is None
        and jours_creation is not None
        and jours_creation >= seuil_jamais_utilise_jours
    ):
        etat = "jamais_utilise"
    elif (
        client.plan != accounts.PLAN_PAR_DEFAUT
        and jours_sans_appel is not None
        and jours_sans_appel >= seuil_dormant_jours
    ):
        etat = "dormant"
    else:
        etat = ETAT_OK

    ligne = {
        "etat": etat,
        "priorite": ETATS[etat]["priorite"],
        "libelle": ETATS[etat]["libelle"],
        # L'échéance qui décide du tri : la fin d'essai quand c'est elle qui va
        # refuser les appels, rien sinon (le tri retombe alors sur le nom).
        "echeance": (
            statut["trial_ends_at"] if etat in ("essai_fini", "essai_bientot") else None
        ),
        "client_id": client.id,
        "email": client.email,
        "shop_name": client.shop_name,
        "platform": client.platform,
        "plan": client.plan,
        "plan_libelle": statut["plan_libelle"],
        "quota": quota,
        "valide_mois": valide_mois,
        "valide_total": statut["validations"],
        "invalides_total": statut["invalides"],
        "restant": statut["restant"],
        "pct_quota": pct_quota,
        "essai_fin": statut["trial_ends_at"],
        "essai_fin_le": _jour_lisible(statut["trial_ends_at"]),
        "jours_essai": jours_essai,
        "dernier_service": dernier,
        "dernier_service_le": _jour_lisible(dernier),
        "jours_sans_appel": jours_sans_appel,
        "creation": creation,
        "creation_le": _jour_lisible(creation),
        "jours_creation": jours_creation,
        # Le préfixe suffit à reconnaître la clé au téléphone ; la clé complète
        # n'existe nulle part en base (D44), donc elle ne peut pas fuir d'ici.
        "cle_prefixe": client.api_key_prefix,
        "refus": refus,
        "refus_http": REFUS_HTTP.get(refus) if refus else None,
        "plan_au_dessus": plan_au_dessus(client.plan),
    }
    ligne["plan_au_dessus_txt"] = _plan_au_dessus_texte(client.plan)
    ligne["action"] = ETATS[etat]["action"].format(**ligne)
    ligne["detail"] = _detail(ligne)
    return ligne


def suivi(
    session,
    *,
    maintenant: datetime | None = None,
    seuil_essai_jours: int = SEUIL_ESSAI_BIENTOT_JOURS,
    seuil_jamais_utilise_jours: int = SEUIL_JAMAIS_UTILISE_JOURS,
    seuil_dormant_jours: int = SEUIL_DORMANT_JOURS,
    seuil_quota_pct: int = SEUIL_QUOTA_PCT,
) -> list[dict]:
    """Une ligne par compte, dans l'ordre d'urgence publié par `ETATS`.

    Un seuil absurde lève une `ValueError` au lieu de rendre un rapport vide : une
    liste vide a l'air d'un « tout va bien », et un rapport de suivi qui se trompe
    de ce côté-là est plus dangereux qu'une erreur visible.
    """
    for nom, valeur in (
        ("seuil_essai_jours", seuil_essai_jours),
        ("seuil_jamais_utilise_jours", seuil_jamais_utilise_jours),
        ("seuil_dormant_jours", seuil_dormant_jours),
    ):
        if isinstance(valeur, bool) or not isinstance(valeur, int) or valeur < 0:
            raise ValueError(f"{nom} doit etre un nombre de jours positif")
    if (
        isinstance(seuil_quota_pct, bool)
        or not isinstance(seuil_quota_pct, int)
        or not 1 <= seuil_quota_pct <= 100
    ):
        raise ValueError("seuil_quota_pct doit etre un pourcentage entre 1 et 100")

    moment = _conscient(maintenant) or accounts.now_utc()
    lignes = [
        etat_client(
            session,
            client,
            maintenant=moment,
            seuil_essai_jours=seuil_essai_jours,
            seuil_jamais_utilise_jours=seuil_jamais_utilise_jours,
            seuil_dormant_jours=seuil_dormant_jours,
            seuil_quota_pct=seuil_quota_pct,
        )
        for client in session.query(Customer).order_by(Customer.id)
    ]
    # Tri stable et explicite : l'urgence déclarée, puis l'échéance la plus
    # proche, puis le nom. Sans l'échéance, deux essais qui finissent le même
    # jour seraient triés au hasard de la base.
    horizon = datetime.max.replace(tzinfo=timezone.utc)
    lignes.sort(
        key=lambda ligne: (
            ligne["priorite"],
            ligne["echeance"] or horizon,
            ligne["shop_name"].casefold(),
            ligne["email"],
        )
    )
    return lignes


def resume(lignes: list[dict]) -> dict:
    """Compteurs du rapport : total, à joindre, par état, abonnés, essais."""
    par_etat = {code: 0 for code in ETATS}
    for ligne in lignes:
        par_etat[ligne["etat"]] += 1
    abonnes = sum(1 for ligne in lignes if ligne["plan"] != accounts.PLAN_PAR_DEFAUT)
    return {
        "total": len(lignes),
        "a_joindre": sum(1 for ligne in lignes if ligne["etat"] != ETAT_OK),
        "par_etat": par_etat,
        "abonnes": abonnes,
        "essais": len(lignes) - abonnes,
    }


#: Colonnes du CSV, dans l'ordre : d'abord de quoi appeler, ensuite les faits.
COLONNES_CSV = (
    "priorite",
    "etat",
    "shop_name",
    "email",
    "platform",
    "plan",
    "plan_libelle",
    "quota",
    "valide_mois",
    "restant",
    "dernier_service_le",
    "essai_fin_le",
    "cle_prefixe",
    "refus",
    "refus_http",
    "detail",
    "action",
)


def _valeur_csv(valeur) -> str:
    """Cellule de CSV : `None` devient vide, le reste est écrit tel quel."""
    return "" if valeur is None else str(valeur)


def rapport_csv(lignes: list[dict]) -> str:
    """Le même rapport en tableau (séparateur `;`), à ouvrir dans un tableur.

    La colonne s'appelle `cle_prefixe` parce que c'est tout ce que le suivi
    connaît : une clé complète n'est pas stockée (D44), donc elle ne peut pas
    figurer dans un export.
    """
    tampon = io.StringIO()
    ecrivain = csv.writer(tampon, delimiter=";", lineterminator="\n")
    ecrivain.writerow(COLONNES_CSV)
    for ligne in lignes:
        ecrivain.writerow([_valeur_csv(ligne[colonne]) for colonne in COLONNES_CSV])
    return tampon.getvalue()


def rapport_texte(lignes: list[dict], moment: datetime | None = None) -> str:
    """Le rapport humain : les comptes à joindre d'abord, l'action sous chacun.

    Les états sans personne à joindre ne sont pas numérotés : une liste d'appels
    qui compterait aussi les lignes « rien à faire » ferait perdre du temps à
    celui qui la lit.
    """
    compteurs = resume(lignes)
    quand = _conscient(moment) or accounts.now_utc()
    sortie = [
        f"Suivi clients Address Validator — {quand.strftime('%Y-%m-%d %H:%M UTC')}",
        "=" * 72,
        f"{compteurs['total']} compte(s) dans la base : {compteurs['a_joindre']} à joindre, "
        f"{compteurs['abonnes']} abonné(s), {compteurs['essais']} essai(s).",
        "Rapport en lecture seule : aucun appel réseau, aucune écriture dans la base.",
        "",
    ]
    numero = 0
    for code, meta in ETATS.items():
        if code == ETAT_OK:
            continue
        groupe = [ligne for ligne in lignes if ligne["etat"] == code]
        if not groupe:
            continue
        numero += 1
        sortie.append(f"{numero}. {meta['libelle']} ({len(groupe)})")
        for ligne in groupe:
            sortie.append(
                f"   - {ligne['shop_name']} <{ligne['email']}> — {ligne['platform']}"
            )
            sortie.append(f"     {ligne['detail']}")
            sortie.append(
                f"     clé {ligne['cle_prefixe']} — action : {ligne['action']}"
            )
        sortie.append("")
    sains = [ligne for ligne in lignes if ligne["etat"] == ETAT_OK]
    if sains:
        sortie.append(
            f"Rien à signaler ({len(sains)}) : "
            + ", ".join(ligne["shop_name"] for ligne in sains)
        )
    if compteurs["total"] == 0:
        sortie.append(
            "Cette base ne contient aucun compte : rien n'a été mesuré, ce n'est pas "
            "un « tout va bien »."
        )
    return "\n".join(sortie).rstrip() + "\n"
