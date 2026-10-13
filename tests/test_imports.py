import datetime as dt
from pathlib import Path

from scorecard.importers.onepager import import_onepager
from scorecard.importers.sales import import_sales_csv
from scorecard.metrics import build_scorecard, find_metrics, potential, used_services
from scorecard.models import PROJETE, REEL

EXAMPLE = Path(__file__).parents[1] / "exemples"


def test_import_onepager_reads_all_sections(session):
    [client] = import_onepager(session, (EXAMPLE / "Scorecard_Client_OnePager.xlsx").read_bytes())
    assert (client.name, client.serti_code) == ("JMF Transport (Fictif)", "TRAJMF")

    m2024 = find_metrics(client, 2024, REEL)
    assert (m2024.revenue, m2024.gross_margin, m2024.service_margin_pct) == (154877, 42588, 0.77)
    assert find_metrics(client, 2026, PROJETE).revenue == 180000
    assert find_metrics(client, 2026, REEL).revenue == 44788

    assert used_services(client, 2024, REEL) == {"pieces", "service", "saaq", "jockey"}
    assert used_services(client, 2026, PROJETE) == {"pieces", "service", "saaq"}

    pot = potential(client, 2026)
    assert (pot.potential, pot.current, pot.gap) == (850000, 150000, 700000)
    assert round(pot.penetration, 4) == round(150000 / 850000, 4)

    grades = {a.year: a.topgrading for a in client.assessments}
    assert grades == {2024: "A", 2025: "A", 2026: "A"}
    assert "Danny" in next(a for a in client.assessments if a.year == 2026).strengths

    assert [a.owner for a in client.actions] == ["Maxime", "Pascal", "Samuel J."]
    assert client.actions[0].due_date == dt.date(2025, 11, 1)
    assert client.actions[1].status == "fait" and client.actions[1].done_date == dt.date(2025, 8, 1)
    assert client.reviews[0].quarter == 1 and "Meritor" in client.reviews[0].notes


def test_import_onepager_twice_does_not_duplicate_actions(session):
    data = (EXAMPLE / "Scorecard_Client_OnePager.xlsx").read_bytes()
    import_onepager(session, data)
    [client] = import_onepager(session, data)
    assert len(client.actions) == 3


def test_scorecard_difference_and_global_margin(session):
    [client] = import_onepager(session, (EXAMPLE / "Scorecard_Client_OnePager.xlsx").read_bytes())
    sc = build_scorecard(client, 2026, today=dt.date(2026, 3, 1))
    revenue = sc["rows"][0]
    assert revenue["diff"] == 180000 - 44788
    global_pct = sc["rows"][4]["values"]
    assert round(global_pct["2024_reel"], 3) == round(42588 / 154877, 3)
    assert sc["overdue_actions"] == 2  # actions 1 (2025-11-01) et 3 (2026-01-31) en retard


def test_import_sales_csv_aggregates_departments(session):
    report = import_sales_csv(session, (EXAMPLE / "ventes_serti_exemple.csv").read_bytes())
    assert report.metrics_updated == 2 and report.clients_created == 2
    from scorecard.models import Client
    from sqlalchemy import select

    jmf = session.scalar(select(Client).where(Client.serti_code == "TRAJMF"))
    m = find_metrics(jmf, 2025, REEL)
    assert m.revenue == 120500 + 22400 + 4999
    assert m.gross_margin == 25305 + 17248 + 3849
    assert m.parts_margin_pct == round(25305 / 120500, 4)
    assert m.source == "serti"
    assert used_services(jmf, 2025, REEL) == {"pieces", "service", "saaq"}


def test_import_sales_csv_with_cost_and_date_columns(session):
    csv = "Client,Date facture,Dept,Ventes,Cout\nABC,2025-03-04,Atelier,1000,300\nABC,2025-05-01,Inconnu,500,400\n"
    report = import_sales_csv(session, csv.encode("cp1252"))
    assert report.unknown_departments == {"Inconnu"}
    from scorecard.models import Client
    from sqlalchemy import select

    client = session.scalar(select(Client).where(Client.serti_code == "ABC"))
    m = find_metrics(client, 2025, REEL)
    assert (m.revenue, m.gross_margin, m.service_margin_pct) == (1500, 800, 0.7)
