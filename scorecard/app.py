"""Application web du scorecard client (FastAPI + gabarits Jinja)."""
from __future__ import annotations

import datetime as dt
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import pipedrive
from .config import get_settings
from .db import ensure_db, get_session
from .exporter import export_client
from .importers.onepager import import_onepager
from .importers.sales import import_sales_csv, import_sales_sql
from .metrics import build_scorecard, portfolio_row, scorecard_columns
from .models import (
    ACTION_STATUSES,
    CX_REP,
    DIRECTION_TITLES,
    SERVICES,
    TEAM_ROLES,
    TOPGRADING_GRADES,
    Action,
    Client,
)
from .repository import set_services, set_team, upsert_assessment, upsert_metrics, upsert_review
from .textutil import parse_date, parse_number, parse_pct

BASE_DIR = Path(__file__).parent


@asynccontextmanager
async def lifespan(_app: FastAPI):
    ensure_db()
    yield


app = FastAPI(title="Scorecard clients – Camions BL", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


# --- Filtres d'affichage (format canadien-français) ---

def _money(value) -> str:
    if value is None:
        return "—"
    return f"{value:,.0f}".replace(",", " ") + " $"


def _pct(value, digits: int = 0) -> str:
    if value is None:
        return "—"
    return f"{value * 100:.{digits}f}".replace(".", ",") + " %"


def _date(value) -> str:
    return value.strftime("%Y-%m-%d") if value else "—"


def _input_pct(value) -> str:
    return "" if value is None else f"{value * 100:g}"


def _input_money(value) -> str:
    return "" if value is None else f"{value:g}"


templates.env.filters.update(money=_money, pct=_pct, date=_date, input_pct=_input_pct, input_money=_input_money)
templates.env.globals.update(SERVICES=SERVICES, ACTION_STATUSES=ACTION_STATUSES, TOPGRADING=TOPGRADING_GRADES,
                             TEAM_ROLES=TEAM_ROLES, DIRECTION_TITLES=DIRECTION_TITLES)


def _redirect(url: str, msg: str | None = None, error: bool = False) -> RedirectResponse:
    if msg:
        url += ("&" if "?" in url else "?") + ("err=" if error else "msg=") + quote(msg)
    return RedirectResponse(url, status_code=303)


def _client_or_404(session: Session, client_id: int) -> Client:
    client = session.get(Client, client_id)
    if client is None:
        raise HTTPException(404, "Client introuvable")
    return client


def _render(request: Request, name: str, **context) -> HTMLResponse:
    settings = get_settings()
    context.update(
        settings=settings,
        today=dt.date.today(),
        msg=request.query_params.get("msg"),
        err=request.query_params.get("err"),
    )
    return templates.TemplateResponse(request, name, context)


# --- Portefeuille ---

SORTS = {
    "nom": (lambda r: r["client"].name.lower(), False),
    "succursale": (lambda r: ((r["client"].branch or "~").lower(), r["client"].name.lower()), False),
    "experience": (lambda r: ((r["client"].cx_rep or "~").lower(), r["client"].name.lower()), False),
    "revenus": (lambda r: r["last_revenue"] or 0, True),
    "gap": (lambda r: r["potential"].gap or 0, True),
    "penetration": (lambda r: r["potential"].penetration if r["potential"].penetration is not None else 2, False),
    "atteinte": (lambda r: r["attainment"] if r["attainment"] is not None else 2, False),
    "retards": (lambda r: r["overdue_actions"], True),
}


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, q: str = "", tri: str = "gap", succursale: str = "", year: int | None = None,
              session: Session = Depends(get_session)):
    year = year or get_settings().year
    clients = session.scalars(select(Client)).all()
    branches = _branches(session)
    if succursale:
        clients = [c for c in clients if (c.branch or "") == succursale]
    if q:
        needle = q.lower()
        clients = [c for c in clients if any(needle in (text or "").lower() for text in
                                             (c.name, c.serti_code, c.branch, *(m.name for m in c.team)))]
    rows = [portfolio_row(c, year) for c in clients]
    key, reverse = SORTS.get(tri, SORTS["gap"])
    rows.sort(key=key, reverse=reverse)
    totals = {
        "potential": sum(r["potential"].potential or 0 for r in rows),
        "current": sum(r["potential"].current or 0 for r in rows),
        "gap": sum(r["potential"].gap or 0 for r in rows),
        "projected": sum(r["projected"] or 0 for r in rows),
        "actual": sum(r["actual"] or 0 for r in rows),
        "overdue": sum(r["overdue_actions"] for r in rows),
    }
    totals["penetration"] = totals["current"] / totals["potential"] if totals["potential"] else None
    totals["attainment"] = totals["actual"] / totals["projected"] if totals["projected"] else None
    upcoming = sorted(
        ((a, c) for c in clients for a in c.actions if a.is_open and a.due_date),
        key=lambda pair: pair[0].due_date,
    )[:10]
    return _render(request, "dashboard.html", rows=rows, totals=totals, q=q, tri=tri, year=year, upcoming=upcoming,
                   branches=branches, succursale=succursale)


def _branches(session: Session) -> list[str]:
    """Succursales déjà saisies (liste de choix et filtre)."""
    return sorted(b for b in session.scalars(select(Client.branch).distinct()) if b)


# --- Clients ---

@app.get("/clients/nouveau", response_class=HTMLResponse)
def new_client_form(request: Request, session: Session = Depends(get_session)):
    return _render(request, "client_new.html", branches=_branches(session))


@app.post("/clients")
def create_client(name: str = Form(...), serti_code: str = Form(""), branch: str = Form(""),
                  cx_rep: str = Form(""), session: Session = Depends(get_session)):
    serti_code = serti_code.strip() or None
    if serti_code and session.scalar(select(Client).where(Client.serti_code == serti_code)):
        return _redirect("/clients/nouveau", f"Le code Serti {serti_code} existe déjà.", error=True)
    client = Client(name=name.strip(), serti_code=serti_code, branch=branch.strip() or None)
    set_team(client, {CX_REP: (cx_rep.strip() or None, None)}, [])
    session.add(client)
    session.commit()
    return _redirect(f"/clients/{client.id}", "Client créé.")


@app.get("/clients/{client_id}", response_class=HTMLResponse)
def client_page(request: Request, client_id: int, year: int | None = None, session: Session = Depends(get_session)):
    client = _client_or_404(session, client_id)
    year = year or get_settings().year
    return _render(request, "client.html", client=client, sc=build_scorecard(client, year), year=year,
                   candidates=None, branches=_branches(session))


@app.post("/clients/{client_id}/fiche")
async def save_scorecard(request: Request, client_id: int, session: Session = Depends(get_session)):
    """Enregistre en une fois les sections 1, 2, 3 et 5 de la fiche."""
    client = _client_or_404(session, client_id)
    form = await request.form()
    year = int(form.get("year") or get_settings().year)
    get = lambda name: str(form.get(name) or "").strip()  # noqa: E731

    client.name = get("name") or client.name
    serti_code = get("serti_code") or None
    if serti_code != client.serti_code and serti_code and session.scalar(
        select(Client).where(Client.serti_code == serti_code, Client.id != client.id)
    ):
        return _redirect(f"/clients/{client.id}?year={year}", f"Le code Serti {serti_code} est déjà utilisé.", True)
    client.serti_code = serti_code
    client.branch = get("branch") or None
    set_team(
        client,
        {role: (get(f"team__{role}__name") or None, get(f"team__{role}__title") or None) for role, _ in TEAM_ROLES},
        [(get(f"other__{i}__title"), get(f"other__{i}__name")) for i in range(int(get("other_count") or 0))],
    )

    for col in scorecard_columns(year):
        values = {
            "revenue": parse_number(get(f"m__{col.key}__revenue")),
            "gross_margin": parse_number(get(f"m__{col.key}__gross_margin")),
            "service_margin_pct": parse_pct(get(f"m__{col.key}__service_margin_pct")),
            "parts_margin_pct": parse_pct(get(f"m__{col.key}__parts_margin_pct")),
        }
        existing = next((m for m in client.metrics if m.year == col.year and m.scenario == col.scenario), None)
        if existing is None and all(v is None for v in values.values()):
            continue
        if existing is not None and all(getattr(existing, k) == v for k, v in values.items()):
            continue  # inchangé: on conserve la source (serti/sql)
        upsert_metrics(session, client, col.year, col.scenario, "manuel", **values)
    for col in scorecard_columns(year):
        set_services(session, client, col.year, col.scenario,
                     {key for key, _ in SERVICES if form.get(f"svc__{col.key}__{key}")})

    client.potential_annual = parse_number(get("potential_annual"))
    client.current_revenue_override = parse_number(get("current_revenue_override"))

    for y in (year - 2, year - 1, year):
        sat = parse_number(get(f"a__{y}__satisfaction"))
        values = {
            "topgrading": get(f"a__{y}__topgrading") or None,
            "satisfaction": max(1, min(5, int(sat))) if sat is not None else None,
        }
        if y == year:
            values.update(strengths=get("strengths") or None, weaknesses=get("weaknesses") or None,
                          growth_potential=get("growth_potential") or None)
        existing = next((a for a in client.assessments if a.year == y), None)
        if existing is not None or any(values.values()):
            upsert_assessment(session, client, y, **values)

    for q in range(1, 5):
        result, notes = get(f"r__{q}__result") or None, get(f"r__{q}__notes") or None
        existing = next((r for r in client.reviews if r.year == year and r.quarter == q), None)
        if existing is not None or result or notes:
            upsert_review(session, client, year, q, result=result, notes=notes)

    session.commit()
    return _redirect(f"/clients/{client.id}?year={year}", "Fiche enregistrée.")


@app.post("/clients/{client_id}/supprimer")
def delete_client(client_id: int, session: Session = Depends(get_session)):
    client = _client_or_404(session, client_id)
    session.delete(client)
    session.commit()
    return _redirect("/", f"{client.name} supprimé.")


@app.get("/clients/{client_id}/export.xlsx")
def export_xlsx(client_id: int, year: int | None = None, session: Session = Depends(get_session)):
    client = _client_or_404(session, client_id)
    year = year or get_settings().year
    filename = f"Scorecard_{(client.serti_code or client.name).replace(' ', '_')}_{year}.xlsx"
    return Response(
        export_client(client, year),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


# --- Plan d'action ---

@app.post("/clients/{client_id}/actions")
def add_action(client_id: int, title: str = Form(...), owner: str = Form(""), due_date: str = Form(""),
               follow_up: str = Form(""), year: int = Form(0), session: Session = Depends(get_session)):
    client = _client_or_404(session, client_id)
    client.actions.append(Action(title=title.strip(), owner=owner.strip() or None, due_date=parse_date(due_date),
                                 follow_up=follow_up.strip() or None))
    session.commit()
    return _redirect(f"/clients/{client.id}?year={year or ''}#actions", "Action ajoutée.")


@app.post("/clients/{client_id}/actions/{action_id}")
def update_action(client_id: int, action_id: int, title: str = Form(...), owner: str = Form(""),
                  due_date: str = Form(""), follow_up: str = Form(""), status: str = Form("a_faire"),
                  year: int = Form(0), session: Session = Depends(get_session)):
    action = session.get(Action, action_id)
    if action is None or action.client_id != client_id:
        raise HTTPException(404, "Action introuvable")
    action.title, action.owner = title.strip(), owner.strip() or None
    action.due_date, action.follow_up = parse_date(due_date), follow_up.strip() or None
    if status != action.status:
        action.done_date = dt.date.today() if status == "fait" else None
    action.status = status if status in dict(ACTION_STATUSES) else action.status
    session.commit()
    return _redirect(f"/clients/{client_id}?year={year or ''}#actions", "Action mise à jour.")


@app.post("/clients/{client_id}/actions/{action_id}/supprimer")
def delete_action(client_id: int, action_id: int, year: int = Form(0), session: Session = Depends(get_session)):
    action = session.get(Action, action_id)
    if action is None or action.client_id != client_id:
        raise HTTPException(404, "Action introuvable")
    session.delete(action)
    session.commit()
    msg = "Action supprimée."
    if action.pipedrive_activity_id:
        msg += " L'activité Pipedrive associée n'a pas été supprimée."
    return _redirect(f"/clients/{client_id}?year={year or ''}#actions", msg)


# --- Pipedrive ---

def _pipedrive_client() -> pipedrive.PipedriveClient:
    return pipedrive.PipedriveClient(get_settings())


@app.post("/clients/{client_id}/pipedrive/rechercher", response_class=HTMLResponse)
def pipedrive_search(request: Request, client_id: int, term: str = Form(""), year: int = Form(0),
                     session: Session = Depends(get_session)):
    client = _client_or_404(session, client_id)
    year = year or get_settings().year
    try:
        with _pipedrive_client() as pd:
            candidates = pd.search_organizations(term) if term.strip() else pipedrive.find_organization(pd, client)
    except pipedrive.PipedriveError as exc:
        return _redirect(f"/clients/{client_id}?year={year}", str(exc), error=True)
    return _render(request, "client.html", client=client, sc=build_scorecard(client, year), year=year,
                   candidates=candidates, branches=_branches(session))


@app.post("/clients/{client_id}/pipedrive/lier")
def pipedrive_link(client_id: int, org_id: str = Form(""), year: int = Form(0),
                   session: Session = Depends(get_session)):
    client = _client_or_404(session, client_id)
    client.pipedrive_org_id = int(org_id) if org_id.strip().isdigit() else None
    if client.pipedrive_org_id is None:
        client.pipedrive_snapshot = None
    session.commit()
    msg = "Organisation Pipedrive liée." if client.pipedrive_org_id else "Lien Pipedrive retiré."
    return _redirect(f"/clients/{client_id}?year={year or ''}", msg)


@app.post("/clients/{client_id}/pipedrive/synchroniser")
def pipedrive_sync(client_id: int, year: int = Form(0), session: Session = Depends(get_session)):
    client = _client_or_404(session, client_id)
    year = year or get_settings().year
    try:
        with _pipedrive_client() as pd:
            pipedrive.sync_client(pd, client, year)
    except pipedrive.PipedriveError as exc:
        session.commit()  # conserve les identifiants d'activités déjà créées
        return _redirect(f"/clients/{client_id}?year={year}", str(exc), error=True)
    session.commit()
    return _redirect(f"/clients/{client_id}?year={year}", "Synchronisé avec Pipedrive.")


@app.post("/pipedrive/synchroniser")
def pipedrive_sync_all(session: Session = Depends(get_session)):
    year = get_settings().year
    clients = session.scalars(select(Client).where(Client.pipedrive_org_id.is_not(None))).all()
    errors = []
    try:
        with _pipedrive_client() as pd:
            for client in clients:
                try:
                    pipedrive.sync_client(pd, client, year)
                except pipedrive.PipedriveError as exc:
                    errors.append(f"{client.name}: {exc}")
                session.commit()
    except pipedrive.PipedriveError as exc:
        return _redirect("/", str(exc), error=True)
    if errors:
        return _redirect("/", " | ".join(errors[:3]), error=True)
    return _redirect("/", f"{len(clients)} client(s) synchronisé(s) avec Pipedrive.")


# --- Importations ---

@app.get("/importer", response_class=HTMLResponse)
def import_page(request: Request):
    return _render(request, "import.html")


@app.post("/importer/onepager")
async def import_onepager_route(fichier: UploadFile = File(...), session: Session = Depends(get_session)):
    try:
        clients = import_onepager(session, await fichier.read())
    except Exception as exc:  # fichier corrompu, format inattendu
        session.rollback()
        return _redirect("/importer", f"Importation impossible: {exc}", error=True)
    session.commit()
    if len(clients) == 1:
        return _redirect(f"/clients/{clients[0].id}", f"Scorecard de {clients[0].name} importé.")
    return _redirect("/", f"{len(clients)} scorecard(s) importé(s).")


@app.post("/importer/ventes")
async def import_sales_route(fichier: UploadFile = File(...), session: Session = Depends(get_session)):
    try:
        report = import_sales_csv(session, await fichier.read())
    except Exception as exc:
        session.rollback()
        return _redirect("/importer", f"Importation impossible: {exc}", error=True)
    session.commit()
    return _redirect("/importer", report.summary())


@app.post("/importer/sql")
def import_sql_route(session: Session = Depends(get_session)):
    settings = get_settings()
    if not settings.source_db_url:
        return _redirect("/importer", "SOURCE_DB_URL n'est pas configuré.", error=True)
    try:
        query = Path(settings.source_query_file).read_text(encoding="utf-8")
        report = import_sales_sql(session, settings.source_db_url, query)
    except Exception as exc:
        session.rollback()
        return _redirect("/importer", f"Importation SQL impossible: {exc}", error=True)
    session.commit()
    return _redirect("/importer", report.summary())
