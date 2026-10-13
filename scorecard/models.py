"""Modèle de données du scorecard client.

Reprend les 5 sections du gabarit « Scorecard Client One-Pager » :
1. Données historiques (YearMetrics, ServiceUsage)
2. Potentiel global (champs de Client)
3. Analyse qualitative (Assessment)
4. Plan d'action (Action)
5. Suivi de performance trimestriel (QuarterlyReview)
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    JSON,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# Services offerts par Camions BL (ordre du gabarit).
SERVICES: list[tuple[str, str]] = [
    ("pieces", "Pièces"),
    ("excelerator", "Excelerator"),
    ("service", "Service"),
    ("saaq", "SAAQ"),
    ("unite_mobile", "Unité mobile"),
    ("alignement", "Alignement"),
    ("jockey", "Jockey"),
    ("location_bl", "Location BL"),
    ("ventes_camions", "Ventes camions"),
    ("academie", "Académie"),
]
SERVICE_LABELS = dict(SERVICES)

# Scénarios d'une année : réel (historique ou cumul de l'année courante) ou projeté (objectif).
REEL = "reel"
PROJETE = "projete"

TOPGRADING_GRADES = ["A", "B", "C", "D"]

# Équipe client: rôles principaux (un membre chacun sur la fiche) puis « autre » (illimité, rôle libre).
CX_REP = "experience_client"
TEAM_ROLES: list[tuple[str, str]] = [
    ("ventes_camions", "Représentant ventes camions"),
    (CX_REP, "Représentant expérience client"),
    ("direction", "Membre de la direction"),
    ("conseiller_technique", "Conseiller technique service"),
]
OTHER_ROLE = "autre"
TEAM_ROLE_LABELS = dict(TEAM_ROLES) | {OTHER_ROLE: "Autre"}
DIRECTION_TITLES = ["DG", "VP", "Co-Président"]

ACTION_STATUSES = [("a_faire", "À faire"), ("en_cours", "En cours"), ("fait", "Fait"), ("annule", "Annulé")]
ACTION_STATUS_LABELS = dict(ACTION_STATUSES)

# Code de couleur du suivi trimestriel (colonne Résultats).
REVIEW_STATUSES = [("vert", "Vert – sur la cible"), ("jaune", "Jaune – à surveiller"), ("rouge", "Rouge – sous la cible")]
REVIEW_STATUS_LABELS = dict(REVIEW_STATUSES)


class Base(DeclarativeBase):
    pass


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class Client(Base):
    __tablename__ = "clients"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    serti_code: Mapped[str | None] = mapped_column(String(50), unique=True)
    branch: Mapped[str | None] = mapped_column(String(100))  # succursale

    # Section 2 – potentiel
    potential_annual: Mapped[float | None] = mapped_column(Float)
    # Revenus actuels saisis manuellement; si vide, on prend la dernière année complète.
    current_revenue_override: Mapped[float | None] = mapped_column(Float)

    # Pipedrive
    pipedrive_org_id: Mapped[int | None] = mapped_column(Integer)
    pipedrive_snapshot: Mapped[dict | None] = mapped_column(JSON)
    pipedrive_synced_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    metrics: Mapped[list[YearMetrics]] = relationship(back_populates="client", cascade="all, delete-orphan")
    services: Mapped[list[ServiceUsage]] = relationship(back_populates="client", cascade="all, delete-orphan")
    assessments: Mapped[list[Assessment]] = relationship(back_populates="client", cascade="all, delete-orphan")
    actions: Mapped[list[Action]] = relationship(
        back_populates="client", cascade="all, delete-orphan", order_by="Action.id"
    )
    reviews: Mapped[list[QuarterlyReview]] = relationship(back_populates="client", cascade="all, delete-orphan")
    team: Mapped[list[TeamMember]] = relationship(
        back_populates="client", cascade="all, delete-orphan", order_by="TeamMember.id"
    )

    def team_member(self, role: str) -> TeamMember | None:
        return next((m for m in self.team if m.role == role), None)

    @property
    def cx_rep(self) -> str | None:
        member = self.team_member(CX_REP)
        return member.name if member else None

    @property
    def other_team(self) -> list[TeamMember]:
        return [m for m in self.team if m.role == OTHER_ROLE]


class TeamMember(Base):
    """Membre de l'équipe Camions BL assigné au client."""

    __tablename__ = "team_members"

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(30))
    name: Mapped[str] = mapped_column(String(100))
    # Direction: DG / VP / Co-Président; « autre »: le rôle en clair (ex.: Représentant pièces).
    title: Mapped[str | None] = mapped_column(String(100))

    client: Mapped[Client] = relationship(back_populates="team")

    @property
    def role_label(self) -> str:
        if self.role == OTHER_ROLE:
            return self.title or "Autre"
        return TEAM_ROLE_LABELS.get(self.role, self.role)


class YearMetrics(Base):
    """Revenus et marges d'un client pour une année et un scénario (réel / projeté)."""

    __tablename__ = "year_metrics"
    __table_args__ = (UniqueConstraint("client_id", "year", "scenario"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"))
    year: Mapped[int] = mapped_column(Integer)
    scenario: Mapped[str] = mapped_column(String(10), default=REEL)
    revenue: Mapped[float | None] = mapped_column(Float)  # Revenus totaux pièces et services
    gross_margin: Mapped[float | None] = mapped_column(Float)  # Marge brute ($)
    service_margin_pct: Mapped[float | None] = mapped_column(Float)  # 0.77 = 77 %
    parts_margin_pct: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(20), default="manuel")  # manuel | serti | sql | excel
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    client: Mapped[Client] = relationship(back_populates="metrics")


class ServiceUsage(Base):
    __tablename__ = "service_usage"
    __table_args__ = (UniqueConstraint("client_id", "year", "scenario", "service"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"))
    year: Mapped[int] = mapped_column(Integer)
    scenario: Mapped[str] = mapped_column(String(10), default=REEL)
    service: Mapped[str] = mapped_column(String(30))

    client: Mapped[Client] = relationship(back_populates="services")


class Assessment(Base):
    """Analyse qualitative annuelle."""

    __tablename__ = "assessments"
    __table_args__ = (UniqueConstraint("client_id", "year"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"))
    year: Mapped[int] = mapped_column(Integer)
    topgrading: Mapped[str | None] = mapped_column(String(2))
    satisfaction: Mapped[int | None] = mapped_column(Integer)  # /5
    strengths: Mapped[str | None] = mapped_column(Text)
    weaknesses: Mapped[str | None] = mapped_column(Text)
    growth_potential: Mapped[str | None] = mapped_column(Text)

    client: Mapped[Client] = relationship(back_populates="assessments")


class Action(Base):
    __tablename__ = "actions"

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(Text)
    owner: Mapped[str | None] = mapped_column(String(100))
    due_date: Mapped[dt.date | None] = mapped_column(Date)
    follow_up: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="a_faire")
    done_date: Mapped[dt.date | None] = mapped_column(Date)
    pipedrive_activity_id: Mapped[int | None] = mapped_column(Integer)

    client: Mapped[Client] = relationship(back_populates="actions")

    @property
    def is_open(self) -> bool:
        return self.status in ("a_faire", "en_cours")

    def is_overdue(self, today: dt.date | None = None) -> bool:
        today = today or dt.date.today()
        return self.is_open and self.due_date is not None and self.due_date < today


class QuarterlyReview(Base):
    __tablename__ = "quarterly_reviews"
    __table_args__ = (UniqueConstraint("client_id", "year", "quarter"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"))
    year: Mapped[int] = mapped_column(Integer)
    quarter: Mapped[int] = mapped_column(Integer)  # 1..4
    result: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str | None] = mapped_column(String(10))  # vert | jaune | rouge
    pipedrive_note_id: Mapped[int | None] = mapped_column(Integer)

    client: Mapped[Client] = relationship(back_populates="reviews")
