import json
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from pathlib import Path

from app import ReliefGridApp, ReliefGridHTTPServer


class ReliefGridApiTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = Path(self.tempdir.name) / "test.db"
        self.app = ReliefGridApp(self.database)
        self.server = ReliefGridHTTPServer(("127.0.0.1", 0), self.app)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.tempdir.cleanup()

    def request(self, path, method="GET", payload=None, headers=None):
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = Request(
            self.base + path,
            data=data,
            method=method,
            headers={"Content-Type": "application/json", **(headers or {})},
        )
        try:
            response = urlopen(request, timeout=3)
        except HTTPError as error:
            response = error
        body = response.read()
        payload = json.loads(body) if body and response.headers.get_content_type() == "application/json" else body
        return response.code, response.headers, payload

    def test_health_and_demo_seed_data(self):
        status, _, health = self.request("/api/health")
        self.assertEqual(status, 200)
        self.assertTrue(health["ok"])
        self.assertEqual(health["mode"], "demo")

        status, _, result = self.request("/api/incidents")
        self.assertEqual(status, 200)
        self.assertEqual(result["total"], 3)
        self.assertEqual(len(result["items"]), 3)
        self.assertEqual(result["items"][0]["severity"], "critical")

    def test_dashboard_and_resource_inventory(self):
        status, _, dashboard = self.request("/api/dashboard")
        self.assertEqual(status, 200)
        self.assertEqual(dashboard["active_incidents"], 3)
        self.assertEqual(dashboard["critical_incidents"], 1)
        self.assertEqual(dashboard["available_units"], 246)

        status, _, resources = self.request("/api/resources")
        self.assertEqual(status, 200)
        self.assertEqual(len(resources), 3)
        self.assertTrue(all(item["available"] <= item["capacity"] for item in resources))

    def test_submit_report_and_persist_it(self):
        payload = {
            "kind": "Road obstruction",
            "description": "A tree is blocking the west access road.",
            "district": "West District",
            "severity": "high",
        }
        status, _, created = self.request("/api/incidents", method="POST", payload=payload)
        self.assertEqual(status, 201)
        self.assertTrue(created["code"].startswith("RG-"))
        self.assertEqual(created["status"], "active")

        status, _, result = self.request("/api/incidents?q=West%20District")
        self.assertEqual(status, 200)
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0]["code"], created["code"])

        reopened_app = ReliefGridApp(self.database)
        self.assertEqual(reopened_app.dashboard()["active_incidents"], 4)

    def test_invalid_report_is_rejected_without_inserting(self):
        status, _, error = self.request(
            "/api/incidents",
            method="POST",
            payload={"kind": "Flooding", "description": "short", "district": "North", "severity": "urgent"},
        )
        self.assertEqual(status, 400)
        self.assertIn("Description", error["error"])
        self.assertEqual(self.app.dashboard()["active_incidents"], 3)

    def test_bad_content_type_and_invalid_json_are_rejected(self):
        status, _, error = self.request(
            "/api/incidents", method="POST", payload={"anything": True}, headers={"Content-Type": "text/plain"}
        )
        self.assertEqual(status, 415)
        self.assertIn("Content-Type", error["error"])

        request = Request(self.base + "/api/incidents", data=b"{bad", method="POST", headers={"Content-Type": "application/json"})
        try:
            urlopen(request, timeout=3)
        except HTTPError as response:
            status = response.code
            error = json.loads(response.read())
        self.assertEqual(status, 400)
        self.assertIn("valid JSON", error["error"])

    def test_filters_and_incident_status_updates(self):
        status, _, result = self.request("/api/incidents?severity=critical&status=active")
        self.assertEqual(status, 200)
        self.assertEqual(result["total"], 1)
        critical = result["items"][0]

        status, _, updated = self.request(
            f"/api/incidents/{critical['id']}", method="PATCH", payload={"status": "resolved", "severity": "medium"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(updated["status"], "resolved")
        self.assertEqual(updated["severity"], "medium")

        status, _, result = self.request("/api/incidents?status=resolved")
        self.assertEqual(status, 200)
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0]["id"], critical["id"])
        self.assertEqual(self.app.dashboard()["active_incidents"], 2)

    def test_unknown_incident_and_route_return_json_errors(self):
        status, _, error = self.request("/api/incidents/99999", method="PATCH", payload={"status": "resolved"})
        self.assertEqual(status, 404)
        self.assertIn("not found", error["error"])

        status, _, error = self.request("/missing")
        self.assertEqual(status, 404)
        self.assertIn("Route not found", error["error"])

    def test_html_and_static_assets_have_security_headers(self):
        status, headers, body = self.request("/")
        self.assertEqual(status, 200)
        self.assertIn(b"ReliefGrid", body)
        self.assertEqual(headers.get("X-Content-Type-Options"), "nosniff")
        self.assertIn("default-src 'self'", headers.get("Content-Security-Policy"))
        status, headers, body = self.request("/static/app.js")
        self.assertEqual(status, 200)
        self.assertIn(b"loadIncidents", body)
        self.assertEqual(headers.get_content_type(), "text/javascript")


if __name__ == "__main__":
    unittest.main()
