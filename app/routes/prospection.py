"""/prospection — CRM de prospection (Tâche 7).

Pourquoi cette route, et pourquoi elle ressemble à `/dashboard` (D32) :

1. **Fermée par défaut.** Sans `DASHBOARD_PASSWORD`, la route répond `503` — pas
   `200`. Le CRM contient des coordonnées de tiers et le chiffre d'affaires
   signé : « oublié de mettre un mot de passe » doit fermer la porte.
2. **Autonome.** Aucune ressource externe (pas de CDN, pas de police distante) ;
   le JavaScript embarqué ne fait que lire et écrire sur ce même serveur. La CSP
   l'autorise explicitement (`script-src 'unsafe-inline'`) là où `/dashboard` s'en
   passe : ce CRM filtre et marque des statuts, il a besoin de script, et le
   déclarer vaut mieux que de le taire.
3. **Honnête.** Si la base de prospection n'existe pas, la route répond `503` en
   disant quoi lancer — elle n'affiche jamais des zéros à la place de l'absence de
   données, et ne crée jamais une base vide depuis une requête HTTP.
4. **Séparée du service payant.** Le CRM lit `prospection/data/prospects.db`
   (SQLite local, `PROSPECTION_DB`). Ce dossier n'est pas déployé sur le VPS :
   l'API en production n'a aucune raison d'héberger les fichiers de prospection,
   et cette route y répondra simplement « base absente ».

Endpoints :

* `GET /prospection` — la page (HTML autonome, Basic Auth) ;
* `GET /prospection/api/prospects` — fiches filtrées (JSON) ;
* `GET /prospection/api/stats` — compteurs et taux (JSON) ;
* `POST /prospection/api/prospects/{id}/statut` — change un statut (`{"statut": "repondu"}`) ;
* `GET /prospection/export.csv` — export CSV des fiches filtrées.

Les endpoints sont déclarés `def` (synchrones) : FastAPI les exécute alors dans un
fil externe, et l'accès SQLite — rapide mais bloquant — n'immobilise pas la boucle
d'événements qui sert `/validate`.
"""

from __future__ import annotations

import csv
import io
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Body, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from app.config import get_settings
from app.routes.dashboard import BASE_HEADERS, pilotage_ouvert, refus_acces

router = APIRouter()

#: La page embarque une feuille de style et un script ; la CSP le dit, au lieu de
#: laisser le navigateur le découvrir. `connect-src 'self'` borne le script aux
#: appels vers ce même service : aucune donnée de prospect ne peut partir ailleurs.
PAGE_HEADERS = {
    **BASE_HEADERS,
    "Content-Security-Policy": (
        "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
        "connect-src 'self'; img-src 'none'; form-action 'none'; "
        "frame-ancestors 'none'; base-uri 'none'"
    ),
}

#: Statuts que le CRM accepte d'écrire (mêmes valeurs que `prospection/scripts/common.py`).
STATUTS = ("nouveau", "envoye", "ouvert", "repondu", "converti", "refuse", "desinscrit")

#: Statut → fait consigné dans `envois`, pour que l'analyse (qui lit le journal)
#: voie les décisions prises depuis le CRM. Ce tableau est recopié ici plutôt
#: qu'importé : l'API ne doit pas dépendre d'un dossier (`prospection/`) qui n'est
#: pas déployé avec elle.
EVENEMENT_POUR = {
    "repondu": "reponse",
    "converti": "conversion",
    "refuse": "refus",
    "desinscrit": "desinscription",
}

#: Colonnes affichées et exportées. Liste fixe : aucune colonne de la base n'est
#: exposée par accident — le revenu et la campagne sont nécessaires au suivi, les
#: notes libres restent dans la base et ne sortent pas par l'API.
COLONNES_VISIBLES = (
    "id",
    "email",
    "nom_boutique",
    "prenom",
    "nom",
    "secteur",
    "plateforme",
    "ville",
    "statut",
    "variante",
    "objet_retenu",
    "date_import",
    "date_envoi",
    "date_reponse",
    "revenu_usdc",
    "campagne",
)


def _chemin_base() -> Path:
    """Chemin de la base de prospection (réglage `PROSPECTION_DB`)."""
    valeur = str(get_settings().prospection_db or "").strip()
    return Path(valeur) if valeur else Path("prospection/data/prospects.db")


def _ouvrir(lecture_seule: bool = True) -> sqlite3.Connection:
    """Ouvre la base de prospection ; refuse de la créer.

    Une route HTTP n'a pas à fabriquer une base vide : elle doit dire qu'elle n'a
    rien à montrer, et comment la remplir.

    Raises:
        FileNotFoundError: base absente.
    """
    chemin = _chemin_base()
    if not chemin.is_file():
        raise FileNotFoundError(
            f"base de prospection absente : {chemin}. La créer sur le poste : "
            "python prospection/scripts/import_prospects.py --input "
            "prospection/data/prospects_exemple.csv"
        )
    if lecture_seule:
        base = sqlite3.connect(f"file:{chemin.as_posix()}?mode=ro", uri=True)
    else:
        base = sqlite3.connect(str(chemin))
    base.row_factory = sqlite3.Row
    return base


def _refus(message: str, status_code: int = 503) -> Response:
    """Réponse d'indisponibilité, en JSON, sans mise en cache."""
    return JSONResponse(
        status_code=status_code, headers=BASE_HEADERS, content={"error": message}
    )


def _filtres(request: Request) -> dict:
    """Filtres acceptés en paramètres d'URL, dans une forme sûre pour SQL.

    Chaque filtre devient une valeur liée (`?`), jamais une concaténation : un
    paramètre d'URL ne peut pas devenir du SQL.
    """
    parametres = request.query_params
    try:
        limite = int(parametres.get("limite") or 200)
    except ValueError:
        limite = 200
    return {
        "secteur": (parametres.get("secteur") or "").strip(),
        "statut": (parametres.get("statut") or "").strip(),
        "recherche": (parametres.get("q") or "").strip(),
        "depuis": (parametres.get("depuis") or "").strip(),
        "limite": min(1000, max(1, limite)),
    }


def _lignes(filtres: dict) -> list[dict]:
    """Lit les fiches filtrées (colonnes explicites, valeurs liées)."""
    conditions: list[str] = []
    valeurs: list = []
    if filtres["secteur"]:
        conditions.append("secteur = ?")
        valeurs.append(filtres["secteur"])
    if filtres["statut"] in STATUTS:
        conditions.append("statut = ?")
        valeurs.append(filtres["statut"])
    if filtres["recherche"]:
        conditions.append("(nom_boutique LIKE ? OR email LIKE ? OR ville LIKE ?)")
        motif = f"%{filtres['recherche']}%"
        valeurs += [motif, motif, motif]
    if filtres["depuis"]:
        conditions.append("date_import >= ?")
        valeurs.append(filtres["depuis"])
    # Les fiches à traiter d'abord : une réponse qui attend coûte plus qu'un
    # prospect jamais écrit.
    requete = (
        f"SELECT {', '.join(COLONNES_VISIBLES)} FROM prospects"
        + (" WHERE " + " AND ".join(conditions) if conditions else "")
        + " ORDER BY CASE statut WHEN 'repondu' THEN 0 WHEN 'ouvert' THEN 1"
        " WHEN 'envoye' THEN 2 WHEN 'nouveau' THEN 3 ELSE 4 END,"
        " date_derniere_maj DESC LIMIT ?"
    )
    valeurs.append(filtres["limite"])
    base = _ouvrir()
    try:
        return [dict(ligne) for ligne in base.execute(requete, valeurs).fetchall()]
    finally:
        base.close()


def _stats() -> dict:
    """Compteurs et taux du CRM, calculés depuis la base (jamais depuis la page).

    Un taux sans dénominateur vaut `None` — affiché « — » côté page. Écrire 0 %
    pour « aucune donnée » ferait croire à un échec de campagne là où il n'y a
    encore rien à mesurer.
    """
    base = _ouvrir()
    try:
        statuts = {
            ligne["statut"]: ligne["nb"]
            for ligne in base.execute(
                "SELECT statut, COUNT(*) AS nb FROM prospects GROUP BY statut"
            )
        }
        jalons = {
            ligne["type"]: ligne["nb"]
            for ligne in base.execute("SELECT type, COUNT(*) AS nb FROM envois GROUP BY type")
        }
        revenu = float(
            base.execute(
                "SELECT COALESCE(SUM(revenu_usdc), 0) AS total FROM prospects"
            ).fetchone()["total"]
        )
    finally:
        base.close()
    envois = sum(jalons.get(nom, 0) for nom in ("intro", "relance_1", "relance_2"))
    reponses = jalons.get("reponse", 0)
    conversions = jalons.get("conversion", 0)

    def taux(numerateur: int) -> float | None:
        return round(100.0 * numerateur / envois, 1) if envois else None

    return {
        "prospects": sum(statuts.values()),
        "statuts": statuts,
        "jalons": jalons,
        "envois": envois,
        "reponses": reponses,
        "conversions": conversions,
        "revenu_usdc": revenu,
        "taux": {
            "ouverture": taux(jalons.get("ouverture", 0)),
            "reponse": taux(reponses),
            "conversion": taux(conversions),
            "conversion_sur_reponses": (
                round(100.0 * conversions / reponses, 1) if reponses else None
            ),
        },
    }


def _ecrire_statut(prospect_id: int, statut: str, note: str = "") -> dict:
    """Change le statut d'une fiche et consigne le fait correspondant.

    Le journal `envois` est alimenté en même temps que le statut : sans cela,
    une réponse marquée depuis le CRM serait invisible pour l'analyse, qui lit
    les jalons datés.

    Args:
        prospect_id: identifiant de la fiche.
        statut: statut cible, dans `STATUTS`.
        note: motif éventuel (ajouté aux notes, jamais substitué).

    Returns:
        La fiche mise à jour (colonnes de `COLONNES_VISIBLES`).

    Raises:
        ValueError: statut hors `STATUTS`, ou fiche inconnue.
    """
    if statut not in STATUTS:
        raise ValueError(f"statut inconnu : {statut}")
    base = _ouvrir(lecture_seule=False)
    try:
        ligne = base.execute("SELECT * FROM prospects WHERE id = ?", (int(prospect_id),)).fetchone()
        if ligne is None:
            raise ValueError(f"prospect inconnu : {prospect_id}")
        horodatage = _maintenant()
        champs = {"statut": statut, "date_derniere_maj": horodatage}
        if statut == "repondu" and not ligne["date_reponse"]:
            champs["date_reponse"] = horodatage
        if statut == "converti" and not ligne["date_reponse"]:
            champs["date_reponse"] = horodatage
        if note and note not in str(ligne["notes"]):
            champs["notes"] = (str(ligne["notes"]) + "; " + note).strip("; ")
        affectations = ", ".join(f"{colonne} = ?" for colonne in champs)
        base.execute(
            f"UPDATE prospects SET {affectations} WHERE id = ?",
            list(champs.values()) + [int(prospect_id)],
        )
        type_evenement = EVENEMENT_POUR.get(statut)
        if type_evenement:
            deja = base.execute(
                "SELECT 1 FROM envois WHERE prospect_id = ? AND type = ?"
                " AND substr(date_evenement, 1, 10) = ? LIMIT 1",
                (int(prospect_id), type_evenement, horodatage[:10]),
            ).fetchone()
            if deja is None:
                base.execute(
                    "INSERT INTO envois (prospect_id, date_evenement, type, objet,"
                    " variante, canal, detail) VALUES (?,?,?,?,?,?,?)",
                    (
                        int(prospect_id),
                        horodatage,
                        type_evenement,
                        str(ligne["objet_retenu"] or ""),
                        ligne["variante"],
                        "email",
                        "marque depuis le CRM",
                    ),
                )
        base.commit()
        mis_a_jour = base.execute(
            f"SELECT {', '.join(COLONNES_VISIBLES)} FROM prospects WHERE id = ?",
            (int(prospect_id),),
        ).fetchone()
        return dict(mis_a_jour)
    finally:
        base.close()


def _maintenant() -> str:
    """Horodatage ISO 8601 UTC, au même format que les scripts de prospection."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _garde(request: Request) -> Response | None:
    """Contrôle d'accès commun aux cinq routes, ou `None` si l'accès est permis.

    L'ordre compte : fermé par défaut (`503` sans mot de passe configuré), puis
    authentification (`401` + challenge), puis existence de la base (`503` avec
    la commande à lancer).
    """
    settings = get_settings()
    if not pilotage_ouvert(settings):
        return _refus(
            "DASHBOARD_PASSWORD absent : /prospection est fermé par défaut (même règle "
            "que /dashboard, D32)."
        )
    refus = refus_acces(request)
    if refus is not None:
        return refus
    if not _chemin_base().is_file():
        return _refus(str(_ouvrir_error()))
    return None


def _ouvrir_error() -> FileNotFoundError:
    """Erreur d'ouverture normalisée, pour un message uniforme entre les routes."""
    try:
        _ouvrir().close()
    except FileNotFoundError as erreur:
        return erreur
    return FileNotFoundError("base de prospection illisible")


#: La page complète est une chaîne littérale (pas de f-string) : les accolades du
#: CSS et du JavaScript restent intactes, seuls `__STATUTS__` (HTML) et
#: `__STATUTS_JS__` (script) sont remplacés.
PAGE = """<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Prospection — Address Validator</title>
<style>
  :root { color-scheme: dark; }
  body { margin: 24px; background: #0f1115; color: #e6e9ef;
         font-family: system-ui, "Segoe UI", Roboto, sans-serif; }
  h1 { font-size: 20px; margin: 0 0 4px; }
  h2 { font-size: 15px; margin: 22px 0 8px; }
  .muted { color: #9aa4b2; font-size: 12px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
          gap: 10px; margin-top: 14px; }
  .card { background: #171a21; border: 1px solid #242833; border-radius: 8px;
          padding: 10px 12px; }
  .card .k { font-size: 11px; text-transform: uppercase; letter-spacing: .04em;
             color: #9aa4b2; }
  .card .v { font-size: 20px; font-weight: 600; }
  .filtres { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
  input, select, button, .lien { background: #171a21; color: #e6e9ef;
        border: 1px solid #242833; border-radius: 6px; padding: 6px 8px;
        font-size: 13px; }
  button, .lien { cursor: pointer; text-decoration: none; }
  .lien { display: inline-block; }
  table { width: 100%; border-collapse: collapse; margin-top: 10px; }
  th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid #242833;
           font-size: 13px; vertical-align: top; }
  th { font-size: 11px; text-transform: uppercase; color: #9aa4b2; }
  td.num { text-align: right; }
  .badge { padding: 2px 6px; border-radius: 4px; background: #242833; font-size: 12px; }
  .st-repondu { background: #14532d; }
  .st-converti { background: #1e3a8a; }
  .st-refuse, .st-desinscrit { background: #7f1d1d; }
  .st-ouvert { background: #4a3a12; }
  footer { margin-top: 24px; }
</style>
</head>
<body>
<h1>Prospection — Address Validator</h1>
<p class="muted">Base locale de prospection (<code>prospection/data/prospects.db</code>).
Protégé par HTTP Basic Auth (<code>DASHBOARD_USER</code> / <code>DASHBOARD_PASSWORD</code>).
Aucune ressource externe : la page fonctionne hors ligne, sur le poste.</p>
<div class="grid" id="cartes"></div>
<h2>Filtrer</h2>
<div class="filtres">
  <input id="q" placeholder="boutique, email ou ville" size="24">
  <select id="secteur"><option value="">tous les secteurs</option></select>
  <select id="statut"><option value="">tous les statuts</option>__STATUTS__</select>
  <input id="depuis" placeholder="importé depuis (AAAA-MM-JJ)" size="20">
  <button id="actualiser">Actualiser</button>
  <a class="lien" id="export" href="/prospection/export.csv">Exporter en CSV</a>
</div>
<h2>Prospects</h2>
<table>
  <thead><tr><th>Boutique</th><th>Contact</th><th>Secteur</th><th>Statut</th>
  <th>Dates</th><th class="num">Revenu</th><th>Marquer</th></tr></thead>
  <tbody id="lignes"></tbody>
</table>
<p class="muted" id="pied"></p>
<footer class="muted">Rendu par <code>app/routes/prospection.py</code>. Les statuts
terminaux (<code>refuse</code>, <code>desinscrit</code>, <code>converti</code>) ne sont
plus jamais recontactés par les scripts de campagne.</footer>
<script>
"""

#: Script embarqué, concaténé à `PAGE` : il ne s'adresse qu'à ce même service
#: (`connect-src 'self'`), écrit le DOM par `textContent` (aucune injection HTML
#: possible depuis un nom de boutique) et n'utilise aucune bibliothèque externe.
SCRIPT = """
const $ = (id) => document.getElementById(id);
function parametresFiltres() {
  const p = new URLSearchParams();
  const q = $("q").value.trim(), s = $("secteur").value;
  const st = $("statut").value, d = $("depuis").value.trim();
  if (q) p.set("q", q);
  if (s) p.set("secteur", s);
  if (st) p.set("statut", st);
  if (d) p.set("depuis", d);
  return p;
}
function cellule(texte, classe) {
  const td = document.createElement("td");
  td.textContent = texte === null || texte === undefined ? "" : String(texte);
  if (classe) td.className = classe;
  return td;
}
function afficherStats(s) {
  const taux = (v) => (v === null || v === undefined ? "—" : v + " %");
  const cartes = [
    ["Prospects", s.prospects], ["Envois", s.envois], ["Réponses", s.reponses],
    ["Conversions", s.conversions], ["Revenu USDC", Number(s.revenu_usdc).toFixed(2)],
    ["Taux d'ouverture", taux(s.taux.ouverture)], ["Taux de réponse", taux(s.taux.reponse)],
    ["Conversion / réponses", taux(s.taux.conversion_sur_reponses)]
  ];
  const cible = $("cartes");
  cible.textContent = "";
  for (const paire of cartes) {
    const carte = document.createElement("div");
    carte.className = "card";
    const titre = document.createElement("div");
    titre.className = "k";
    titre.textContent = paire[0];
    const valeur = document.createElement("div");
    valeur.className = "v";
    valeur.textContent = String(paire[1]);
    carte.append(titre, valeur);
    cible.append(carte);
  }
}
function optionsSecteurs(prospects) {
  const secteurs = [...new Set(prospects.map((p) => p.secteur).filter(Boolean))].sort();
  const select = $("secteur");
  const choix = select.value;
  select.textContent = "";
  const vide = document.createElement("option");
  vide.value = "";
  vide.textContent = "tous les secteurs";
  select.append(vide);
  for (const secteur of secteurs) {
    const option = document.createElement("option");
    option.value = secteur;
    option.textContent = secteur;
    select.append(option);
  }
  select.value = choix;
}
function afficherLignes(prospects) {
  const corps = $("lignes");
  corps.textContent = "";
  for (const p of prospects) {
    const ligne = document.createElement("tr");
    ligne.append(cellule(p.nom_boutique));
    ligne.append(cellule([p.prenom, p.nom].filter(Boolean).join(" ") + " <" + p.email + ">"));
    ligne.append(cellule(p.secteur));
    const cellStatut = document.createElement("td");
    const badge = document.createElement("span");
    badge.className = "badge st-" + p.statut;
    badge.textContent = p.statut;
    cellStatut.append(badge);
    ligne.append(cellStatut);
    ligne.append(cellule("import " + (p.date_import || "—").slice(0, 10)
      + (p.date_envoi ? " · envoi " + p.date_envoi.slice(0, 10) : "")
      + (p.date_reponse ? " · réponse " + p.date_reponse.slice(0, 10) : "")));
    ligne.append(cellule(Number(p.revenu_usdc || 0).toFixed(2), "num"));
    const cellAction = document.createElement("td");
    const select = document.createElement("select");
    for (const statut of __STATUTS_JS__) {
      const option = document.createElement("option");
      option.value = statut;
      option.textContent = statut;
      if (statut === p.statut) { option.selected = true; }
      select.append(option);
    }
    const bouton = document.createElement("button");
    bouton.textContent = "Enregistrer";
    bouton.onclick = () => marquer(p.id, select.value);
    cellAction.append(select, bouton);
    ligne.append(cellAction);
    corps.append(ligne);
  }
  $("pied").textContent = prospects.length + " fiche(s) affichée(s).";
}
async function charger() {
  const p = parametresFiltres();
  $("export").href = "/prospection/export.csv?" + p.toString();
  try {
    const reponses = await Promise.all([
      fetch("/prospection/api/stats").then((r) => r.ok ? r.json() : Promise.reject(r.status)),
      fetch("/prospection/api/prospects?" + p.toString())
        .then((r) => r.ok ? r.json() : Promise.reject(r.status))
    ]);
    afficherStats(reponses[0]);
    optionsSecteurs(reponses[1].prospects);
    afficherLignes(reponses[1].prospects);
  } catch (erreur) {
    $("pied").textContent = "Lecture impossible (" + erreur + ").";
  }
}
async function marquer(id, statut) {
  const reponse = await fetch("/prospection/api/prospects/" + id + "/statut", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ statut: statut })
  });
  if (!reponse.ok) { alert("Refus du serveur : " + reponse.status); }
  charger();
}
$("actualiser").onclick = charger;
$("q").onkeydown = (evenement) => { if (evenement.key === "Enter") { charger(); } };
$("depuis").onkeydown = (evenement) => { if (evenement.key === "Enter") { charger(); } };
$("secteur").onchange = charger;
$("statut").onchange = charger;
charger();
"""

#: Fin du document.
FIN = """
</script>
</body>
</html>
"""


def _page() -> str:
    """Assemble la page, avec les statuts injectés côté HTML et côté script."""
    options = "".join(f'<option value="{statut}">{statut}</option>' for statut in STATUTS)
    liste_js = "[" + ", ".join(f'"{statut}"' for statut in STATUTS) + "]"
    return (
        PAGE.replace("__STATUTS__", options)
        + SCRIPT.replace("__STATUTS_JS__", liste_js)
        + FIN
    )


# --- Routes -----------------------------------------------------------------

#: Plafond de l'export CSV : au-delà, mieux vaut filtrer que produire un fichier
#: tronqué en silence (un export incomplet se voit trois semaines plus tard).
PLAFOND_EXPORT = 5000


@router.get("/prospection")
def prospection_page(request: Request) -> Response:
    """Page du CRM : fermée par défaut, autonome, sans donnée côté serveur."""
    refus = _garde(request)
    if refus is not None:
        return refus
    return HTMLResponse(content=_page(), status_code=200, headers=PAGE_HEADERS)


@router.get("/prospection/api/prospects")
def prospection_prospects(request: Request) -> Response:
    """Liste filtrée des fiches (JSON)."""
    refus = _garde(request)
    if refus is not None:
        return refus
    filtres = _filtres(request)
    try:
        lignes = _lignes(filtres)
    except sqlite3.Error as erreur:
        return _refus(f"lecture impossible de la base de prospection : {erreur}", 500)
    return JSONResponse(
        status_code=200,
        headers=BASE_HEADERS,
        content={"prospects": lignes, "filtres": filtres},
    )


@router.get("/prospection/api/stats")
def prospection_stats(request: Request) -> Response:
    """Compteurs et taux (JSON) — les taux sans dénominateur valent `null`."""
    refus = _garde(request)
    if refus is not None:
        return refus
    try:
        charge = _stats()
    except sqlite3.Error as erreur:
        return _refus(f"lecture impossible de la base de prospection : {erreur}", 500)
    charge["generated_at"] = _maintenant()
    return JSONResponse(status_code=200, headers=BASE_HEADERS, content=charge)


@router.post("/prospection/api/prospects/{prospect_id}/statut")
def prospection_statut(
    prospect_id: int, request: Request, charge: dict = Body(...)
) -> Response:
    """Change le statut d'une fiche et consigne le fait dans le journal.

    Seuls le statut et une note libre sont acceptés : cette route ne permet pas
    de modifier l'adresse électronique ou le revenu d'un prospect depuis un
    navigateur.
    """
    refus = _garde(request)
    if refus is not None:
        return refus
    if not isinstance(charge, dict):
        return _refus('corps JSON attendu : {"statut": "repondu"}', 400)
    try:
        ligne = _ecrire_statut(
            prospect_id, str(charge.get("statut") or ""), str(charge.get("note") or "")
        )
    except ValueError as erreur:
        return _refus(str(erreur), 400)
    except sqlite3.Error as erreur:
        return _refus(f"écriture impossible dans la base de prospection : {erreur}", 500)
    return JSONResponse(status_code=200, headers=BASE_HEADERS, content={"prospect": ligne})


@router.get("/prospection/export.csv")
def prospection_export(request: Request) -> Response:
    """Export CSV de la sélection courante (filtres appliqués, plafond annoncé)."""
    refus = _garde(request)
    if refus is not None:
        return refus
    filtres = _filtres(request)
    filtres["limite"] = PLAFOND_EXPORT + 1
    try:
        lignes = _lignes(filtres)
    except sqlite3.Error as erreur:
        return _refus(f"lecture impossible de la base de prospection : {erreur}", 500)
    if len(lignes) > PLAFOND_EXPORT:
        return _refus(
            f"plus de {PLAFOND_EXPORT} fiches correspondent à ces filtres : affinez la "
            "sélection (secteur, statut, date) — un export tronqué serait trompeur.",
            400,
        )
    tampon = io.StringIO()
    ecrivain = csv.DictWriter(
        tampon, fieldnames=list(COLONNES_VISIBLES), extrasaction="ignore"
    )
    ecrivain.writeheader()
    for ligne in lignes:
        ecrivain.writerow(ligne)
    entetes = {
        **BASE_HEADERS,
        "Content-Type": "text/csv; charset=utf-8",
        "Content-Disposition": 'attachment; filename="prospects.csv"',
    }
    return Response(
        content=tampon.getvalue().encode("utf-8"), status_code=200, headers=entetes
    )

