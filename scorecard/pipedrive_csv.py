"""Export CSV du plan d'action pour l'importation d'activités dans Pipedrive.

Sert quand l'API n'est pas configurée, ou pour un chargement initial en lot:
Pipedrive › Importer des données › Feuille de calcul › Activités, puis associer
chaque colonne au champ correspondant (Sujet, Type, Date d'échéance, Organisation,
Assigné à l'utilisateur, Note, Fait).
"""
from __future__ import annotations

import csv
import io
from collections.abc import Iterable

from .models import ACTION_STATUS_LABELS, Action, Client

HEADERS = ["Sujet", "Type", "Date d'échéance", "Organisation", "Assigné à", "Note", "Fait"]


def actions_csv(clients: Iterable[Client], open_only: bool = True) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";")
    writer.writerow(HEADERS)
    for client in clients:
        for action in client.actions:
            if open_only and not action.is_open:
                continue
            writer.writerow(_row(client, action))
    # BOM UTF-8: Excel et Pipedrive lisent correctement les accents.
    return ("﻿" + buffer.getvalue()).encode("utf-8")


def _row(client: Client, action: Action) -> list[str]:
    note = [f"Plan d'action – scorecard client {client.name}", f"Statut: {ACTION_STATUS_LABELS[action.status]}"]
    if client.serti_code:
        note.append(f"Code Serti: {client.serti_code}")
    if action.follow_up:
        note.append(f"Suivi: {action.follow_up}")
    return [
        action.title,
        "Tâche",
        action.due_date.isoformat() if action.due_date else "",
        client.name,
        action.owner or "",
        " | ".join(note),
        "Oui" if action.status in ("fait", "annule") else "Non",
    ]
