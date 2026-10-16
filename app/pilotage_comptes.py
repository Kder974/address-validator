"""Comptes du tableau de bord d'exploitation (D74) : un compte par personne, son mot de passe à elle.

Avant D74, toutes les personnes de l'exploitation partageaient un identifiant et un code
(`DASHBOARD_USER` / `DASHBOARD_PASSWORD`), créé sur le serveur au Terminal. Désormais :

* **seules les adresses de `PILOTAGE_COMPTES`** (« Nom=email », dans le `.env` : le dépôt est
  public, aucune adresse n'est écrite dans le code) peuvent avoir un compte ;
* la personne demande un lien sur `/dashboard/mot-de-passe` ; le lien part par mail (le SMTP de
  D63), vaut `PASSWORD_RESET_MINUTES` minutes et ne sert qu'une fois ; la base n'en garde que
  l'empreinte SHA-256 ;
* elle choisit son mot de passe (mêmes règles que les clients, D44) ; la base n'en garde que
  l'empreinte bcrypt ;
* la réponse est la même que l'adresse soit autorisée ou non, et trois liens par compte et par
  heure au plus : la page ne dit pas qui a un compte.

L'identifiant partagé reste accepté tant qu'il est configuré, pour que personne ne soit enfermé
dehors pendant la transition.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select

from app import accounts
from app.config import get_settings
from app.models import Operator, OperatorToken

#: Liens par compte et par heure au plus.
LIENS_PAR_HEURE = 3


def maintenant() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def autorises(reglages=None) -> dict[str, str]:
    """{email en minuscules: nom} lu dans `PILOTAGE_COMPTES` (« Fzed=a@b.re, Kder=c@d.re »)."""
    comptes = {}
    for morceau in (reglages or get_settings()).pilotage_comptes.split(","):
        nom, _, email = morceau.partition("=")
        email = accounts.normalize_email(email)
        if nom.strip() and "@" in email:
            comptes[email] = nom.strip()[:60]
    return comptes


def _empreinte(jeton: str) -> str:
    return hashlib.sha256(jeton.encode("utf-8")).hexdigest()


def creer_lien(session, email: str, moment: datetime | None = None, reglages=None) -> tuple[str, str] | None:
    """(jeton, nom) pour une adresse autorisée et pas trop sollicitée ; sinon `None`, sans rien dire."""
    reglages = reglages or get_settings()
    moment = moment or maintenant()
    email = accounts.normalize_email(email)
    nom = autorises(reglages).get(email)
    if nom is None:
        return None
    operateur = session.scalar(select(Operator).where(Operator.email == email))
    if operateur is None:
        operateur = Operator(email=email, name=nom, created_at=moment)
        session.add(operateur)
        session.flush()
    operateur.name = nom
    session.execute(delete(OperatorToken).where(OperatorToken.expires_at < moment))
    recents = session.scalar(select(func.count()).select_from(OperatorToken).where(
        OperatorToken.operator_id == operateur.id, OperatorToken.created_at >= moment - timedelta(hours=1))) or 0
    if recents >= LIENS_PAR_HEURE:
        session.commit()
        return None
    jeton = secrets.token_urlsafe(32)
    session.add(OperatorToken(operator_id=operateur.id, token_hash=_empreinte(jeton), created_at=moment,
                              expires_at=moment + timedelta(minutes=reglages.password_reset_minutes)))
    session.commit()
    return jeton, nom


def operateur_du_lien(session, jeton: str, moment: datetime | None = None) -> Operator | None:
    moment = moment or maintenant()
    ligne = session.scalar(select(OperatorToken).where(OperatorToken.token_hash == _empreinte(jeton or "")))
    if ligne is None or ligne.used_at is not None or ligne.expires_at < moment:
        return None
    operateur = session.get(Operator, ligne.operator_id)
    # Une adresse retirée de PILOTAGE_COMPTES ne peut plus rien faire, même avec un lien.
    return operateur if operateur and operateur.email in autorises() else None


def definir_mot_de_passe(session, jeton: str, mot_de_passe: str, moment: datetime | None = None) -> Operator | None:
    """Pose l'empreinte bcrypt et consomme le lien ; tous les autres liens du compte sont effacés."""
    moment = moment or maintenant()
    operateur = operateur_du_lien(session, jeton, moment)
    if operateur is None:
        return None
    operateur.password_hash = accounts.hash_password(mot_de_passe)
    operateur.password_changed_at = moment
    session.execute(delete(OperatorToken).where(OperatorToken.operator_id == operateur.id))
    session.commit()
    return operateur


def authentifier(session, email: str, mot_de_passe: str) -> Operator | None:
    email = accounts.normalize_email(email)
    operateur = session.scalar(select(Operator).where(Operator.email == email))
    empreinte = operateur.password_hash if operateur and operateur.password_hash and email in autorises() else None
    # Même coût bcrypt que l'adresse existe ou non : le temps de réponse ne dit pas qui a un compte.
    bon = accounts.verify_password(mot_de_passe, empreinte or accounts._empreinte_factice())
    return operateur if bon and empreinte else None
