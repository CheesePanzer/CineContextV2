"""Run against a locally started demo: python tests/smoke.py."""
import json
import os
import time
import unittest
from urllib.request import Request, urlopen
from uuid import uuid4

BASE = os.environ.get("CINECONTEXT_TEST_URL", "http://127.0.0.1:8765")


def call(path, data=None):
    request = Request(BASE + "/api" + path, data=json.dumps(data).encode() if data is not None else None,
                      headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=6) as response:
        return json.load(response)


def run_prompt(prompt):
    parsed = call("/requests/parse", {"prompt": prompt})
    body = {"constraints": parsed["constraints"], "submission_key": str(uuid4())}
    run_id = call("/runs", body)["run_id"]
    assert call("/runs", body)["run_id"] == run_id
    for _ in range(80):
        result = call(f"/runs/{run_id}")
        if result["status"] in {"completed", "partial", "failed", "interrupted"}:
            return result
        time.sleep(.2)
    raise AssertionError("Task did not reach a terminal state")


class SmokeTests(unittest.TestCase):
    def test_norse_timeline_evidence_and_ratings(self):
        result = run_prompt("Find five films inspired by Norse mythology and show how they relate to the mythological timeline.")
        self.assertEqual(result["status"], "completed", result)
        self.assertEqual(result["model_mode"], "mock")
        self.assertEqual([item["period"][:4] for item in result["study"]["timeline_views"]["release"]],
                         ["1986", "1989", "2011", "2013", "2022"])
        self.assertEqual([item["label"] for item in result["study"]["timeline_views"]["myth"]],
                         ["Creation", "The world of gods and humans", "Ragnarök", "The world renewed"])
        film = next(item for item in result["study"]["films"] if item["slug"] == "ragnarok-2013")
        detail = call(f"/runs/{result['run_id']}/films/{film['id']}")
        self.assertEqual([item["label"] for item in detail["film"]["releases"]], ["2013-10-04", "2014-08-15"])
        self.assertTrue(set(detail["guide"]["reason"]["evidence_ids"]) <= {item["id"] for item in detail["evidence"]})
        ratings = call(f"/films/{film['id']}/ratings")["ratings"]
        self.assertTrue(any(item["metric"] == "tomatometer" and item["provider"] == "demo_snapshot" for item in ratings))

    def test_release_filter_is_distinct_from_story_setting(self):
        result = run_prompt("Find three European films about state surveillance released in the 1980s.")
        self.assertEqual(result["status"], "partial", result)
        self.assertEqual(result["study"]["films"], [])
        barbara = next(item for item in result["study"]["decisions"] if "Barbara" in item["title"])
        self.assertEqual(barbara["status"], "rejected")
        self.assertIn("2012", barbara["reason"])
        self.assertIsNone(result["guide"])

    def test_history_timeline_has_film_links(self):
        result = run_prompt("Find three films about Romanian political memory and show their historical context.")
        self.assertEqual(result["status"], "completed", result)
        events = result["study"]["timeline_views"]["history"]
        self.assertEqual([event["sort_year"] for event in events], [1965, 1966, 1989])
        event = events[-1]
        self.assertEqual(event["sort_year"], 1989)
        self.assertEqual(len(event["film_ids"]), 2)
        self.assertGreaterEqual(len(event["evidence_ids"]), 2)

    def test_unsupported_topic_returns_empty_result(self):
        result = run_prompt("Recommend three films about settling Mars.")
        self.assertEqual(result["status"], "partial", result)
        self.assertEqual(result["study"]["films"], [])


if __name__ == "__main__":
    unittest.main()
