"""Configuration lue depuis les variables d'environnement (ou un fichier .env)."""
from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name, default)
    return value.strip() if isinstance(value, str) and value.strip() else default


@dataclass(frozen=True)
class Settings:
    database_url: str = field(default_factory=lambda: _env("DATABASE_URL", "sqlite:///scorecard.db"))
    # Année "courante" du scorecard (colonnes projeté / actuel). Par défaut: l'année du jour.
    year: int = field(default_factory=lambda: int(_env("SCORECARD_YEAR") or dt.date.today().year))

    # --- Pipedrive ---
    pipedrive_api_token: str | None = field(default_factory=lambda: _env("PIPEDRIVE_API_TOKEN"))
    # Sous-domaine de la compagnie (ex.: "camionsbl" -> https://camionsbl.pipedrive.com).
    pipedrive_domain: str | None = field(default_factory=lambda: _env("PIPEDRIVE_DOMAIN"))
    # Clés (hash) des champs personnalisés d'organisation à mettre à jour (optionnel).
    pipedrive_field_serti: str | None = field(default_factory=lambda: _env("PIPEDRIVE_FIELD_SERTI"))
    pipedrive_field_potential: str | None = field(default_factory=lambda: _env("PIPEDRIVE_FIELD_POTENTIEL"))
    pipedrive_field_penetration: str | None = field(default_factory=lambda: _env("PIPEDRIVE_FIELD_PENETRATION"))
    pipedrive_field_topgrading: str | None = field(default_factory=lambda: _env("PIPEDRIVE_FIELD_TOPGRADING"))
    pipedrive_field_satisfaction: str | None = field(default_factory=lambda: _env("PIPEDRIVE_FIELD_SATISFACTION"))

    # --- Source SQL (base de données interne ou réplique Serti) ---
    source_db_url: str | None = field(default_factory=lambda: _env("SOURCE_DB_URL"))
    source_query_file: str = field(default_factory=lambda: _env("SOURCE_QUERY_FILE", "requete_ventes.sql"))

    @property
    def pipedrive_enabled(self) -> bool:
        return bool(self.pipedrive_api_token)

    @property
    def pipedrive_base_url(self) -> str:
        if self.pipedrive_domain:
            return f"https://{self.pipedrive_domain}.pipedrive.com/api/v1"
        return "https://api.pipedrive.com/v1"


def get_settings() -> Settings:
    return Settings()
