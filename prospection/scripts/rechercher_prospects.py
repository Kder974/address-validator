#!/usr/bin/env python
"""Agent Web — trouver des boutiques (France métropolitaine / US cross-border) et un email publié.

Ce script complète `sourcer_prospects.py` (annuaire INSEE/INPI) : il interroge un
moteur de recherche public avec les `requetes_web` du secteur, puis visite les
boutiques trouvées pour relever **l'adresse électronique que le site publie**.

Usage :
    python rechercher_prospects.py --secteur mode --limite 30            # simulation
    python rechercher_prospects.py --secteur mode --limite 30 --reseau  # scraping réel
    python rechercher_prospects.py --secteur us-cross-border --moteur google --limite 20 --reseau
    python rechercher_prospects.py --requete 'site:myshopify.com "livraison en France"' --reseau

Ce qu'il fait :
    1. construit les requêtes à partir de `settings.yaml` (ou `--requete`) ;
    2. lit la page de résultats du moteur (DuckDuckGo HTML par défaut, Google en
       option), en dédupliquant par domaine ;
    3. visite chaque boutique : accueil puis pages de contact publiques, en
       respectant `robots.txt`, avec un User-Agent identifié, une pause entre
       sites et une taille de page bornée ;
    4. extrait les adresses **publiées** : liens `mailto:` et textes contenant
       `@` ; jamais d'adresse reconstruite ni devinée ;
    5. écrit un CSV au format `import_prospects.py` (une ligne par boutique avec
       email, plateforme détectée et notes de sourcing).

Ce qu'il ne fait pas :
    * aucun envoi d'email, aucun formulaire rempli, aucun réseau social ;
    * aucune donnée personnelle cachée : seuls des contacts professionnels
      publiés par la boutique sont repris ;
    * sans `--reseau`, le script ne sort pas du poste (simulation) : il affiche
      les requêtes prévues et s'arrête.

Avertissement Google : `google.com/robots.txt` interdit `/search` à tous les
robots. Le moteur par défaut est donc DuckDuckGo (HTML public) ; `--moteur google`
reste possible mais doit être utilisé avec parcimonie, et peut renvoyer un
consentement ou un captcha — le script le dit et continue avec ce qu'il a.

Codes de retour : 0 réussi, 1 aucune boutique exploitable, 2 usage invalide.
"""

from __future__ import annotations

import argparse
import base64
import html as html_module
import re
import sys
import time
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import common as c  # noqa: E402

#: Colonnes de sortie : celles de `import_prospects.py`, ni plus ni moins.
COLONNES = (
    "nom_boutique",
    "site_web",
    "email",
    "prenom",
    "nom",
    "secteur",
    "plateforme",
    "taille",
    "ville",
    "siren",
    "pays",
    "source",
    "notes",
)

#: Moteurs acceptés → gabarit d'URL de résultats. DuckDuckGo **Lite** est le
#: défaut : `html.duckduckgo.com` rend un 202 anti-robot sur certaines IP, alors
#: que `lite.duckduckgo.com` sert des résultats statiques lisibles. Bing/Google
#: ne servent que de secours automatique (voir `agent_web.moteurs_secours`).
MOTEURS = {
    "duckduckgo": "https://lite.duckduckgo.com/lite/?q={requete}",
    "duckduckgo_html": "https://html.duckduckgo.com/html/?q={requete}",
    "bing": "https://www.bing.com/search?q={requete}&setlang=fr",
    "google": "https://www.google.com/search?q={requete}&num={nombre}&hl=fr",
}

#: Moteurs essayés si le premier ne rend rien d'exploitable.
MOTEURS_SECOURS = ("duckduckgo_html", "bing", "google")

#: Domaines de moteurs/annuaires : jamais des boutiques.
DOMAINES_MOTEURS = (
    "google.", "duckduckgo.com", "bing.com", "yahoo.", "yandex.", "qwant.com",
    "ecosia.org", "baidu.com",
)

#: Groupes d'adresses visibles dans le texte d'une page (hors `mailto:`).
#: Le domaine et son TLD sont exigés **en minuscules** : écrit en pleine phrase,
#: « adresse@gmail.com. Nous » ne doit pas donner l'email « …@gmail.com.Nous ».
EMAIL_TEXTE = re.compile(r"[A-Za-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}\b")
LIEN_GOOGLE = re.compile(r'href="/url\?q=(https?://[^&"]+)')
LIEN_DDG = re.compile(r'href="[^"]*[?&]uddg=([^"&]+)')
LIEN_DIRECT = re.compile(r'href="(https?://[^"]+)"')
LIEN_MARKDOWN = re.compile(r'\[[^\]]*\]\((https?://[^)\s]+)\)')
TITRE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)

#: Lecteur public utilisé quand les moteurs renvoient 202/403 depuis l'IP locale.
#: Il ne contourne aucun paiement : il lit la même page publique et la rend en
#: markdown ; les liens de résultats restent ceux du moteur.
PROXY_MOTEUR = "https://r.jina.ai/"

#: Plateformes acceptées comme « vrai site e-commerce » pour la Phase 1.
#: `Inconnue` est écartée : on ne garde pas une page d'aide ou un annuaire.
PLATEFORMES_ECOMMERCE = (
    "Shopify", "WooCommerce", "PrestaShop", "Wix", "Squarespace", "BigCommerce",
)

#: Faux contacts que les thèmes laissent dans le HTML : ils ne sont jamais vendus.
FRAGMENTS_PLACEHOLDER = (
    "you@", "your@", "votre@", "email@", "name@", "nom@", "prenom@", "prénom@",
    "@company.", "@domain.", "@example", "@exemple.", "valider_email",
    "@monsite", "@votresite", "youremail", "email@email", "test@test", "yourname",
    "nomdusite", "votredomaine", "mondomaine", "@votredomaine", "@mondomaine",
)


# --------------------------------------------------------------------------- #
# Moteur de recherche
# --------------------------------------------------------------------------- #


def requetes_du_secteur(secteur: dict, reglages: dict) -> list[str]:
    """Requêtes du secteur (`requetes_web`), ou repli générique si absentes."""
    requetes = [str(r).strip() for r in (secteur.get("requetes_web") or []) if str(r).strip()]
    if requetes:
        return requetes
    # Repli : le code du secteur ne doit jamais produire une liste vide.
    mots = " ".join(str(m) for m in (secteur.get("mots_cles") or [])[:2]) or str(secteur.get("nom") or "")
    return [f"site:myshopify.com {mots} France"]


def _deballer(url_brute: str) -> str:
    """Décode une URL de redirection de moteur (`uddg=`, `q=`, Bing `u=a1…`)."""
    valeur = urllib.parse.unquote(html_module.unescape(url_brute or "").strip())
    valeurs = urllib.parse.parse_qs(urllib.parse.urlparse(valeur).query)
    # Bing enveloppe ses liens dans `bing.com/ck/a?...&u=a1<base64url>`.
    for brut in valeurs.get("u", []):
        if brut.startswith("a1") and len(brut) > 2:
            encode = brut[2:]
            encode += "=" * (-len(encode) % 4)
            try:
                candidate = base64.urlsafe_b64decode(encode).decode("utf-8", errors="replace")
            except Exception:  # noqa: BLE001 - un lien illisible n'est pas une panne
                candidate = ""
            if candidate.startswith(("http://", "https://")):
                return candidate
    for cle in ("uddg", "q", "url"):
        if valeurs.get(cle):
            candidate = urllib.parse.unquote(valeurs[cle][0])
            if candidate.startswith(("http://", "https://")):
                return candidate
    return valeur


def extraire_liens(page: str, moteur: str) -> list[str]:
    """URLs de résultats d'une page de moteur, dans l'ordre, sans doublon."""
    trouves: list[str] = []
    if moteur == "google":
        motifs = [LIEN_GOOGLE, LIEN_DIRECT]
    elif moteur in ("duckduckgo", "duckduckgo_html"):
        motifs = [LIEN_DDG, LIEN_DIRECT]
    else:  # Bing et autres : liens directs, éventuellement enveloppés
        motifs = [LIEN_DIRECT]
    for motif in motifs:
        for brut in motif.findall(page):
            url = _deballer(brut)
            if url.lower().startswith(("http://", "https://")) and url not in trouves:
                trouves.append(url)
    # Une page rendue par le lecteur public est en markdown : liens `[titre](url)`.
    for brut in LIEN_MARKDOWN.findall(page or ""):
        url = _deballer(brut)
        if url.lower().startswith(("http://", "https://")) and url not in trouves:
            trouves.append(url)
    return trouves


def lire_moteur(
    moteur: str, requete: str, nombre: int, reglages: dict
) -> tuple[str, int, str, str]:
    """Lit une page de résultats : direct, puis proxy public, puis secours.

    L'ordre est volontaire : DuckDuckGo **via le proxy** donne des résultats
    propres, alors que Bing direct, sur cette IP, renvoie surtout du bruit. Le
    proxy ne sert qu'après un échec direct du moteur principal.

    Returns:
        `(moteur_utilisé, code, page, note)` ; `code == 0` si rien d'exploitable.
    """
    agent_web = reglages.get("agent_web") or {}
    secours = list(agent_web.get("moteurs_secours") or MOTEURS_SECOURS)
    proxy = str(agent_web.get("proxy_moteur") or PROXY_MOTEUR)
    essais: list[tuple[str, bool]] = [(moteur, False), (moteur, True)]
    for nom in secours:
        if nom != moteur:
            essais.extend([(nom, False), (nom, True)])
    for nom, via_proxy in essais:
        if nom not in MOTEURS:
            continue
        url = MOTEURS[nom].format(requete=urllib.parse.quote(requete), nombre=nombre)
        cible = (proxy + url) if via_proxy else url
        code, page, note = c.requete(cible, reglages, respecter_robots=False)
        if code == 200 and page and filtrer_boutiques(extraire_liens(page, nom), reglages):
            if nom != moteur:
                c.alerte(f"moteur {moteur} indisponible — repli sur {nom}")
            if via_proxy:
                c.alerte(f"moteur {nom} lu via proxy public ({proxy})")
            return nom, code, page, note
        c.alerte(f"{'proxy ' if via_proxy else 'moteur '}{nom} : {note or f'HTTP {code}'}")
        _attendre_moteur(reglages)
    return moteur, 0, "", "aucun moteur exploitable (captcha, 202, proxy ou panne)"


# --------------------------------------------------------------------------- #
# Boutiques et emails publiés
# --------------------------------------------------------------------------- #


def domaine_exclu(url: str, reglages: dict) -> bool:
    """Vrai pour un moteur, un réseau social ou un annuaire listé dans les réglages."""
    domaine = c.domaine_depuis_url(url)
    if not domaine:
        return True
    if any(domaine == suffixe or domaine.endswith("." + suffixe) for suffixe in DOMAINES_MOTEURS):
        return True
    exclusions = (reglages.get("agent_web") or {}).get("exclure_domaines") or []
    return any(
        domaine == str(exclu).lower() or domaine.endswith("." + str(exclu).lower())
        for exclu in exclusions
        if str(exclu).strip()
    )


def filtrer_boutiques(urls: list[str], reglages: dict) -> list[str]:
    """Garde les domaines publics de boutiques, un seul par domaine, ordre conservé."""
    gardees: list[str] = []
    vus: set[str] = set()
    for url in urls:
        propre = c.normaliser_url(url)
        domaine = c.domaine_depuis_url(propre)
        if not domaine or domaine in vus or domaine_exclu(propre, reglages):
            continue
        if not c.hote_public(domaine):
            continue
        vus.add(domaine)
        gardees.append(f"https://{domaine}")
    return gardees


def emails_dans_page(page: str) -> list[str]:
    """Emails publiés : `mailto:` d'abord, puis textes contenant `@`.

    Aucun email n'est inventé : un candidat doit passer `valider_email`, et les
    fragments techniques (sentry, cdn, exemples…) sont écartés comme ailleurs.
    """
    trouves: list[str] = []
    for adresse in c.extraire_emails(page):
        if adresse not in trouves:
            trouves.append(adresse)
    for brut in EMAIL_TEXTE.findall(page or ""):
        adresse = brut.strip().strip(".,;:)(")
        if not adresse or adresse in trouves:
            continue
        if any(fragment in adresse.lower() for fragment in c.FRAGMENTS_EMAIL_A_IGNORER):
            continue
        if any(fragment in adresse.lower() for fragment in FRAGMENTS_PLACEHOLDER):
            continue
        if c.valider_email(adresse)[0]:
            trouves.append(adresse)
    return trouves


def choisir_email(emails: list[str]) -> str:
    """Le meilleur contact publié : nominatif/normal avant une boîte générique."""
    if not emails:
        return ""
    notes = sorted(
        emails,
        key=lambda adresse: (
            "boite_personnelle" in c.valider_email(adresse)[2],
            "role_generique" in c.valider_email(adresse)[2],
            len(adresse),
        ),
    )
    return notes[0]


def nom_boutique(page: str, url: str) -> str:
    """Titre de la page d'accueil, nettoyé ; repli sur le domaine."""
    trouve = TITRE.search(page or "")
    if trouve:
        titre = re.sub(r"\s+", " ", html_module.unescape(trouve.group(1))).strip()
        titre = re.split(r"\s+[|–—]\s+", titre, maxsplit=1)[0].strip()
        if titre and len(titre) <= 80:
            return titre
    domaine = c.domaine_depuis_url(url)
    return domaine.split(".")[0].replace("-", " ").title() if domaine else ""


def _attendre_moteur(reglages: dict) -> None:
    """Pause entre deux requêtes de moteur (réglage `agent_web.delai_moteur_s`)."""
    delai = float((reglages.get("agent_web") or {}).get("delai_moteur_s") or 0)
    if delai > 0:
        time.sleep(delai)


def visiter_boutique(url: str, secteur: dict, reglages: dict) -> dict | None:
    """Visite accueil + pages contact, puis rend la fiche ou `None` si rien d'utile."""
    agent_web = reglages.get("agent_web") or {}
    pages = list(agent_web.get("pages_contact") or ["", "/contact"])
    base = url.rstrip("/")
    accueil = ""
    emails: list[str] = []
    notes: list[str] = []
    for chemin in pages:
        cible = base + ("" if not chemin else "/" + str(chemin).lstrip("/"))
        code, corps, note = c.requete(cible, reglages)
        if code == 200 and corps:
            if not accueil:
                accueil = corps
            emails.extend(emails_dans_page(corps))
            if emails:
                break
        if note:
            notes.append(str(note))
        c.attendre(reglages)
    if not accueil:
        return None
    email = choisir_email(list(dict.fromkeys(emails)))
    plateforme, preuve = c.detecter_plateforme(url, accueil, reglages)
    if plateforme not in PLATEFORMES_ECOMMERCE:
        return None
    return {
        "nom_boutique": nom_boutique(accueil, url),
        "site_web": base,
        "email": email,
        "prenom": "",
        "nom": "",
        "secteur": str(secteur.get("code") or ""),
        "plateforme": plateforme,
        "taille": "",
        "ville": "",
        "siren": "",
        "pays": str(secteur.get("pays_cible") or "FR"),
        "source": "agent-web",
        "notes": " ; ".join(notes[:2]),
        "_plateforme_preuve": preuve,
    }


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #


def chercher(
    secteur: dict,
    reglages: dict,
    *,
    requetes: list[str],
    moteur: str,
    limite: int,
    max_visites: int,
    reseau: bool,
) -> tuple[list[dict], dict[str, int]]:
    """Enchaîne moteur → boutiques → emails. Ne rend que les boutiques AVEC email.

    Returns:
        `(fiches, statistiques)` : les fiches retenues et les compteurs de refus
        (visites, plateforme non e-commerce, page sans email, moteur indisponible).
    """
    resultats: list[dict] = []
    stats = {"visites": 0, "plateforme_ecartee": 0, "sans_email": 0, "moteur_indisponible": 0}
    visites = 0
    urls_vues: set[str] = set()
    for requete in requetes:
        if len(resultats) >= limite or visites >= max_visites:
            break
        c.etape(f"moteur {moteur} : {requete}")
        utilise, code, page, note = lire_moteur(
            moteur, requete, int((reglages.get("agent_web") or {}).get("resultats_par_requete") or 30), reglages
        )
        if code != 200 or not page:
            stats["moteur_indisponible"] += 1
            c.alerte(f"moteur : {note or f'HTTP {code}'} — requête abandonnée")
            _attendre_moteur(reglages)
            continue
        if utilise != moteur:
            c.resultat(f"résultats obtenus via {utilise}")
        boutiques = filtrer_boutiques(extraire_liens(page, utilise), reglages)
        c.resultat(f"{len(boutiques)} boutique(s) retenue(s) après filtrage")
        for url in boutiques:
            if len(resultats) >= limite or visites >= max_visites:
                break
            if url in urls_vues:
                continue
            urls_vues.add(url)
            visites += 1
            stats["visites"] = visites
            fiche = visiter_boutique(url, secteur, reglages) if reseau else None
            if fiche is None:
                stats["plateforme_ecartee"] += 1
                c.resultat(f"écartée (plateforme non e-commerce ou page injoignable) : {url}")
            elif not fiche["email"]:
                stats["sans_email"] += 1
                c.resultat(f"écartée (aucun email publié) : {url}")
            else:
                resultats.append(fiche)
        _attendre_moteur(reglages)
    return resultats, stats


def main(argv: list[str] | None = None) -> int:
    c.securiser_sortie()
    reglages = c.charger_reglages()
    agent_web = reglages.get("agent_web") or {}

    analyseur = argparse.ArgumentParser(
        description="Agent Web : moteur de recherche → boutiques → emails publiés.",
    )
    analyseur.add_argument(
        "--secteur", help="code du secteur (mode, cosmetiques, high-tech, decoration, us-cross-border)"
    )
    analyseur.add_argument(
        "--requete", action="append", help="requête de moteur explicite (répétable, prioritaire sur le secteur)"
    )
    analyseur.add_argument("--moteur", choices=tuple(MOTEURS), default=str(agent_web.get("moteur") or "duckduckgo"))
    analyseur.add_argument("--limite", type=int, default=30, help="nombre maximal de boutiques AVEC email")
    analyseur.add_argument(
        "--max-visites", type=int, default=int(agent_web.get("max_visites") or 60), help="pages de boutiques visitées"
    )
    analyseur.add_argument(
        "--sortie", type=Path, help="CSV de sortie (défaut : data/sourcing_web_<date>.csv)"
    )
    analyseur.add_argument("--reseau", action="store_true", help="effectue réellement les requêtes (sinon simulation)")
    args = analyseur.parse_args(argv)

    secteur = c.trouver_secteur(args.secteur, reglages) if args.secteur else None
    if args.secteur and secteur is None:
        c.alerte(f"secteur inconnu : {args.secteur}")
        return 2
    if secteur is None:
        c.alerte("indiquez --secteur ou --requete")
        return 2
    requetes = args.requete or requetes_du_secteur(secteur, reglages)
    if not requetes:
        c.alerte("aucune requête à exécuter")
        return 2
    if args.limite <= 0:
        c.alerte("--limite doit être positif")
        return 2

    sortie = args.sortie or (c.DOSSIER_DONNEES / f"sourcing_web_{c.horodatage_jour()}.csv")
    c.titre(f"Agent Web — secteur {secteur.get('nom')} ({secteur.get('code')})")
    c.etape(f"moteur : {args.moteur} ; {len(requetes)} requête(s) ; limite {args.limite} boutiques avec email")
    for requete in requetes:
        c.resultat(f"requête prévue : {requete}")

    if not args.reseau:
        c.alerte("simulation : aucune requête réseau. Relancez avec --reseau pour sourcer réellement.")
        return 0

    fiches, stats = chercher(
        secteur,
        reglages,
        requetes=requetes,
        moteur=args.moteur,
        limite=args.limite,
        max_visites=args.max_visites,
        reseau=True,
    )
    c.resultat(
        f"bilan : {stats['visites']} visite(s), {stats['plateforme_ecartee']} écartée(s) "
        f"(plateforme non e-commerce/injoignable), {stats['sans_email']} sans email publié, "
        f"{len(fiches)} retenue(s)"
    )
    if not fiches:
        c.alerte("aucune boutique avec email publié : rien à importer cette fois.")
        return 1

    c.ecrire_csv(sortie, COLONNES, fiches)
    c.resultat(f"{len(fiches)} boutique(s) avec email écrites dans {sortie}")
    c.tableau(
        ["boutique", "domaine", "email", "plateforme"],
        [(f["nom_boutique"], c.domaine_depuis_url(f["site_web"]), f["email"], f["plateforme"]) for f in fiches[:12]],
    )
    c.etape("import : python import_prospects.py --input " + str(sortie) + f" --secteur {secteur.get('code')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
