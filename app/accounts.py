"""Comptes clients : mots de passe, clés API, quotas, estimation d'économies.

Décision D44. Ce module ne connaît **pas** le HTTP : il tient les règles, la
route ne fait que les présenter. Trois règles méritent d'être dites ici, parce
qu'elles sont des choix et pas des détails d'implémentation.

1. **Le mot de passe est haché avec bcrypt** (coût 12), jamais stocké ni
   journalisé. Au-delà de 72 octets, bcrypt **ignore la fin** du mot de passe :
   accepter silencieusement un mot de passe de 80 caractères reviendrait à dire
   au client qu'il en a un plus long qu'en réalité. On refuse donc, plutôt que de
   tronquer en silence.
2. **La clé API n'est jamais stockée en clair.** Elle est générée avec
   `secrets` (`sk_live_` + 32 caractères aléatoires) et la base ne garde que son
   empreinte SHA-256 et un préfixe affichable (`sk_live_ab12`). C'est la seule
   façon de pouvoir dire au client, sans mentir, que personne — pas même
   l'exploitant — ne peut relire sa clé : elle est affichée une fois, et une clé
   perdue se **régénère**. Le corollaire est assumé : une clé ne se « retrouve »
   pas, elle se remplace.
3. **L'estimation d'économies est une estimation**, montrée avec sa formule :
   `adresses invalides détectées × COUT_ECHEC_USD`, où `COUT_ECHEC_USD` est la
   **borne basse** de la fourchette publiée sur `/pricing` (17 à 30 $). Un chiffre
   d'économies sans formule serait un argument commercial, pas un calcul.
"""

from __future__ import annotations

import hashlib
import re
import secrets
import string
from datetime import datetime, timedelta, timezone
from functools import lru_cache

import bcrypt
from sqlalchemy import case, func

from app.models import Customer, CustomerSession, CustomerValidation, PasswordReset

#: Format public d'une clé : `sk_live_` + 32 caractères (exigence de la spec).
API_KEY_PREFIX = "sk_live_"
API_KEY_CHARS = 32

#: Longueur minimale exigée par la spec (12 caractères).
PASSWORD_MIN_LENGTH = 12
#: bcrypt s'arrête à 72 octets : on refuse au-delà au lieu de tronquer en silence.
PASSWORD_MAX_BYTES = 72
#: Coût bcrypt. 12 = ~0,25 s par hachage sur le VPS : lent pour un attaquant,
#: invisible pour une inscription.
BCRYPT_ROUNDS = 12

#: Plateformes proposées à l'inscription (spec : Shopify, WooCommerce, autre).
PLATEFORMES = ("shopify", "woocommerce", "autre")

#: Quotas mensuels par plan (`None` = illimité). Les chiffres sont ceux publiés
#: sur `/pricing` : une offre qui ne serait pas appliquée ici serait un mensonge.
PLANS: dict[str, int | None] = {
    "trial": 100,
    "starter": 2000,
    "pro": 10000,
    "enterprise": None,
}
PLAN_LIBELLES = {
    "trial": "Essai gratuit",
    "starter": "Starter",
    "pro": "Pro",
    "enterprise": "Enterprise",
}
PLAN_PAR_DEFAUT = "trial"
JOURS_ESSAI = 14
VALIDATIONS_ESSAI = 100

#: Les offres, en euros : la **vérité de facturation** (D57). `app/i18n.py` en tire
#: le prix de chaque langue et le prix unitaire (prix ÷ quota), la porte D45
#: vérifie que les quotas sont ceux de `PLANS`. Le texte des cartes vit dans les
#: gabarits de `/pricing` ; `periode` est la portée du quota telle que la page
#: française l'écrit. Elles vivent ici, pas dans la route : `i18n` les lit, et la
#: page des tarifs lit `i18n` (D60).
OFFRES = (
    {"nom": "Essai gratuit", "prix": "0 €", "prix_eur": None, "validations": 100, "periode": "au total"},
    {"nom": "Starter", "prix": "29 €", "prix_eur": 29.0, "validations": 2000, "periode": "par mois"},
    {"nom": "Pro", "prix": "79 €", "prix_eur": 79.0, "validations": 10000, "periode": "par mois"},
    {
        "nom": "Enterprise",
        "prix": "Sur devis",
        "prix_eur": None,
        "validations": None,
        "periode": "sans plafond mensuel",
    },
)

#: D91 : montant d'un paiement Stripe (centimes d'euro) -> plan. Les liens de paiement
#: facturent exactement le prix des offres, sans taxe (franchise en base) : un
#: montant inconnu ne change aucun plan.
PLAN_PAR_MONTANT = {round(offre["prix_eur"] * 100): offre["nom"].lower() for offre in OFFRES if offre["prix_eur"]}

#: Coût évité par adresse invalide détectée : borne basse de la fourchette
#: publiée sur `/pricing` (17 à 30 $). Borne basse = estimation prudente.
COUT_ECHEC_USD = 17.0

_EMAIL = re.compile(r"^[^@\s]+@[^@\s.]+(\.[^@\s.]+)+$")


def hash_password(password: str) -> str:
    """Empreinte bcrypt (sel aléatoire inclus) prête à stocker."""
    return bcrypt.hashpw(
        password.encode("utf-8"), bcrypt.gensalt(rounds=BCRYPT_ROUNDS)
    ).decode("ascii")


def verify_password(password: str, empreinte: str) -> bool:
    """Vérifie un mot de passe. Toute empreinte illisible est un refus, pas une erreur."""
    try:
        return bcrypt.checkpw(password.encode("utf-8"), empreinte.encode("ascii"))
    except (ValueError, TypeError):
        return False


def password_problem(password: str) -> str | None:
    """Message d'erreur si le mot de passe est refusé, `None` s'il convient."""
    if len(password) < PASSWORD_MIN_LENGTH:
        return f"Le mot de passe doit compter au moins {PASSWORD_MIN_LENGTH} caractères."
    if len(password.encode("utf-8")) > PASSWORD_MAX_BYTES:
        return (
            f"Le mot de passe dépasse {PASSWORD_MAX_BYTES} octets (au-delà, bcrypt "
            "ignorerait la fin : mieux vaut un mot de passe plus court)."
        )
    return None


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def valid_email(email: str) -> bool:
    """Validation légère (forme) : la délivrabilité n'est pas prouvée ici."""
    return bool(_EMAIL.match(normalize_email(email)))


def api_key_hash(cle: str) -> str:
    """Empreinte stockée d'une clé (index de recherche, non réversible)."""
    return hashlib.sha256(cle.strip().encode("utf-8")).hexdigest()


def api_key_prefix(cle: str) -> str:
    """Préfixe affichable : de quoi reconnaître sa clé sans la révéler."""
    return cle.strip()[: len(API_KEY_PREFIX) + 4]


def new_api_key() -> tuple[str, str, str]:
    """Renvoie `(clé en clair, empreinte, préfixe)`. La clé n'existe qu'ici."""
    alphabet = string.ascii_letters + string.digits
    secret = "".join(secrets.choice(alphabet) for _ in range(API_KEY_CHARS))
    cle = f"{API_KEY_PREFIX}{secret}"
    return cle, api_key_hash(cle), api_key_prefix(cle)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def trial_end(depart: datetime | None = None, jours: int = JOURS_ESSAI) -> datetime:
    return (depart or now_utc()) + timedelta(days=jours)


def quota_for(plan: str) -> int | None:
    """Quota du plan (`None` = illimité). Un plan inconnu retombe sur l'essai."""
    return PLANS.get(plan, PLANS[PLAN_PAR_DEFAUT])


def month_start(moment: datetime | None = None) -> datetime:
    """Premier instant du mois calendaire en cours (UTC).

    C'est la borne du quota (D45) : `/pricing` publie des quotas **mensuels**,
    donc le droit d'être servi se rejoue chaque mois. Le mois est calendaire en
    UTC ; l'écart avec La Réunion (UTC+4) vaut au plus quelques heures après le
    1ᵉʳ du mois — assumé, et dit plutôt que caché.
    """
    return (_conscient(moment or now_utc())).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )


def savings_usd(invalides: int) -> float:
    """Estimation **avec sa formule** : invalides × borne basse du coût d'échec."""
    return round(max(0, int(invalides)) * COUT_ECHEC_USD, 2)


# --------------------------------------------------------------------------- #
# Accès base : les seules fonctions qui touchent les tables (D44)
# --------------------------------------------------------------------------- #


@lru_cache(maxsize=1)
def _empreinte_factice() -> str:
    """Empreinte d'un mot de passe bidon, comparée quand l'email est inconnu.

    Sans elle, une connexion sur un email inexistant répondrait instantanément
    là où un email connu paie le coût bcrypt : le temps de réponse dirait qui a
    un compte. Calculée une seule fois (mémoïsée), elle ne coûte rien au
    démarrage.
    """
    return hash_password("mot-de-passe-factice-pour-egaliser-le-temps")


def create_customer(
    session,
    *,
    email: str,
    password: str,
    shop_name: str,
    platform: str,
    plan: str = PLAN_PAR_DEFAUT,
    maintenant: datetime | None = None,
    jours_essai: int = JOURS_ESSAI,
) -> tuple[Customer, str]:
    """Crée un compte et renvoie `(client, clé en clair)`.

    La clé en clair n'existe que dans la valeur de retour : elle n'est ni
    stockée, ni journalisée. `IntegrityError` (email ou empreinte déjà prise)
    remonte à l'appelant, qui décide du message à afficher.
    """
    cle, empreinte, prefixe = new_api_key()
    client = Customer(
        email=normalize_email(email),
        password_hash=hash_password(password),
        shop_name=shop_name.strip(),
        platform=platform,
        api_key_hash=empreinte,
        api_key_prefix=prefixe,
        plan=plan,
        trial_ends_at=trial_end(maintenant, jours_essai),
    )
    session.add(client)
    session.commit()
    return client, cle


def find_by_email(session, email: str) -> Customer | None:
    adresse = normalize_email(email)
    return session.query(Customer).filter(Customer.email == adresse).one_or_none()


def find_by_api_key(session, cle: str) -> Customer | None:
    """Retrouve un compte par sa clé API (l'empreinte est l'index de recherche)."""
    if not cle or not cle.strip():
        return None
    return (
        session.query(Customer)
        .filter(Customer.api_key_hash == api_key_hash(cle))
        .one_or_none()
    )


def authenticate(session, email: str, password: str) -> Customer | None:
    """Vérifie un couple email/mot de passe (bcrypt). `None` = refus."""
    client = find_by_email(session, email)
    if client is None:
        verify_password(password or "", _empreinte_factice())
        return None
    if not verify_password(password or "", client.password_hash):
        return None
    return client


def _sans_fuseau(moment: datetime) -> datetime:
    """La base garde des `TIMESTAMP` sans fuseau, en UTC."""
    return moment.astimezone(timezone.utc).replace(tzinfo=None) if moment.tzinfo else moment


def open_session(session, client: Customer, maintenant: datetime, heures: int) -> str:
    """Ouvre une session : renvoie le jeton du cookie, dont seule l'empreinte est gardée.

    Les sessions échues de tous les comptes sont effacées au passage : une ligne
    ne survit pas à sa durée (PRIVACY.md §3).
    """
    # ponytail: purge à chaque connexion, sans tâche planifiée ; suffit tant que les connexions se comptent en milliers par jour.
    session.query(CustomerSession).filter(CustomerSession.expires_at <= _sans_fuseau(maintenant)).delete()
    jeton = secrets.token_urlsafe(32)
    session.add(
        CustomerSession(
            customer_id=client.id,
            token_hash=hashlib.sha256(jeton.encode("ascii")).hexdigest(),
            expires_at=_sans_fuseau(maintenant + timedelta(hours=heures)),
        )
    )
    session.commit()
    return jeton


def session_customer(session, jeton: str, maintenant: datetime) -> Customer | None:
    """Le compte d'une session encore valable, sinon `None` (session échue effacée)."""
    if not jeton or len(jeton) > 128:
        return None
    empreinte = hashlib.sha256(jeton.encode("utf-8", "replace")).hexdigest()
    ligne = session.query(CustomerSession).filter(CustomerSession.token_hash == empreinte).one_or_none()
    if ligne is None:
        return None
    if ligne.expires_at <= _sans_fuseau(maintenant):
        session.delete(ligne)
        session.commit()
        return None
    return session.get(Customer, ligne.customer_id)


def close_session(session, jeton: str) -> None:
    """Ferme une session (déconnexion) : sa ligne disparaît, le cookie ne vaut plus rien."""
    if not jeton:
        return
    empreinte = hashlib.sha256(jeton.encode("utf-8", "replace")).hexdigest()
    session.query(CustomerSession).filter(CustomerSession.token_hash == empreinte).delete()
    session.commit()


def close_all_sessions(session, client: Customer) -> None:
    """Ferme toutes les sessions d'un compte (nouveau mot de passe : partout déconnecté)."""
    session.query(CustomerSession).filter(CustomerSession.customer_id == client.id).delete()
    session.commit()


def _empreinte_jeton(jeton: str) -> str:
    return hashlib.sha256(jeton.encode("utf-8", "replace")).hexdigest()


def create_password_reset(session, client: Customer, maintenant: datetime, minutes: int) -> str:
    """Crée un lien de mot de passe oublié : renvoie le jeton (seule l'empreinte est gardée).

    Les liens échus, de tous les comptes, sont effacés au passage (PRIVACY.md §3).
    """
    session.query(PasswordReset).filter(PasswordReset.expires_at <= _sans_fuseau(maintenant)).delete()
    jeton = secrets.token_urlsafe(32)
    session.add(
        PasswordReset(
            customer_id=client.id,
            token_hash=_empreinte_jeton(jeton),
            expires_at=_sans_fuseau(maintenant + timedelta(minutes=minutes)),
        )
    )
    session.commit()
    return jeton


def password_reset_customer(session, jeton: str, maintenant: datetime) -> tuple[PasswordReset, Customer] | None:
    """(lien, compte) si le lien existe, n'a pas servi et n'est pas échu ; sinon `None`."""
    if not jeton or len(jeton) > 128:
        return None
    ligne = session.query(PasswordReset).filter(PasswordReset.token_hash == _empreinte_jeton(jeton)).one_or_none()
    if ligne is None or ligne.used_at is not None or ligne.expires_at <= _sans_fuseau(maintenant):
        return None
    client = session.get(Customer, ligne.customer_id)
    return (ligne, client) if client is not None else None


def finish_password_reset(session, ligne: PasswordReset, client: Customer, mot_de_passe: str, maintenant: datetime) -> None:
    """Nouveau mot de passe : le lien est consommé, les autres liens et toutes les sessions tombent."""
    client.password_hash = hash_password(mot_de_passe)
    ligne.used_at = _sans_fuseau(maintenant)
    session.query(PasswordReset).filter(
        PasswordReset.customer_id == client.id, PasswordReset.id != ligne.id
    ).delete()
    session.query(CustomerSession).filter(CustomerSession.customer_id == client.id).delete()
    session.commit()


def activer_abonnement(session, client_id: int, montant: int, stripe_customer: str) -> Customer | None:
    """D91 : un paiement Stripe réussi passe le compte au plan payé. None si rien ne correspond."""
    plan = PLAN_PAR_MONTANT.get(montant)
    client = session.get(Customer, client_id) if plan else None
    if client is None:
        return None
    client.plan = plan
    client.stripe_customer = stripe_customer or client.stripe_customer
    session.commit()
    return client


def resilier_abonnement(session, stripe_customer: str, maintenant: datetime | None = None) -> Customer | None:
    """D91 : Stripe a terminé l'abonnement : retour à l'essai, déjà échu, donc la clé n'est plus servie."""
    if not stripe_customer:
        return None
    client = session.query(Customer).filter_by(stripe_customer=stripe_customer).first()
    if client is None:
        return None
    client.plan = PLAN_PAR_DEFAUT
    moment = _naif_utc(maintenant or now_utc())
    if client.trial_ends_at is None or client.trial_ends_at > moment:
        client.trial_ends_at = moment
    session.commit()
    return client


def regenerate_api_key(session, client: Customer) -> str:
    """Remplace la clé : renvoie la nouvelle (en clair), qui ne sera plus montrée."""
    cle, empreinte, prefixe = new_api_key()
    client.api_key_hash = empreinte
    client.api_key_prefix = prefixe
    session.commit()
    return cle


def _conscient(moment: datetime | None) -> datetime | None:
    """Horodatage conscient en UTC (`TIMESTAMP` sans fuseau en base)."""
    if moment is None:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _naif_utc(moment: datetime) -> datetime:
    """Moment conscient (UTC) ramené à la convention des colonnes `TIMESTAMP`.

    Les horodatages sont écrits **sans fuseau**, en UTC (`_conscient` les relit
    dans cette convention). Comparer une borne consciente à une colonne sans
    fuseau reviendrait, sur SQLite, à comparer deux formats de chaînes différents
    — et sur PostgreSQL à dépendre du fuseau du serveur. On ramène donc la borne
    dans la convention de la donnée.
    """
    return _conscient(moment).replace(tzinfo=None)


def consumption(
    session, client: Customer, since: datetime | None = None
) -> dict:
    """Ce que le compte a consommé : validations, invalides, économies estimées.

    `since` (D45) borne le comptage à une fenêtre : le quota publié sur
    `/pricing` est **mensuel** (« 2 000 validations/mois »), donc ce qui décide du
    service est le mois en cours, pas la vie du compte. Un compteur à vie
    fermerait la porte pour toujours à un client en règle au mois suivant — une
    offre annoncée qui ne serait pas appliquée est un mensonge, et l'inverse
    (appliquer plus dur que l'offre) en est un aussi.
    """
    base = session.query(CustomerValidation).filter(
        CustomerValidation.customer_id == client.id
    )
    if since is not None:
        base = base.filter(CustomerValidation.created_at >= _naif_utc(since))
    validations = base.count()
    invalides = base.filter(CustomerValidation.validated.is_(False)).count()
    return {
        "validations": int(validations),
        "invalides": int(invalides),
        "economies_usd": savings_usd(invalides),
    }


def plan_status(session, client: Customer, maintenant: datetime | None = None) -> dict:
    """État du plan : quota, consommation, reste, fin d'essai. Rien d'inventé."""
    moment = maintenant or now_utc()
    quota = quota_for(client.plan)
    conso = consumption(session, client)
    conso_mois = consumption(session, client, since=month_start(moment))
    fin_essai = _conscient(client.trial_ends_at)
    essai_passe = bool(fin_essai is not None and fin_essai <= moment)
    return {
        "plan": client.plan,
        "plan_libelle": PLAN_LIBELLES.get(client.plan, client.plan),
        "quota": quota,
        # Consommation à vie : c'est la statistique montrée au client.
        "validations": conso["validations"],
        "invalides": conso["invalides"],
        "economies_usd": conso["economies_usd"],
        # Consommation du mois : c'est elle qui décide du service (D45), parce
        # que le quota publié est mensuel. Les deux chiffres coexistent : le
        # client voit son total, la règle travaille sur la fenêtre.
        "validations_mois": conso_mois["validations"],
        "restant": None if quota is None else max(0, quota - conso_mois["validations"]),
        "trial_ends_at": fin_essai,
        # `trial_expire` est le **fait** (la date est derrière) ; `expire` est la
        # **conséquence** (le compte n'est plus servi par sa clé). Un client passé
        # au plan payant n'est plus bloqué par une date d'essai dépassée : le
        # fait reste vrai, la conséquence n'en découle plus.
        "trial_expire": essai_passe,
        "expire": bool(client.plan == PLAN_PAR_DEFAUT and essai_passe),
        "trial_jours_restants": (
            None if fin_essai is None else max(0, (fin_essai - moment).days)
        ),
        "quota_atteint": quota is not None and conso_mois["validations"] >= quota,
        "cout_par_echec_usd": COUT_ECHEC_USD,
    }


def daily_usage(session, client: Customer, maintenant: datetime | None = None) -> list[tuple[int, int]]:
    """(validations, invalides) de chaque jour du mois en cours (UTC), du 1er à aujourd'hui.

    Source des courbes du tableau de bord (D64) : la même table que les compteurs
    (`customer_validations`), regroupée par jour, donc la somme des jours vaut le
    compteur du mois.
    """
    moment = _conscient(maintenant or now_utc())
    debut = month_start(moment)
    jour = func.date(CustomerValidation.created_at)
    lignes = (
        session.query(jour, func.count(), func.sum(case((CustomerValidation.validated.is_(False), 1), else_=0)))
        .filter(CustomerValidation.customer_id == client.id, CustomerValidation.created_at >= _naif_utc(debut))
        .group_by(jour)
        .all()
    )
    par_jour = {str(date)[:10]: (int(n), int(invalides or 0)) for date, n, invalides in lignes}
    return [par_jour.get(f"{debut:%Y-%m}-{numero:02d}", (0, 0)) for numero in range(1, moment.day + 1)]


def record_validation(
    session, client_id: int, cache_key: str | None, validated: bool
) -> None:
    """Inscrit une validation servie à un compte (source des statistiques)."""
    session.add(
        CustomerValidation(
            customer_id=client_id, cache_key=cache_key, validated=bool(validated)
        )
    )
    session.commit()


# --------------------------------------------------------------------------- #
# Autorisation par clé API (D45) : les règles qui décident de servir
# --------------------------------------------------------------------------- #

#: Motifs de refus, rendus tels quels à l'appelant, qui les traduit en codes
#: HTTP et en messages. Ce module ne connaît pas le HTTP : il dit *pourquoi*, la
#: route dit *comment*. Un motif et un seul par refus : « essai terminé » et
#: « quota atteint » ne veulent pas dire la même chose au client.
REFUS_CLE_INCONNUE = "cle_inconnue"
REFUS_COMPTE_EXPIRE = "compte_expire"
REFUS_QUOTA_ATTEINT = "quota_atteint"


def refusal_reason(
    session, client: Customer, maintenant: datetime | None = None
) -> str | None:
    """Pourquoi ce compte ne peut pas être servi (`None` = il peut l'être).

    Trois issues, dans cet ordre :

      * `REFUS_COMPTE_EXPIRE` — l'essai est terminé **et** le compte est encore
        sur le plan d'essai. La date ne ferme la porte qu'à l'essai : bloquer un
        client passé au plan payant parce qu'une date d'essai est derrière lui
        serait fermer la porte à quelqu'un qui paie.
      * `REFUS_QUOTA_ATTEINT` — le quota du mois est consommé.
      * `None` — servi.

    L'ordre compte : un compte expiré **et** au quota reçoit « essai terminé »,
    qui est la raison actionnable, plutôt que « quota atteint », qui n'explique
    rien quand il n'y a plus d'essai.

    Limite assumée et dite : l'expiration d'un **plan payant** (fin
    d'abonnement) n'existe pas encore, faute de facturation automatique — un plan
    accordé à la main reste ouvert jusqu'à ce qu'il soit retiré à la main. Seul
    le quota mensuel le referme. C'est un point ouvert, pas une règle cachée.
    """
    statut = plan_status(session, client, maintenant)
    if statut["expire"]:
        return REFUS_COMPTE_EXPIRE
    if statut["quota_atteint"]:
        return REFUS_QUOTA_ATTEINT
    return None


def authorize_api_key(
    session, cle: str, maintenant: datetime | None = None
) -> tuple[Customer | None, str | None]:
    """Authentifie une clé et dit si le compte peut être servi.

    Renvoie `(client, motif)`, et **un seul des deux** est renseigné : une clé
    inconnue ne renvoie aucun compte, pour qu'aucun appelant ne puisse servir par
    inadvertance après un refus. La clé présentée n'est ni journalisée ni
    comparée en clair : la recherche se fait par empreinte SHA-256, et le message
    est le même que la clé ait existé ou non (même règle que D44 pour les
    connexions : le refus ne renseigne pas sur l'existence d'un secret).
    """
    if not cle or not cle.strip():
        return None, REFUS_CLE_INCONNUE
    client = find_by_api_key(session, cle)
    if client is None:
        return None, REFUS_CLE_INCONNUE
    return client, refusal_reason(session, client, maintenant)
