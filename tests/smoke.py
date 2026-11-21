"""Run against a locally started demo: python tests/smoke.py."""
import json
import os
import time
import unittest
from urllib.request import Request, urlopen
from uuid import uuid4

BASE = os.environ.get("CINECONTEXT_TEST_URL", "http://127.0.0.1:8765")
PROMPTS = {
    "medieval": "I'm interested in Kingdom Come: Deliverance II. Can you recommend a few films about medieval European history?",
    "romanian": "I'm interested in the Romanian New Wave. Which films should I watch?",
    "italian": "I'm interested in Italian neorealism. Which films should I watch?",
    "spiderman": "I'm getting ready to watch Spider-Man: Brand New Day. Please recommend four films to watch beforehand to catch up on the necessary background and story.",
}
EDITORIAL_SITES = {"Criterion", "Senses of Cinema", "BFI", "BFI / Sight and Sound"}


def call(path, data=None):
    request = Request(
        BASE + "/api" + path,
        data=json.dumps(data).encode() if data is not None else None,
        headers={"Content-Type": "application/json"},
    )
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
    @classmethod
    def setUpClass(cls):
        if call("/health")["model_mode"] != "mock":
            raise RuntimeError("Start CineContext in demo mode or set CINECONTEXT_TEST_URL to the demo instance.")

    def check_guide(self, case, titles):
        result = run_prompt(PROMPTS[case])
        self.assertEqual(result["status"], "completed", result.get("error"))
        self.assertEqual(result["model_mode"], "mock")
        self.assertEqual(result["constraints"]["count"], len(titles))
        films = {film["id"]: film for film in result["study"]["films"]}
        self.assertEqual({film["title"] for film in films.values()}, set(titles))
        self.assertEqual(len(films), len(titles))
        self.assertEqual([films[film_id]["title"] for film_id in result["guide"]["viewing_order"]], titles)
        release_years = [int(event["period"][:4]) for event in result["study"]["timeline_views"]["release"]]
        self.assertEqual(release_years, sorted(film["year"] for film in films.values()))
        self.assertEqual({item["film_id"] for item in result["guide"]["film_guides"]}, set(films))
        evidence = {source["id"]: source for source in result["study"]["evidence"]}
        for guide in result["guide"]["film_guides"]:
            refs = [evidence[source_id] for source_id in guide["reason"]["evidence_ids"]]
            self.assertTrue(refs)
            self.assertTrue(all(source["film_id"] == guide["film_id"] for source in refs))
            self.assertTrue(any(source["kind"] == "editorial" and source["locator"] in EDITORIAL_SITES
                                for source in refs), refs)
        return result

    def check_four_sources(self, result, title):
        film = next(item for item in result["study"]["films"] if item["title"] == title)
        run_id, film_id = result["run_id"], film["id"]
        detail = call(f"/runs/{run_id}/films/{film_id}")
        self.assertTrue(detail["guide"]["reason"]["text"])
        self.assertTrue(detail["film"]["tmdb_id"])
        readings = call(f"/runs/{run_id}/films/{film_id}/readings")["readings"]
        sources = {"BFI" if row["publication"] == "BFI / Sight and Sound" else row["publication"]
                   for row in readings}
        self.assertTrue({"Criterion", "BFI", "Senses of Cinema"} <= sources)
        ratings = call(f"/films/{film_id}/ratings")["ratings"]
        self.assertTrue(any(row["origin"] == "TMDB" and row["provider"] == "demo_snapshot"
                            and 0 < row["value"] <= 10 for row in ratings))

    def test_medieval_europe(self):
        result = self.check_guide("medieval", ["Medieval", "The Passion of Joan of Arc", "Marketa Lazarová"])
        self.assertEqual(result["constraints"]["setting_match"], "nearby")
        story = result["study"]["timeline_views"]["story"]
        film = next(item for item in result["study"]["films"] if item["title"] == "Medieval")
        self.assertEqual([(event["period"], event["film_ids"]) for event in story],
                         [("1402, Bohemia", [film["id"]])])

    def test_romanian_new_wave(self):
        result = self.check_guide("romanian", ["4 Months, 3 Weeks and 2 Days", "Tuesday, After Christmas", "Child's Pose"])
        self.check_four_sources(result, "4 Months, 3 Weeks and 2 Days")

    def test_italian_neorealism(self):
        result = self.check_guide("italian", ["Rome, Open City", "Bicycle Thieves", "Umberto D."])
        self.check_four_sources(result, "Bicycle Thieves")

    def test_spiderman_brand_new_day(self):
        result = self.check_guide(
            "spiderman",
            ["Spider-Man: Homecoming", "Avengers: Endgame", "Spider-Man: Far From Home", "Spider-Man: No Way Home"],
        )
        story = result["study"]["timeline_views"]["story"]
        self.assertEqual(len(story), 4)


if __name__ == "__main__":
    unittest.main()
