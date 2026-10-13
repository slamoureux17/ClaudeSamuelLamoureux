import datetime as dt
import json

import httpx

from scorecard import pipedrive
from scorecard.config import Settings
from scorecard.models import Action, Assessment, Client, QuarterlyReview


class FakePipedrive:
    def __init__(self):
        self.calls = []
        self.activities = {501: {"id": 501, "done": True, "marked_as_done_time": "2026-02-03 10:00:00"}}

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        path, method = request.url.path.removeprefix("/api").removeprefix("/v1"), request.method
        self.calls.append((method, path, body))
        assert request.url.params["api_token"] == "tok"
        if path == "/users":
            data = [{"id": 7, "name": "Maxime Tremblay", "active_flag": True},
                    {"id": 8, "name": "Pascal Roy", "active_flag": True}]
        elif path == "/activities" and method == "POST":
            data = {"id": 900 + len(self.calls)}
        elif path.startswith("/activities/") and method == "GET":
            data = self.activities.get(int(path.split("/")[-1]), {"done": False})
        elif path == "/notes" and method == "POST":
            data = {"id": 77}
        elif path == "/organizations/42" and method == "GET":
            data = {"id": 42, "name": "JMF Transport", "owner_id": {"name": "Samuel"},
                    "next_activity_date": "2026-11-01", "last_activity_date": "2026-10-01"}
        elif path == "/organizations/42/deals":
            return httpx.Response(200, json={"success": True, "data": [
                {"id": 1, "title": "Location remorques", "value": 25000, "status": "open"},
                {"id": 2, "title": "Contrat Meritor", "value": 10000, "status": "won", "won_time": "2026-04-01 12:00:00"},
                {"id": 3, "title": "Vieux", "value": 5000, "status": "won", "won_time": "2024-01-01 12:00:00"},
            ], "additional_data": {"pagination": {"more_items_in_collection": False}}})
        elif path == "/organizations/search":
            data = {"items": [{"item": {"id": 42, "name": "JMF Transport (Fictif)"}},
                              {"item": {"id": 43, "name": "JMF Transport Inc"}}]}
        else:
            data = {}
        return httpx.Response(200, json={"success": True, "data": data})


def make_client():
    client = Client(name="JMF Transport (Fictif)", serti_code="TRAJMF", pipedrive_org_id=42,
                    potential_annual=850000, current_revenue_override=150000)
    client.actions = [
        Action(id=1, title="Reprendre les SAAQ", owner="Maxime", due_date=dt.date(2026, 11, 1), status="a_faire"),
        Action(id=2, title="Déjà dans Pipedrive", owner="Pascal", status="a_faire", pipedrive_activity_id=501),
        Action(id=3, title="Fermée sans activité", status="fait"),
    ]
    client.reviews = [QuarterlyReview(year=2026, quarter=1, notes="3 rencontres")]
    client.assessments = [Assessment(year=2026, topgrading="A", satisfaction=4)]
    return client


def test_sync_client_pushes_and_pulls():
    fake = FakePipedrive()
    settings = Settings(pipedrive_api_token="tok", pipedrive_domain="camionsbl",
                        pipedrive_field_penetration="abc123", pipedrive_field_topgrading="def456")
    client = make_client()
    with pipedrive.PipedriveClient(settings, transport=httpx.MockTransport(fake.handler)) as pd:
        snapshot = pipedrive.sync_client(pd, client, 2026)

    created = [body for method, path, body in fake.calls if method == "POST" and path == "/activities"]
    assert len(created) == 1  # seule l'action ouverte sans activité est créée
    assert created[0]["subject"] == "Reprendre les SAAQ"
    assert created[0]["user_id"] == 7 and created[0]["org_id"] == 42 and created[0]["due_date"] == "2026-11-01"
    assert client.actions[0].pipedrive_activity_id is not None
    assert client.actions[2].pipedrive_activity_id is None

    # L'activité 501 était faite dans Pipedrive -> action fermée, puis mise à jour (done=1)
    assert client.actions[1].status == "fait" and client.actions[1].done_date == dt.date(2026, 2, 3)
    updated = [body for method, path, body in fake.calls if method == "PUT" and path == "/activities/501"]
    assert updated[0]["done"] == 1

    assert client.reviews[0].pipedrive_note_id == 77
    org_update = [body for method, path, body in fake.calls if method == "PUT" and path == "/organizations/42"]
    assert org_update == [{"abc123": 17.6, "def456": "A"}]

    assert snapshot["open_value"] == 25000
    assert snapshot["won_count_year"] == 1 and snapshot["won_value_year"] == 10000
    assert snapshot["org"]["url"] == "https://camionsbl.pipedrive.com/organization/42"


def test_find_organization_prefers_exact_name():
    fake = FakePipedrive()
    settings = Settings(pipedrive_api_token="tok")
    with pipedrive.PipedriveClient(settings, transport=httpx.MockTransport(fake.handler)) as pd:
        result = pipedrive.find_organization(pd, Client(name="JMF Transport (fictif)"))
    assert [org["id"] for org in result] == [42]


def test_user_matching_is_not_ambiguous():
    fake = FakePipedrive()
    with pipedrive.PipedriveClient(Settings(pipedrive_api_token="tok"),
                                   transport=httpx.MockTransport(fake.handler)) as pd:
        assert pd.user_id_for("maxime") == 7
        assert pd.user_id_for("Pascal R.") == 8
        assert pd.user_id_for("Inconnu") is None
