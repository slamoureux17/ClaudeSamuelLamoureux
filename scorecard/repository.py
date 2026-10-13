"""Fonctions d'écriture « upsert » partagées par les formulaires et les importations."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Assessment, Client, QuarterlyReview, ServiceUsage, YearMetrics


def get_or_create_client(session: Session, name: str, serti_code: str | None) -> Client:
    client = None
    if serti_code:
        client = session.scalar(select(Client).where(Client.serti_code == serti_code))
    if client is None:
        client = session.scalar(select(Client).where(Client.name == name))
    if client is None:
        client = Client(name=name, serti_code=serti_code)
        session.add(client)
        session.flush()
    elif serti_code and not client.serti_code:
        client.serti_code = serti_code
    return client


def upsert_metrics(session: Session, client: Client, year: int, scenario: str, source: str, **values) -> YearMetrics:
    metrics = session.scalar(
        select(YearMetrics).where(
            YearMetrics.client_id == client.id, YearMetrics.year == year, YearMetrics.scenario == scenario
        )
    )
    if metrics is None:
        metrics = YearMetrics(client=client, year=year, scenario=scenario)
        session.add(metrics)
    for key, value in values.items():
        setattr(metrics, key, value)
    metrics.source = source
    return metrics


def set_services(session: Session, client: Client, year: int, scenario: str, services: set[str]) -> None:
    existing = {s.service: s for s in client.services if s.year == year and s.scenario == scenario}
    for key, usage in existing.items():
        if key not in services:
            client.services.remove(usage)
    for key in services - existing.keys():
        client.services.append(ServiceUsage(year=year, scenario=scenario, service=key))


def add_services(session: Session, client: Client, year: int, scenario: str, services: set[str]) -> None:
    current = {s.service for s in client.services if s.year == year and s.scenario == scenario}
    set_services(session, client, year, scenario, current | services)


def upsert_assessment(session: Session, client: Client, year: int, **values) -> Assessment:
    assessment = next((a for a in client.assessments if a.year == year), None)
    if assessment is None:
        assessment = Assessment(year=year)
        client.assessments.append(assessment)
    for key, value in values.items():
        setattr(assessment, key, value)
    return assessment


def upsert_review(session: Session, client: Client, year: int, quarter: int, **values) -> QuarterlyReview:
    review = next((r for r in client.reviews if r.year == year and r.quarter == quarter), None)
    if review is None:
        review = QuarterlyReview(year=year, quarter=quarter)
        client.reviews.append(review)
    for key, value in values.items():
        setattr(review, key, value)
    return review
