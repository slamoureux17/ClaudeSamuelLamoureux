"""Intégration avec Pipedrive (API REST v1, jeton personnel).

Ce que fait la synchronisation d'un client lié à une organisation Pipedrive:
- Envoi: chaque action du plan d'action devient une activité (tâche) sur l'organisation,
  assignée au responsable (associé par nom à un utilisateur Pipedrive); les suivis
  trimestriels deviennent des notes; les indicateurs (potentiel, % de pénétration,
  Topgrading, satisfaction) peuvent alimenter des champs personnalisés.
- Réception: les affaires (deals) ouvertes/gagnées et les dates d'activité de
  l'organisation sont mémorisées pour affichage; une activité marquée « faite » dans
  Pipedrive ferme l'action correspondante.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

import httpx

from .config import Settings
from .metrics import latest_assessment, potential
from .models import ACTION_STATUS_LABELS, REVIEW_STATUS_LABELS, Action, Client, QuarterlyReview
from .textutil import normalize


class PipedriveError(RuntimeError):
    pass


class PipedriveClient:
    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None):
        if not settings.pipedrive_api_token:
            raise PipedriveError("PIPEDRIVE_API_TOKEN n'est pas configuré.")
        self.settings = settings
        self.http = httpx.Client(
            base_url=settings.pipedrive_base_url,
            params={"api_token": settings.pipedrive_api_token},
            timeout=20,
            transport=transport,
        )
        self._users: list[dict] | None = None

    def close(self) -> None:
        self.http.close()

    def __enter__(self) -> PipedriveClient:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _request(self, method: str, path: str, **kwargs) -> dict:
        try:
            response = self.http.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise PipedriveError(f"Pipedrive injoignable: {exc}") from exc
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        if response.status_code >= 400 or payload.get("success") is False:
            message = payload.get("error") or payload.get("error_info") or response.text[:200]
            raise PipedriveError(f"Pipedrive {method} {path} -> {response.status_code}: {message}")
        return payload

    def get(self, path: str, **params) -> Any:
        return self._request("GET", path, params=params).get("data")

    def get_all(self, path: str, **params) -> list[dict]:
        items, start = [], 0
        while True:
            payload = self._request("GET", path, params={**params, "start": start, "limit": 500})
            items.extend(payload.get("data") or [])
            pagination = (payload.get("additional_data") or {}).get("pagination") or {}
            if not pagination.get("more_items_in_collection"):
                return items
            start = pagination.get("next_start", start + 500)

    def post(self, path: str, body: dict) -> Any:
        return self._request("POST", path, json=body).get("data")

    def put(self, path: str, body: dict) -> Any:
        return self._request("PUT", path, json=body).get("data")

    # --- Organisations ---
    def search_organizations(self, term: str) -> list[dict]:
        data = self.get("/organizations/search", term=term, fields="name", limit=10) or {}
        return [item["item"] for item in data.get("items", [])]

    def users(self) -> list[dict]:
        if self._users is None:
            self._users = [u for u in (self.get("/users") or []) if u.get("active_flag", True)]
        return self._users

    def user_id_for(self, owner: str | None) -> int | None:
        """Associe « Maxime » ou « Samuel J. » à l'utilisateur Pipedrive dont le nom correspond."""
        if not owner:
            return None
        wanted = normalize(owner).replace(".", "")
        names = {user["id"]: normalize(user.get("name")).replace(".", "") for user in self.users()}
        exact = [uid for uid, name in names.items() if name == wanted]
        prefix = [uid for uid, name in names.items() if name.startswith(wanted)]
        matches = exact or prefix
        return matches[0] if len(matches) == 1 else None  # ambigu (deux « Maxime ») -> non assigné


def org_url(settings: Settings, org_id: int) -> str:
    domain = settings.pipedrive_domain or "app"
    return f"https://{domain}.pipedrive.com/organization/{org_id}"


def find_organization(pd: PipedriveClient, client: Client) -> list[dict]:
    """Candidats pour lier un client: par nom, puis par code Serti."""
    candidates = pd.search_organizations(client.name)
    if not candidates and client.serti_code:
        candidates = pd.search_organizations(client.serti_code)
    exact = [c for c in candidates if normalize(c.get("name")) == normalize(client.name)]
    return exact or candidates


# --- Envoi vers Pipedrive ---

def _activity_body(pd: PipedriveClient, client: Client, action: Action) -> dict:
    note_lines = [f"Plan d'action – scorecard client {client.name}", f"Statut: {ACTION_STATUS_LABELS[action.status]}"]
    if action.owner:
        note_lines.append(f"Responsable: {action.owner}")
    if action.follow_up:
        note_lines.append(f"Suivi: {action.follow_up}")
    body = {
        "subject": action.title[:250],
        "type": "task",
        "org_id": client.pipedrive_org_id,
        "note": "<br>".join(note_lines),
        "done": 1 if action.status in ("fait", "annule") else 0,
    }
    if action.due_date:
        body["due_date"] = action.due_date.isoformat()
    user_id = pd.user_id_for(action.owner)
    if user_id:
        body["user_id"] = user_id
    return body


def push_action(pd: PipedriveClient, client: Client, action: Action) -> None:
    if action.pipedrive_activity_id:
        pd.put(f"/activities/{action.pipedrive_activity_id}", _activity_body(pd, client, action))
    elif action.is_open:
        created = pd.post("/activities", _activity_body(pd, client, action))
        action.pipedrive_activity_id = created["id"]


def export_actions(pd: PipedriveClient, client: Client, actions: list[Action] | None = None) -> tuple[int, int]:
    """Exporte le plan d'action (ou les actions données) en activités. Retourne (créées, mises à jour)."""
    if not client.pipedrive_org_id:
        raise PipedriveError(f"{client.name} n'est pas lié à une organisation Pipedrive.")
    created = updated = 0
    for action in client.actions if actions is None else actions:
        had_activity = bool(action.pipedrive_activity_id)
        push_action(pd, client, action)
        if had_activity:
            updated += 1
        elif action.pipedrive_activity_id:
            created += 1
    return created, updated


def push_review(pd: PipedriveClient, client: Client, review: QuarterlyReview) -> None:
    if not (review.result or review.notes or review.status):
        return
    parts = [f"<b>Suivi de performance T{review.quarter} {review.year}</b>"]
    if review.status:
        parts.append(f"Statut: {REVIEW_STATUS_LABELS[review.status]}")
    if review.result:
        parts.append(f"Résultats: {review.result}")
    if review.notes:
        parts.append(f"Notes: {review.notes}")
    body = {"content": "<br>".join(parts), "org_id": client.pipedrive_org_id}
    if review.pipedrive_note_id:
        pd.put(f"/notes/{review.pipedrive_note_id}", body)
    else:
        review.pipedrive_note_id = pd.post("/notes", body)["id"]


def push_kpis(pd: PipedriveClient, client: Client, year: int) -> None:
    s = pd.settings
    pot = potential(client, year)
    assessment = latest_assessment(client)
    fields = {
        s.pipedrive_field_serti: client.serti_code,
        s.pipedrive_field_potential: pot.potential,
        s.pipedrive_field_penetration: round(pot.penetration * 100, 1) if pot.penetration is not None else None,
        s.pipedrive_field_topgrading: assessment.topgrading if assessment else None,
        s.pipedrive_field_satisfaction: assessment.satisfaction if assessment else None,
    }
    body = {key: value for key, value in fields.items() if key and value is not None}
    if body:
        pd.put(f"/organizations/{client.pipedrive_org_id}", body)


# --- Réception depuis Pipedrive ---

def pull_snapshot(pd: PipedriveClient, client: Client, year: int) -> dict:
    org_id = client.pipedrive_org_id
    org = pd.get(f"/organizations/{org_id}") or {}
    deals = pd.get_all(f"/organizations/{org_id}/deals", status="all_not_deleted")
    open_deals = [d for d in deals if d.get("status") == "open"]
    won_this_year = [d for d in deals if d.get("status") == "won" and str(d.get("won_time") or "").startswith(str(year))]
    owner = org.get("owner_id")
    return {
        "org": {
            "id": org_id,
            "name": org.get("name"),
            "owner": owner.get("name") if isinstance(owner, dict) else None,
            "url": org_url(pd.settings, org_id),
        },
        "open_deals": [
            {"id": d["id"], "title": d.get("title"), "value": d.get("value"), "currency": d.get("currency"),
             "expected_close_date": d.get("expected_close_date")}
            for d in open_deals
        ],
        "open_value": sum(d.get("value") or 0 for d in open_deals),
        "won_count_year": len(won_this_year),
        "won_value_year": sum(d.get("value") or 0 for d in won_this_year),
        "next_activity_date": org.get("next_activity_date"),
        "last_activity_date": org.get("last_activity_date"),
    }


def pull_action_status(pd: PipedriveClient, action: Action) -> None:
    """Une activité complétée dans Pipedrive ferme l'action dans le scorecard."""
    if not action.pipedrive_activity_id or not action.is_open:
        return
    activity = pd.get(f"/activities/{action.pipedrive_activity_id}") or {}
    if activity.get("done"):
        action.status = "fait"
        marked = str(activity.get("marked_as_done_time") or "")[:10]
        action.done_date = dt.date.fromisoformat(marked) if marked else dt.date.today()


def sync_client(pd: PipedriveClient, client: Client, year: int) -> dict:
    """Synchronisation bidirectionnelle d'un client déjà lié à une organisation."""
    if not client.pipedrive_org_id:
        raise PipedriveError(f"{client.name} n'est pas lié à une organisation Pipedrive.")
    for action in client.actions:
        pull_action_status(pd, action)
    for action in client.actions:
        push_action(pd, client, action)
    for review in client.reviews:
        if review.year == year:
            push_review(pd, client, review)
    push_kpis(pd, client, year)
    client.pipedrive_snapshot = pull_snapshot(pd, client, year)
    client.pipedrive_synced_at = dt.datetime.now(dt.timezone.utc)
    return client.pipedrive_snapshot
