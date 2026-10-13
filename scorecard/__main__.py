"""Ligne de commande: python -m scorecard <commande>.

    serve                      démarre l'application web (http://localhost:8000)
    import-excel FICHIER.xlsx  importe un ou des scorecards One-Pager
    import-ventes FICHIER.csv  importe un export de ventes Serti
    import-sql                 exécute SOURCE_QUERY_FILE sur SOURCE_DB_URL
    sync-pipedrive             synchronise tous les clients liés à Pipedrive

Les commandes d'importation et de synchronisation peuvent être planifiées
(Planificateur de tâches Windows ou cron) pour une mise à jour automatique.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy import select

from .config import get_settings
from .db import session_scope


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scorecard", description="Scorecard clients – Camions BL")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="démarrer l'application web")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    sub.add_parser("import-excel", help="importer un scorecard One-Pager").add_argument("fichier", type=Path)
    sub.add_parser("import-ventes", help="importer un CSV de ventes Serti").add_argument("fichier", type=Path)
    sub.add_parser("import-sql", help="importer depuis la base SQL configurée")
    sub.add_parser("sync-pipedrive", help="synchroniser les clients liés à Pipedrive")
    args = parser.parse_args(argv)
    settings = get_settings()

    if args.command == "serve":
        import uvicorn

        uvicorn.run("scorecard.app:app", host=args.host, port=args.port)
        return 0

    if args.command == "import-excel":
        from .importers.onepager import import_onepager

        with session_scope() as session:
            clients = import_onepager(session, args.fichier.read_bytes())
            print(f"{len(clients)} scorecard(s) importé(s): " + ", ".join(c.name for c in clients))
        return 0

    if args.command == "import-ventes":
        from .importers.sales import import_sales_csv

        with session_scope() as session:
            print(import_sales_csv(session, args.fichier.read_bytes()).summary())
        return 0

    if args.command == "import-sql":
        from .importers.sales import import_sales_sql

        if not settings.source_db_url:
            print("SOURCE_DB_URL n'est pas configuré.", file=sys.stderr)
            return 1
        query = Path(settings.source_query_file).read_text(encoding="utf-8")
        with session_scope() as session:
            print(import_sales_sql(session, settings.source_db_url, query).summary())
        return 0

    if args.command == "sync-pipedrive":
        from . import pipedrive
        from .models import Client

        failures = 0
        with session_scope() as session, pipedrive.PipedriveClient(settings) as pd:
            clients = session.scalars(select(Client).where(Client.pipedrive_org_id.is_not(None))).all()
            for client in clients:
                try:
                    pipedrive.sync_client(pd, client, settings.year)
                    print(f"OK  {client.name}")
                except pipedrive.PipedriveError as exc:
                    failures += 1
                    print(f"ERR {client.name}: {exc}", file=sys.stderr)
                session.commit()
        return 1 if failures else 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
