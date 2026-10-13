"""Calculs du scorecard (équivalent des formules du gabarit Excel)."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from .models import PROJETE, REEL, SERVICES, Assessment, Client, QuarterlyReview, YearMetrics


@dataclass(frozen=True)
class Column:
    key: str  # identifiant de formulaire, ex. "2025_reel"
    label: str
    year: int
    scenario: str


def scorecard_columns(year: int) -> list[Column]:
    """Colonnes du gabarit: 2 années historiques, l'année courante projetée puis actuelle."""
    return [
        Column(f"{year - 2}_{REEL}", str(year - 2), year - 2, REEL),
        Column(f"{year - 1}_{REEL}", str(year - 1), year - 1, REEL),
        Column(f"{year}_{PROJETE}", f"{year} (projeté)", year, PROJETE),
        Column(f"{year}_{REEL}", f"{year} actuel", year, REEL),
    ]


def ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or not denominator:
        return None
    return numerator / denominator


def difference(projected: float | None, actual: float | None) -> float | None:
    """Comme la colonne « Différence » du gabarit: projeté − actuel (ce qui reste à aller chercher)."""
    if projected is None or actual is None:
        return None
    return projected - actual


def find_metrics(client: Client, year: int, scenario: str) -> YearMetrics | None:
    return next((m for m in client.metrics if m.year == year and m.scenario == scenario), None)


def current_revenue(client: Client, year: int) -> float | None:
    """Revenus actuels avec Camions BL: valeur saisie, sinon la dernière année complète."""
    if client.current_revenue_override is not None:
        return client.current_revenue_override
    last = find_metrics(client, year - 1, REEL)
    return last.revenue if last else None


@dataclass
class Potential:
    potential: float | None
    current: float | None
    penetration: float | None
    gap: float | None


def potential(client: Client, year: int) -> Potential:
    current = current_revenue(client, year)
    gap = client.potential_annual - current if client.potential_annual is not None and current is not None else None
    return Potential(client.potential_annual, current, ratio(current, client.potential_annual), gap)


def used_services(client: Client, year: int, scenario: str) -> set[str]:
    return {s.service for s in client.services if s.year == year and s.scenario == scenario}


def find_assessment(client: Client, year: int) -> Assessment | None:
    return next((a for a in client.assessments if a.year == year), None)


def latest_assessment(client: Client) -> Assessment | None:
    return max(client.assessments, key=lambda a: a.year, default=None)


def find_review(client: Client, year: int, quarter: int) -> QuarterlyReview | None:
    return next((r for r in client.reviews if r.year == year and r.quarter == quarter), None)


def build_scorecard(client: Client, year: int, today: dt.date | None = None) -> dict:
    """Prépare toutes les valeurs affichées sur la fiche (et utilisées par l'export Excel)."""
    today = today or dt.date.today()
    columns = scorecard_columns(year)
    metrics = {c.key: find_metrics(client, c.year, c.scenario) for c in columns}

    def values(attr: str) -> dict[str, float | None]:
        return {key: getattr(m, attr) if m else None for key, m in metrics.items()}

    revenue = values("revenue")
    margin = values("gross_margin")
    global_pct = {key: ratio(margin[key], revenue[key]) for key in revenue}
    proj, actual = columns[2].key, columns[3].key

    rows = [
        {"field": "revenue", "label": "Revenus totaux pièces et services", "fmt": "money", "values": revenue,
         "diff": difference(revenue[proj], revenue[actual]), "editable": True},
        {"field": "gross_margin", "label": "Marge brute", "fmt": "money", "values": margin,
         "diff": difference(margin[proj], margin[actual]), "editable": True},
        {"field": "service_margin_pct", "label": "% Marge Service", "fmt": "pct", "values": values("service_margin_pct"),
         "diff": None, "editable": True},
        {"field": "parts_margin_pct", "label": "% Marge Pièces", "fmt": "pct", "values": values("parts_margin_pct"),
         "diff": None, "editable": True},
        {"field": "global_pct", "label": "% Marge Global", "fmt": "pct", "values": global_pct,
         "diff": None, "editable": False},
    ]

    services = {c.key: used_services(client, c.year, c.scenario) for c in columns}
    assessment_years = [year - 2, year - 1, year]

    return {
        "year": year,
        "columns": columns,
        "rows": rows,
        "attainment": ratio(revenue[actual], revenue[proj]),
        "services": services,
        "service_list": SERVICES,
        "potential": potential(client, year),
        "assessment_years": assessment_years,
        "assessments": {y: find_assessment(client, y) for y in assessment_years},
        "reviews": {q: find_review(client, year, q) for q in range(1, 5)},
        "open_actions": sum(1 for a in client.actions if a.is_open),
        "overdue_actions": sum(1 for a in client.actions if a.is_overdue(today)),
    }


def portfolio_row(client: Client, year: int, today: dt.date | None = None) -> dict:
    """Résumé d'un client pour le tableau de bord du portefeuille."""
    today = today or dt.date.today()
    proj = find_metrics(client, year, PROJETE)
    actual = find_metrics(client, year, REEL)
    last = find_metrics(client, year - 1, REEL)
    pot = potential(client, year)
    assessment = latest_assessment(client)
    open_actions = [a for a in client.actions if a.is_open]
    next_action = min(
        (a for a in open_actions if a.due_date), key=lambda a: a.due_date, default=None
    )
    return {
        "client": client,
        "last_revenue": last.revenue if last else None,
        "projected": proj.revenue if proj else None,
        "actual": actual.revenue if actual else None,
        "attainment": ratio(actual.revenue if actual else None, proj.revenue if proj else None),
        "global_pct": ratio(actual.gross_margin, actual.revenue) if actual else None,
        "potential": pot,
        "topgrading": assessment.topgrading if assessment else None,
        "satisfaction": assessment.satisfaction if assessment else None,
        "open_actions": len(open_actions),
        "overdue_actions": sum(1 for a in open_actions if a.is_overdue(today)),
        "next_action": next_action,
        "services_count": len(used_services(client, year, REEL) or used_services(client, year - 1, REEL)),
    }
