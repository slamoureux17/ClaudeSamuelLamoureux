"""Importation des ventes par client / année / département.

Deux sources produisent le même format de lignes:
- un export CSV de Serti (Keyloop) ou de tout autre rapport;
- une requête SQL sur votre base de données (ou une réplique Serti), voir ``requete_ventes.sql``.

Colonnes attendues (noms tolérants, sans égard aux accents/majuscules):
    code_client   numéro d'adresse Serti (ex.: TRAJMF) — obligatoire
    nom_client    optionnel, sert à créer le client s'il n'existe pas
    annee         ex.: 2025 — ou une colonne « date » dont on extrait l'année
    departement   pièces, service, SAAQ, alignement, jockey, location, ...
    revenus       montant des ventes
    marge         marge brute ($) — ou une colonne « cout » (marge = revenus − coût)
    scenario      optionnel: « reel » (défaut) ou « projete » pour charger des objectifs
"""
from __future__ import annotations

import csv
import io
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from ..models import PROJETE, REEL, SERVICES, Client
from ..repository import add_services, get_or_create_client, upsert_metrics
from ..textutil import normalize, parse_date, parse_number

COLUMN_ALIASES = {
    "code_client": {"code_client", "code client", "client", "no client", "num client", "numero client",
                    "adresse", "num. adresse", "num adresse serti", "code serti", "customer"},
    "nom_client": {"nom_client", "nom client", "nom", "name", "raison sociale"},
    "annee": {"annee", "an", "year", "exercice"},
    "date": {"date", "date facture", "invoice date"},
    "departement": {"departement", "dept", "department", "secteur", "type"},
    "revenus": {"revenus", "revenu", "ventes", "vente", "montant", "sales", "revenue"},
    "marge": {"marge", "marge brute", "profit", "gross margin", "margin"},
    "cout": {"cout", "couts", "cost", "prix coutant"},
    "scenario": {"scenario"},
}

# Départements reconnus -> service du scorecard. « pieces » et « service » alimentent aussi les % de marge.
DEPARTMENT_ALIASES = {
    "pieces": {"pieces", "piece", "parts", "comptoir"},
    "service": {"service", "atelier", "main d'oeuvre", "main-d'oeuvre", "mo", "labour", "labor"},
    "excelerator": {"excelerator"},
    "saaq": {"saaq", "inspection", "pep", "inspection saaq"},
    "unite_mobile": {"unite mobile", "mobile", "service mobile"},
    "alignement": {"alignement", "alignment"},
    "jockey": {"jockey"},
    "location_bl": {"location", "location bl", "leasing", "rental"},
    "ventes_camions": {"ventes camions", "camions", "vente camion", "vehicules", "trucks"},
    "academie": {"academie", "formation", "academy"},
}
_DEPARTMENT_LOOKUP = {alias: key for key, aliases in DEPARTMENT_ALIASES.items() for alias in aliases}
_SERVICE_KEYS = {key for key, _ in SERVICES}


def classify_department(value: object) -> str | None:
    return _DEPARTMENT_LOOKUP.get(normalize(value))


@dataclass
class _Totals:
    name: str | None = None
    revenue: float = 0.0
    margin: float = 0.0
    dept_revenue: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    dept_margin: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    services: set[str] = field(default_factory=set)


@dataclass
class ImportReport:
    rows: int = 0
    skipped: int = 0
    clients_created: int = 0
    metrics_updated: int = 0
    unknown_departments: set[str] = field(default_factory=set)

    def summary(self) -> str:
        text = (f"{self.rows} lignes lues, {self.skipped} ignorées, {self.metrics_updated} années mises à jour, "
                f"{self.clients_created} nouveaux clients.")
        if self.unknown_departments:
            text += " Départements non reconnus (comptés dans les totaux): " + ", ".join(sorted(self.unknown_departments))
        return text


def _map_columns(fieldnames: Iterable[str]) -> dict[str, str]:
    mapping = {}
    for name in fieldnames:
        norm = normalize(name)
        for target, aliases in COLUMN_ALIASES.items():
            if norm in aliases and target not in mapping:
                mapping[target] = name
    return mapping


def import_sales_rows(session: Session, rows: Iterable[Mapping[str, object]], source: str) -> ImportReport:
    report = ImportReport()
    totals: dict[tuple[str, int, str], _Totals] = defaultdict(_Totals)
    mapping: dict[str, str] | None = None

    for raw in rows:
        report.rows += 1
        if mapping is None:
            mapping = _map_columns(raw.keys())
            missing = {"code_client", "revenus"} - mapping.keys()
            if missing or not ({"annee", "date"} & mapping.keys()):
                raise ValueError(
                    "Colonnes manquantes dans la source: "
                    + ", ".join(sorted(missing | ({"annee"} if not {"annee", "date"} & mapping.keys() else set())))
                    + f". Colonnes trouvées: {', '.join(raw.keys())}"
                )
        get = lambda key: raw.get(mapping[key]) if key in mapping else None  # noqa: E731

        code = str(get("code_client") or "").strip()
        year = parse_number(get("annee"))
        if year is None and get("date") is not None:
            date = parse_date(get("date"))
            year = date.year if date else None
        revenue = parse_number(get("revenus"))
        if not code or year is None or revenue is None:
            report.skipped += 1
            continue
        margin = parse_number(get("marge"))
        if margin is None and parse_number(get("cout")) is not None:
            margin = revenue - parse_number(get("cout"))
        scenario = PROJETE if normalize(get("scenario")).startswith("proj") else REEL

        bucket = totals[(code, int(year), scenario)]
        bucket.name = bucket.name or (str(get("nom_client")).strip() if get("nom_client") else None)
        bucket.revenue += revenue
        bucket.margin += margin or 0.0
        dept = classify_department(get("departement")) if "departement" in mapping else None
        if dept:
            bucket.dept_revenue[dept] += revenue
            bucket.dept_margin[dept] += margin or 0.0
            if revenue > 0 and dept in _SERVICE_KEYS:
                bucket.services.add(dept)
        elif get("departement"):
            report.unknown_departments.add(str(get("departement")).strip())

    for (code, year, scenario), bucket in totals.items():
        exists = session.scalar(select(Client).where(Client.serti_code == code))
        client = get_or_create_client(session, bucket.name or code, code)
        if exists is None:
            report.clients_created += 1

        def dept_pct(dept: str) -> float | None:
            rev = bucket.dept_revenue.get(dept)
            return bucket.dept_margin[dept] / rev if rev else None

        values = {"revenue": round(bucket.revenue, 2), "gross_margin": round(bucket.margin, 2)}
        for dept, attr in (("service", "service_margin_pct"), ("pieces", "parts_margin_pct")):
            if dept_pct(dept) is not None:
                values[attr] = round(dept_pct(dept), 4)
        upsert_metrics(session, client, year, scenario, source, **values)
        if bucket.services:
            add_services(session, client, year, scenario, bucket.services)
        report.metrics_updated += 1
        session.flush()
    return report


def read_csv(data: bytes) -> list[dict[str, str]]:
    """Lit un CSV en détectant l'encodage (UTF-8 ou Windows-1252) et le séparateur (; , ou tab)."""
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            content = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    sample = content[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=";,\t|")
    except csv.Error:
        dialect = csv.excel
    return list(csv.DictReader(io.StringIO(content), dialect=dialect))


def import_sales_csv(session: Session, data: bytes, source: str = "serti") -> ImportReport:
    return import_sales_rows(session, read_csv(data), source)


def import_sales_sql(session: Session, db_url: str, query: str) -> ImportReport:
    """Exécute la requête sur la base source (lecture seule) et importe le résultat."""
    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            result = conn.execute(text(query))
            rows = [dict(row._mapping) for row in result]
    finally:
        engine.dispose()
    return import_sales_rows(session, rows, "sql")
