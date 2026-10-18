"""Tests ohne Netzzugriff:  python -m unittest discover tests"""
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from pmtiles.tile import deserialize_directory

from openrisk import __version__, db, mapfetch, places, risk, settings, updater
from openrisk.collectors import NotReady, Context, cityinfo, gdelt, countries, internet, osm_de, weathermaps, wind, aircraft, dwd_stations, dwd_warnings, news, nina, ships, usgs, weather
from openrisk.geo import Geo

GEO_DIR = Path(__file__).resolve().parent.parent / "data" / "geo"


class Classify(unittest.TestCase):
    def test_german(self):
        self.assertEqual(risk.classify("Russland startet Luftangriff auf Kiew", "de"), ("conflict", 3))
        self.assertEqual(risk.classify("Schweres Erdbeben erschüttert Japan", "de"), ("disaster", 3))
        self.assertEqual(risk.classify("Das war ein schöner Tag", "de"), ("general", 0))  # "war" ist kein Krieg

    def test_english(self):
        self.assertEqual(risk.classify("War in Sudan enters third year", "en"), ("conflict", 3))
        self.assertEqual(risk.classify("Minister warns of reforms", "en"), ("general", 0))  # "warns" != "war"
        self.assertEqual(risk.classify("Cholera outbreak spreads", "en"), ("health", 2))


class Feeds(unittest.TestCase):
    def test_rss_and_atom(self):
        rss = b"""<rss><channel><item><title>A &amp; B</title><link>https://x.test/1</link>
            <description>&lt;p&gt;Text&lt;/p&gt;</description><pubDate>Wed, 30 Sep 2026 08:00:00 GMT</pubDate>
            </item></channel></rss>"""
        atom = b"""<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>C</title>
            <link rel="alternate" href="https://x.test/2"/><summary>S</summary>
            <updated>2026-09-30T08:00:00Z</updated></entry></feed>"""
        item = news.parse_feed(rss)[0]
        self.assertEqual((item["title"], item["link"], item["ts"]), ("A & B", "https://x.test/1", 1790755200))
        self.assertEqual(news._clean(item["summary"], 100), "Text")
        item = news.parse_feed(atom)[0]
        self.assertEqual((item["title"], item["link"], item["ts"]), ("C", "https://x.test/2", 1790755200))


class Scoring(unittest.TestCase):
    def test_severity_rules(self):
        self.assertEqual(usgs.severity(4.6, None, 0), 2)
        self.assertEqual(usgs.severity(7.2, None, 1), 4)
        self.assertEqual(usgs.severity(5.0, "red", 0), 4)
        self.assertEqual(weather.assess(20, 30, 0, 3), (0, "", "weather"))
        self.assertEqual(weather.assess(46, 30, 0, 0)[0], 3)
        self.assertEqual(weather.assess(10, 125, 0, 0)[::2], (4, "wind"))

    def test_country_index(self):
        con = sqlite3.connect(":memory:")
        self.addCleanup(con.close)
        con.executescript(db.SCHEMA)
        now = int(time.time())
        db.upsert_events(con, [
            {"id": "a", "source": "t", "category": "news", "title": "x", "country": "AAA", "severity": 4, "ts": now},
            {"id": "b", "source": "t", "category": "news", "title": "x", "country": "BBB", "severity": 1, "ts": now},
            {"id": "c", "source": "t", "category": "news", "title": "x", "country": "CCC", "severity": 0, "ts": now},
        ])
        scores = risk.country_scores(con)
        self.assertEqual([s["iso"] for s in scores], ["AAA", "BBB"])
        self.assertTrue(0 < scores[1]["score"] < scores[0]["score"] <= 100)


class Dwd(unittest.TestCase):
    def test_station_report(self):
        def entry(short, value):
            return {"shortname": short, "value": value}
        feature = {"geometry": {"type": "Point", "coordinates": [8.41, 55.01, 24.7]}, "properties": {
            "abbreviated_heading": "ISGD01", "d0": entry("wigosLocalIdentifierCharacter", "10020   "),
            "d1": entry("stationOrSiteName", "List auf Sylt  "), "d2": entry("year", 2026), "d3": entry("month", 9),
            "d4": entry("day", 30), "d5": entry("hour", 15), "d6": entry("minute", 10),
            "d7": entry("airTemperature", 291.55), "d8": entry("airTemperature", 293.0),  # 2 m, dann 5 cm
            "d9": entry("windSpeed", None), "d10": entry("windSpeed", 10.0), "d11": entry("maximumWindGustSpeed", 20.0),
            "d12": entry("pressureReducedToMeanSeaLevel", 101950)}}
        report = dwd_stations.parse_report(feature)
        self.assertEqual((report["id"], report["name"], report["ts"]), ("10020", "List auf Sylt", 1790781000))
        self.assertEqual((report["temp"], report["wind"], report["gust"], report["pressure"]), (18.4, 36.0, 72.0, 1019.5))
        self.assertIsNone(dwd_stations.parse_report({"geometry": None, "properties": {}}))

    def test_streamed_file_matches_full_json(self):
        import gzip
        import json as js

        def entry(short, value):
            return {"name": short.upper(), "value": value, "shortname": short}
        features = [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [8.41 + i, 55.01, 24.7]},
                     "properties": {"d0": entry("wigosLocalIdentifierCharacter", f"1002{i}   "),
                                    "d1": entry("stationOrSiteName", 'Sylt "Nord"'), "d2": entry("year", 2026),
                                    "d3": entry("month", 9), "d4": entry("day", 30), "d5": entry("hour", 15),
                                    "d6": entry("minute", 10), "d7": entry("airTemperature", None),
                                    "d8": entry("airTemperature", 291.55 + i), "d9": entry("windSpeed", 10),
                                    "d10": entry("pressureReducedToMeanSeaLevel", 1.0195e5)}} for i in range(3)]
        # so ausführlich formatiert wie beim DWD
        raw = gzip.compress(js.dumps({"type": "FeatureCollection", "features": features}, indent=3).encode())
        self.assertEqual(list(dwd_stations.parse_file(raw)), [dwd_stations.parse_report(f) for f in features])
        self.assertEqual(next(dwd_stations.parse_file(raw))["name"], 'Sylt "Nord"')

    def test_station_merge_keeps_last_known_values(self):
        con = sqlite3.connect(":memory:")
        self.addCleanup(con.close)
        con.executescript(db.SCHEMA)
        base = {"id": "1", "name": "A", "lat": 50.0, "lon": 8.0, "elevation": 1.0, "humidity": None,
                "wind": None, "wind_dir": None, "gust": None, "precip": None, "pressure": None}
        dwd_stations.store(con, [{**base, "ts": 200, "temp": 10.0}])
        dwd_stations.store(con, [{**base, "ts": 300, "temp": None, "gust": 50.0}])  # Meldung ohne Temperatur
        dwd_stations.store(con, [{**base, "ts": 100, "temp": 99.0}])  # veraltet
        self.assertEqual(con.execute("SELECT ts, temp, gust FROM stations").fetchone(), (300, 10.0, 50.0))

    def test_warnings(self):
        payload = 'warnWetter.loadWarnings({"time":1,"warnings":{"105315000":[' \
                  '{"level":4,"event":"SCHWERES GEWITTER","description":"Hagel.","start":900000,"end":2000000,' \
                  '"regionName":"x"},{"level":2,"event":"WINDBÖEN","description":"","end":500000,"regionName":"x"}]},' \
                  '"vorabInformation":{"105315000":[{"level":1,"event":"ORKANBÖEN","end":null}]}});'
        events = dwd_warnings.parse(payload, {"105315000": ("Stadt Köln", 50.9, 6.9)}, now=1000)
        by_id = {e["id"]: e for e in events}
        # Windböen abgelaufen; der Beginn gehört zur ID, damit eine spätere Warnung einen eigenen Eintrag bekommt
        self.assertEqual(set(by_id), {"dwd:105315000:SCHWERES GEWITTER:900", "dwd:105315000:ORKANBÖEN:0"})
        storm = by_id["dwd:105315000:SCHWERES GEWITTER:900"]
        self.assertEqual((storm["kind"], storm["severity"], storm["title"], storm["lat"], storm["started"]),
                         ("thunderstorm", 3, "Schweres Gewitter – Stadt Köln", 50.9, 900))
        self.assertTrue(by_id["dwd:105315000:ORKANBÖEN:0"]["title"].startswith("Vorabinformation Orkanböen"))

    def test_regional_warnings_count_once_per_kind(self):
        con = sqlite3.connect(":memory:")
        self.addCleanup(con.close)
        con.executescript(db.SCHEMA)
        now = int(time.time())
        warning = {"source": "DWD", "category": "weather", "kind": "wind", "title": "x", "country": "DEU",
                   "severity": 1, "ts": now}
        db.upsert_events(con, [{**warning, "id": f"dwd:{cell}:WIND"} for cell in range(300)])
        self.assertEqual(risk.country_scores(con)[0]["events"], 1)


class Gdelt(unittest.TestCase):
    @staticmethod
    def row(code="190", sources="2", place="Kyiv, Kyyiv, Misto, Ukraine", root="1", geo="4", url="https://x.test/1",
            actor1="RUSMIL"):
        cols = [""] * 61
        cols[0], cols[1], cols[6], cols[16], cols[25], cols[26] = "1", "20261007", "RUSSIA", "UKRAINE", root, code
        cols[5], cols[15] = actor1, "UKR"
        cols[32], cols[33], cols[51], cols[52], cols[53] = sources, "4", geo, place, "UP"
        cols[56], cols[57], cols[58], cols[59], cols[60] = "50.45", "30.52", "-1044367", "20261007074500", url
        return cols

    def test_filter(self):
        self.assertTrue(gdelt.relevant(self.row()))
        self.assertFalse(gdelt.relevant(self.row(code="043")))  # Besuch, kein Konflikt
        self.assertFalse(gdelt.relevant(self.row(root="0")))  # nur Nebenereignis des Artikels
        self.assertFalse(gdelt.relevant(self.row(geo="1")))  # nur auf das Land genau
        self.assertTrue(gdelt.relevant(self.row(code="1452")))  # gewaltsame Proteste
        self.assertFalse(gdelt.relevant(self.row(actor1="USAGOV")))  # „Kampf“ ohne Bewaffnete: Politik
        self.assertTrue(gdelt.relevant(self.row(code="195", actor1="")))  # Luftangriff zählt immer

    def test_aggregate_until_enough_sources(self):
        con = sqlite3.connect(":memory:")
        con.executescript(gdelt.SCHEMA)
        geo = type("G", (), {"country_at": lambda self, lat, lon: "UKR"})()
        (key,) = gdelt.aggregate(con, [self.row(sources="2")])
        self.assertIsNone(gdelt.to_event(con.execute("SELECT * FROM gdelt_agg").fetchone(), geo))  # 2 Quellen: zu wenig
        gdelt.aggregate(con, [self.row(code="195", sources="1"), self.row(sources="1", url="https://x.test/2")])
        rows = {r[0]: r for r in con.execute("SELECT * FROM gdelt_agg")}
        event = gdelt.to_event(rows[key], geo)
        self.assertEqual((event["kind"], event["severity"], event["country"], event["title"]),
                         ("conflict", 2, "UKR", "Militärische Gewalt: Kyiv"))
        self.assertIn("3 Quellen", event["summary"])
        self.assertEqual(len([k for k in rows if ":195:" in k]), 1)  # Luftangriff getrennt gezählt

    def test_catch_up_window(self):
        newest = 1_791_300_000 - 1_791_300_000 % 900
        self.assertEqual(len(gdelt._stamps(None, newest)), 48 * 4 + 1)
        self.assertEqual(gdelt._stamps(newest - 1800, newest), [newest - 900, newest])


class Radiation(unittest.TestCase):
    def test_traffic_light(self):
        from openrisk.collectors import radiation as r
        self.assertEqual(r.assess(None, 0.1), 0)  # Sonde ohne Messwert
        self.assertEqual(r.assess(0.11, 0.10), 1)  # normal
        self.assertEqual(r.assess(0.15, None), 1)  # noch kein Verlauf: nur feste Grenzen
        self.assertEqual(r.assess(0.21, 0.10), 2)  # verdoppelt (typisch Regen) -> beobachten
        self.assertEqual(r.assess(0.32, None), 2)  # über dem natürlichen Bereich
        self.assertEqual(r.assess(0.35, 0.10), 3)  # mehr als dreifach und über 0,3
        self.assertEqual(r.assess(0.25, 0.08), 2)  # dreifach, aber unter 0,3: noch gelb
        self.assertEqual(r.assess(1.2, 0.5), 3)  # über 1 µSv/h immer rot

    def test_server_fault_is_readable(self):
        import httpx
        from openrisk.collectors import radiation as r
        fault = '<?xml version="1.0"?><ows:ExceptionReport><ows:Exception><ows:ExceptionText>Unable to obtain ' \
                'connection</ows:ExceptionText></ows:Exception></ows:ExceptionReport>'
        http = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, text=fault)))
        self.addCleanup(http.close)
        with self.assertRaisesRegex(RuntimeError, "BfS-Server gestört \\(Datenbank nicht erreichbar\\)"):
            r._wfs(http, r.PARAMS, 10)

    def test_baseline_needs_a_day_of_values(self):
        from openrisk.collectors import radiation as r
        con = sqlite3.connect(":memory:")
        self.addCleanup(con.close)
        con.executescript(db.SCHEMA)
        now = 1_790_000_000
        con.executemany("INSERT INTO radiation_history (id, ts, value, status) VALUES (?, ?, ?, 1)",
                        [("A", now - i * 3600, 100 + (i % 3)) for i in range(30)]
                        + [("B", now - i * 3600, 120) for i in range(10)])
        self.assertEqual(r.baselines(con, now), {"A": 0.101})  # B hat noch zu wenig Werte


class Radon(unittest.TestCase):
    def test_small_municipalities_are_not_simplified_away(self):
        from openrisk.collectors import radon
        tiny = {"type": "Polygon", "coordinates": [[[8.0, 50.0], [8.001, 50.0], [8.001, 50.001], [8.0, 50.001], [8.0, 50.0]]]}
        self.assertEqual(len(radon._rings(tiny)[0][0]), 5)
        big = {"type": "Polygon", "coordinates": [[[8 + i / 1000, 50.0] for i in range(500)]
                                                  + [[8.5, 50.5], [8.0, 50.5], [8.0, 50.0]]]}
        self.assertLess(len(radon._rings(big)[0][0]), 10)  # gerade Kante mit 500 Punkten -> wenige Punkte


class Nina(unittest.TestCase):
    def test_build_warning(self):
        entry = {"id": "mow.X-1", "version": 3, "severity": "Severe", "i18nTitle": {"de": "Großbrand - Musterstadt"}}
        detail = {"info": [{"language": "de", "description": "Fenster<br/>schließen.", "area": [{"areaDesc": "Musterstadt"}]}]}
        shapes = {"features": [{"geometry": {"type": "Polygon", "coordinates": [[[8, 50], [9, 50], [9, 51], [8, 51]]]}}]}
        item = nina.build(entry, "katwarn", detail, shapes)
        event = item["event"]
        self.assertEqual((event["id"], event["source"], event["kind"], event["severity"], event["place"]),
                         ("nina:mow.X-1", "KATWARN", "fire", 3, "Musterstadt"))
        self.assertEqual((event["summary"], event["lat"], event["lon"]), ("Fenster schließen.", 50.5, 8.5))
        self.assertIsNone(event["started"])
        self.assertEqual(item["areas"][0]["properties"], {"id": "nina:mow.X-1", "severity": 3})
        self.assertIsNone(nina.build(entry, "mowas", {}, {})["event"]["lat"])  # ohne Geometrie nur in der Liste


class History(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.addCleanup(self.con.close)
        self.con.executescript(db.SCHEMA)

    def test_lifted_warnings_stay_in_history(self):
        warning = {"source": "NINA", "category": "disaster", "title": "x", "severity": 2, "ts": 100}
        db.upsert_events(self.con, [{**warning, "id": "nina:a"}, {**warning, "id": "nina:b"}])
        db.end_missing(self.con, "nina:", {"nina:a"}, now=200)
        self.assertEqual(self.con.execute("SELECT id, ended FROM events ORDER BY id").fetchall(),
                         [("nina:a", None), ("nina:b", 200)])
        db.upsert_events(self.con, [{**warning, "id": "nina:b", "ts": 300}])  # wieder aktiv
        self.assertIsNone(self.con.execute("SELECT ended FROM events WHERE id = 'nina:b'").fetchone()[0])

    def test_cleanup_respects_history_setting(self):
        now = int(time.time())
        db.upsert_events(self.con, [
            {"id": "alt", "source": "t", "category": "news", "title": "x", "ts": now - 400 * 86400},
            {"id": "neu", "source": "t", "category": "news", "title": "x", "ts": now - 10 * 86400}])
        self.con.execute("INSERT INTO weather_history (place, ts, temp) VALUES ('Berlin', ?, 5)", (now - 400 * 86400,))
        db.cleanup(self.con, 0, 48)  # 0 = unbegrenzt: nichts löschen
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM events").fetchone()[0], 2)
        db.cleanup(self.con, 365, 48)
        self.assertEqual([r[0] for r in self.con.execute("SELECT id FROM events")], ["neu"])
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM weather_history").fetchone()[0], 0)

    def test_nothing_is_deleted_while_offline(self):
        now = int(time.time())
        track = "INSERT INTO track_points (vehicle_id, ts, lat, lon) VALUES ('air:1', ?, 50, 8)"
        self.con.execute(track, (now - 40 * 86400,))
        self.con.execute(track, (now - 20 * 86400,))
        # letzter erfolgreicher Abruf vor 25 Tagen: seitdem offline -> Frist zählt ab dort
        db.set_status(self.con, "aircraft", True, "ok", 1)
        self.con.execute("UPDATE sources SET last_ok = ?", (now - 25 * 86400,))
        db.cleanup(self.con, 365, 30)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM track_points").fetchone()[0], 2)
        self.con.execute("UPDATE sources SET last_ok = ?", (now,))  # wieder online
        db.cleanup(self.con, 365, 30)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM track_points").fetchone()[0], 1)

    def test_risk_index_at_a_past_moment(self):
        now = int(time.time())
        event = {"source": "t", "category": "news", "title": "x", "severity": 4}
        db.upsert_events(self.con, [{**event, "id": "damals", "country": "AAA", "ts": now - 60 * 86400},
                                    {**event, "id": "heute", "country": "BBB", "ts": now}])
        self.assertEqual([s["iso"] for s in risk.country_scores(self.con)], ["BBB"])
        self.assertEqual([s["iso"] for s in risk.country_scores(self.con, at=now - 59 * 86400)], ["AAA"])


class TrackHistory(unittest.TestCase):
    """Flug suchen, Spur eines Tages abrufen, Positionen zu einem früheren Zeitpunkt."""

    def setUp(self):
        from starlette.testclient import TestClient

        from openrisk import config
        from openrisk.server import create_app
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)  # Server hält die DB offen (Windows)
        self.addCleanup(self.tmp.cleanup)
        cfg = config.load(Path(self.tmp.name) / "fehlt.toml")
        cfg["storage"]["data_dir"] = Path(self.tmp.name)
        self.client = TestClient(create_app(cfg, collect=False))
        con = db.connect(Path(self.tmp.name) / "openrisk.db")
        self.addCleanup(con.close)
        con.row_factory = sqlite3.Row
        self.t0 = 1_790_000_000
        for i in range(5):  # GAF618 fliegt fünf Minuten lang nach Norden
            aircraft.store_positions(con, "aircraft", [{"id": "air:3f1234", "callsign": "GAF618", "reg": "54+01",
                                                        "type": "A400", "country": "Deutschland", "lat": 50 + i * 0.1,
                                                        "lon": 8.0, "alt": 20000, "speed": 300, "heading": 0,
                                                        "seen": self.t0 + i * 60}])
        aircraft.store_positions(con, "aircraft", [{"id": "air:3f1234", "callsign": "GAF999", "lat": 51.0, "lon": 8.0,
                                                    "seen": self.t0 + 86400}])  # am nächsten Tag anderes Rufzeichen
        con.commit()

    def test_search_and_track_of_a_day(self):
        day = {"from": self.t0 - 3600, "to": self.t0 + 3600}
        found = self.client.get("/api/vehicle_search", params={"q": "gaf618", **day}).json()["vehicles"]
        self.assertEqual([(v["callsign"], v["type"], v["points"]) for v in found], [("GAF618", "A400", 5)])
        self.assertEqual(self.client.get("/api/vehicle_search", params={"q": "GAF999", **day}).json()["vehicles"], [])
        points = self.client.get("/api/track", params={"id": "air:3f1234", **day}).json()["points"]
        self.assertEqual([p["lat"] for p in points], [50.0, 50.1, 50.2, 50.3, 50.4])
        self.assertEqual(points[0]["speed"], 300)

    def test_positions_at_an_earlier_moment(self):
        past = self.client.get("/api/vehicles", params={"at": self.t0 + 150}).json()["vehicles"]
        self.assertEqual([(v["callsign"], v["lat"], v["kind"]) for v in past], [("GAF618", 50.2, "aircraft")])
        self.assertEqual(self.client.get("/api/vehicles", params={"at": self.t0 + 7200}).json()["vehicles"], [])


class Vehicles(unittest.TestCase):
    def test_lookup_tables(self):
        self.assertEqual(aircraft.country_of("3c6444"), "Deutschland")
        self.assertEqual(aircraft.country_of("ae1234"), "USA")
        self.assertIsNone(aircraft.country_of("zzz"))

    def test_track_points_only_when_moved(self):
        con = sqlite3.connect(":memory:")
        self.addCleanup(con.close)
        con.row_factory = sqlite3.Row
        con.executescript(db.SCHEMA)
        base = {"id": "air:1", "lat": 50.0, "lon": 8.0}
        aircraft.store_positions(con, "aircraft", [{**base, "seen": 100}])
        aircraft.store_positions(con, "aircraft", [{**base, "seen": 200}])  # steht still
        aircraft.store_positions(con, "aircraft", [{**base, "lat": 50.5, "seen": 300}])
        self.assertEqual(con.execute("SELECT COUNT(*) FROM track_points").fetchone()[0], 2)
        self.assertEqual(con.execute("SELECT seen, lat FROM vehicles").fetchone()[:], (300, 50.5))

    def test_ais_message(self):
        message = {"MetaData": {"MMSI": 211123456, "ShipName": "TEST  ", "latitude": 54.3, "longitude": 10.1},
                   "Message": {"ShipStaticData": {"Type": 35, "Dimension": {"A": 100, "B": 39}, "Destination": "KIEL"}}}
        vessel = ships.parse(message, {35})
        self.assertEqual((vessel["id"], vessel["callsign"], vessel["country"]), ("ship:211123456", "TEST", "Deutschland"))
        self.assertEqual(vessel["info"], "139 m · Ziel: KIEL")
        message["Message"]["ShipStaticData"]["Type"] = 70  # Frachter
        self.assertIsNone(ships.parse(message, {35}))


class OfflineMap(unittest.TestCase):
    def test_directory_roundtrip_with_leaves(self):
        directory = mapfetch._Directory()
        for i in range(60_000):  # zu groß für das Wurzelverzeichnis -> Blätter
            directory.add(i * 3, i * 1000 + (i % 7) * 13, 500 + i % 11, 1 + i % 3)
        root, leaves = directory.build()
        self.assertLessEqual(len(root), mapfetch.ROOT_BUDGET)
        entries = []
        for pointer in deserialize_directory(root):
            self.assertEqual(pointer.run_length, 0)
            entries += deserialize_directory(leaves[pointer.offset: pointer.offset + pointer.length])
        self.assertEqual(len(entries), 60_000)
        for i in (0, 1, 4095, 4096, 59_999):
            e = entries[i]
            self.assertEqual((e.tile_id, e.offset, e.length, e.run_length),
                             (directory.ids[i], directory.offsets[i], directory.lengths[i], directory.runs[i]))

    def test_no_zoom_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(settings.map_zoom_limit(Path(tmp)), 15)

    def test_region_tiles(self):
        from pmtiles.tile import tileid_to_zxy
        square = [[[6.0, 49.5], [6.3, 49.5], [6.3, 49.8], [6.0, 49.8], [6.0, 49.5]]]  # etwa Luxemburg
        ids = mapfetch.region_tiles([square], 12)
        self.assertEqual(list(ids), sorted(set(ids)))
        zooms = [tileid_to_zxy(i)[0] for i in ids]
        self.assertEqual(zooms.count(0), 1)
        self.assertEqual(zooms.count(12), 16 * zooms.count(10))  # über Stufe 10 nur Unterkacheln
        x, y = mapfetch._tile_of(6.13, 49.61, 12)
        self.assertIn(mapfetch.zxy_to_tileid(12, x, y), ids)
        x, y = mapfetch._tile_of(2.35, 48.85, 12)  # Paris liegt nicht im Quadrat
        self.assertNotIn(mapfetch.zxy_to_tileid(12, x, y), ids)

    def test_api_keys_stay_on_the_server(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            settings.save(data_dir, {"api_keys": {"aisstream": "  geheim-1234567890  ", "unbekannt": "x"}})
            self.assertEqual(settings.load(data_dir)["api_keys"], {"aisstream": "geheim-1234567890"})
            shown = settings.public(data_dir)["api_keys"][0]
            self.assertEqual((shown["set"], shown["tail"]), (True, "7890"))
            self.assertNotIn("geheim", str(settings.public(data_dir)))
            settings.save(data_dir, {"api_keys": {"aisstream": ""}})  # leer = entfernen
            self.assertEqual(settings.api_key(data_dir, "aisstream", "aus-config"), "aus-config")


@unittest.skipUnless((GEO_DIR / "gazetteer.json").exists(), "tools/fetch_assets.py noch nicht ausgeführt")
class Places(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.geo = Geo(GEO_DIR)

    def test_locate(self):
        self.assertEqual(self.geo.locate("Russland greift Kiew mit Drohnen an").name, "Kiew")  # Stadt vor Land
        self.assertEqual(self.geo.locate("Irans Präsident reist ab").iso, "IRN")  # Genitiv
        self.assertEqual(self.geo.locate("Die chinesische Marine übt").iso, "CHN")  # Adjektiv
        self.assertEqual(self.geo.locate("Tanker attacked in the Red Sea").name, "Rotes Meer")
        self.assertEqual(self.geo.locate("Kein Ort", "Treffen in Paris").name, "Paris")  # Anreißer als Rückfall
        self.assertIsNone(self.geo.locate("Nice weather for a split decision"))

    def test_country_at(self):
        self.assertEqual(self.geo.country_at(52.5, 13.4), "DEU")
        self.assertEqual(self.geo.country_at(35.7, 139.7), "JPN")
        self.assertIsNone(self.geo.country_at(0.0, -30.0))  # Atlantik



class PlaceSearch(unittest.TestCase):
    def test_search(self):
        from openrisk.collectors.places import SCHEMA
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "places.db"
            con = sqlite3.connect(path)
            con.executescript(SCHEMA)
            rows = [(1, "München", "Bayern", 1505005, ["München", "Munich", "Muenchen"]),
                    (2, "Halle (Saale)", "Sachsen-Anhalt", 237865, ["Halle (Saale)", "Halle"]),
                    (3, "Halle", "Nordrhein-Westfalen", 21393, ["Halle"]),
                    (4, "Baia Mare", "Maramureş", 108759, ["Baia Mare", "Neustadt"]),
                    (5, "Neustadt", "Hamburg", 12689, ["Neustadt"])]
            for pid, name, region, pop, names in rows:
                con.execute("INSERT INTO places VALUES (?, ?, ?, 'X', 0, 0, ?, 'PPL', 'X', '', NULL, '')", (pid, name, region, pop))
                con.executemany("INSERT OR IGNORE INTO names VALUES (?, ?)", [(places.normalize(n), pid) for n in names])
            con.commit()
            con.close()
            first = lambda q: places.search(path, q)[0]["name"]  # noqa: E731
            self.assertEqual([first("münchen"), first("Munich"), first("Muenchen"), first("MÜN")], ["München"] * 4)
            self.assertEqual([p["name"] for p in places.search(path, "Halle")], ["Halle (Saale)", "Halle"])
            self.assertEqual(first("Neustadt"), "Neustadt")  # echter Name vor fremdsprachiger Schreibweise
            self.assertEqual(places.search(path, "xyz"), [])
            self.assertIsNone(places.search(Path(tmp) / "fehlt.db", "Halle"))


class CountryInfo(unittest.TestCase):
    def test_collect(self):
        import json

        class Response:
            def __init__(self, text="", data=None):
                self.text, self.data = text, data

            def raise_for_status(self):
                pass

            def json(self):
                return self.data

        info_txt = ("#ISO\tISO3\n" + "\t".join(["DE", "DEU", "276", "GM", "Germany", "Berlin", "357021", "82927922",
                    "EU", ".de", "EUR", "Euro", "49", "", "", "de", "2921044", "CH,PL", ""]) + "\n")

        class Http:
            def get(self, url, timeout=None):
                if "geonames" in url:
                    return Response(info_txt)
                value = 83491249 if "SP.POP.TOTL" in url else None
                return Response(data=[{}, [{"countryiso3code": "DEU", "date": "2025", "value": value}]])

        with tempfile.TemporaryDirectory() as tmp:
            geo = Geo(Path(tmp))
            countries.collect(Context(Http(), None, {}, geo, Path(tmp)))
            de = json.loads((Path(tmp) / "geo" / "country_info.json").read_text(encoding="utf-8"))["DEU"]
        self.assertEqual((de["capital"], de["continent"], de["phone"], de["neighbours"]), ("Berlin", "Europa", "+49", ["CH", "PL"]))
        self.assertEqual((de["population"], de["population_year"]), (83491249, 2025))  # Weltbank vor GeoNames
        self.assertNotIn("gdp", de)  # kein Wert gemeldet


class CityInfo(unittest.TestCase):
    def test_destatis(self):
        import io
        import zipfile
        cells = [["Gemeinden in Deutschland am 30.09.2026 (3. Quartal)"], [], ["Satzart"], [],
                 ["", "", "Gebietsstand", "", "", "", "", "", "31.12.2025 (Jahr)"],
                 ["40", "43", "05", "7", "58", "", "", "Herford"],
                 ["60", "63", "05", "7", "58", "0032", "032", "Spenge, Stadt", "40.35", "14596", "7145", "7451", "362",
                  "32139", "", "", "", "Teutoburger Wald", "", "mittlere Besiedlungsdichte"]]
        strings, rows = [], []
        for r, values in enumerate(cells, 1):
            row = []
            for c, value in enumerate(values):
                strings.append(value)
                row.append(f'<c r="{chr(65 + c)}{r}" t="s"><v>{len(strings) - 1}</v></c>')
            rows.append(f'<row r="{r}">{"".join(row)}</row>')
        ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
        sheet = f'<worksheet {ns}><sheetData>{"".join(rows)}</sheetData></worksheet>' + " " * 1_000_001
        shared = f'<sst {ns}>' + "".join(f"<si><t>{v}</t></si>" for v in strings) + "</sst>"
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as z:
            z.writestr("xl/sharedStrings.xml", shared)
            z.writestr("xl/worksheets/sheet2.xml", sheet)

        class Http:
            def get(self, url, timeout=None):
                return type("R", (), {"status_code": 200 if "GV3Q" in url else 404, "content": buffer.getvalue()})()

        with tempfile.TemporaryDirectory() as tmp:
            gemeinden, kreise, stand = cityinfo.destatis(Http(), Path(tmp))
            self.assertEqual(list(Path(tmp).iterdir()), [])  # heruntergeladene Datei wieder gelöscht
        self.assertEqual(stand, "31.12.2025")
        self.assertEqual(kreise["05758"], {"name": "Herford", "kind": "Kreis"})
        spenge = gemeinden["05758032"]
        self.assertEqual((spenge["type"], spenge["population"], spenge["area_km2"], spenge["plz"], spenge["region"]),
                         ("Stadt", 14596, 40.35, "32139", "Teutoburger Wald"))

    def test_place_near(self):
        from openrisk.collectors.places import SCHEMA
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "places.db"
            con = sqlite3.connect(path)
            con.executescript(SCHEMA)
            for pid, name, lat, pop in [(1, "Neustadt", 52.1, 500), (2, "Neustadt", 49.3, 53000), (3, "Spenge", 52.14, 15625)]:
                con.execute("INSERT INTO places VALUES (?, ?, '', 'Deutschland', ?, 8.4, ?, 'PPL', 'DEU', '', NULL, '')",
                            (pid, name, lat, pop))
                con.execute("INSERT INTO names VALUES (?, ?)", (places.normalize(name), pid))
            con.commit()
            con.close()
            self.assertEqual(places.near(path, "Neustadt", 52.0, 8.5)["id"], 1)  # der nahe, nicht der größte
            self.assertEqual(places.get(path, 3)["name"], "Spenge")
            self.assertIsNone(places.near(path, "Spenge", 40.0, 8.4))  # zu weit weg


class MastsAndBunkers(unittest.TestCase):
    def test_parse(self):
        masts = osm_de.masts([
            {"type": "node", "lat": 52.0, "lon": 8.5, "tags": {"man_made": "mast", "tower:type": "communication",
                                                              "operator": "Deutsche Telekom AG", "communication:lte": "yes"}},
            {"type": "way", "center": {"lat": 52.1, "lon": 8.6}, "tags": {"man_made": "tower", "tower:type": "communication"}},
            {"type": "node", "tags": {}}])  # ohne Lage: weglassen
        self.assertEqual([m["properties"] for m in masts], [
            {"mobile": True, "kind": "Mast", "operator": "Deutsche Telekom AG", "bands": "4G"},
            {"mobile": False, "kind": "Turm"}])
        bunker = osm_de.bunkers([{"type": "way", "center": {"lat": 52.0, "lon": 8.5}, "tags": {
            "military": "bunker", "bunker_type": "air_raid_shelter", "disused:military": "bunker", "location": "underground"}}])
        self.assertEqual(bunker[0]["properties"], {"type": "Luftschutzbunker", "military": True, "lost": True,
                                                   "level": "Tiefbunker"})

    def test_telecom_lines(self):
        lines = osm_de.telecom([
            {"type": "way", "tags": {"telecom": "line", "telecom:medium": "fibre", "location": "underground",
                                     "operator": "Telekom"},
             "geometry": [{"lat": 52.0, "lon": 8.5}, {"lat": 52.1, "lon": 8.6}]},
            {"type": "way", "tags": {"communication": "line"}, "geometry": [{"lat": 52.0, "lon": 8.5}]}])  # zu kurz
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["properties"], {"fibre": True, "operator": "Telekom", "medium": "Glasfaser",
                                                  "location": "unterirdisch"})
        self.assertEqual(lines[0]["geometry"]["type"], "LineString")

    def test_water(self):
        found = osm_de.water([
            {"type": "node", "lat": 49.04, "lon": 8.91, "tags": {"natural": "spring", "name": "Zaberursprung"}},
            {"type": "node", "lat": 49.05, "lon": 8.94, "tags": {"amenity": "drinking_water", "seasonal": "yes"}},
            {"type": "node", "lat": 49.06, "lon": 9.0, "tags": {"man_made": "water_well", "drinking_water": "no",
                                                                 "pump": "manual"}},
            {"type": "node", "lat": 49.0, "lon": 9.0, "tags": {"natural": "tree"}}])  # keine Wasserstelle
        self.assertEqual([f["properties"] for f in found], [
            {"kind": "spring", "type": "Quelle", "name": "Zaberursprung"},
            {"kind": "drinking_water", "type": "Trinkwasserstelle", "drinking": True, "seasonal": True},
            {"kind": "water_well", "type": "Brunnen", "drinking": False, "pump": "Handpumpe"}])


class Internet(unittest.TestCase):
    def test_cables_and_landings(self):
        geo = {"features": [{"properties": {"id": "c1", "name": "Kabel 1", "color": "#123456"},
                             "geometry": {"type": "MultiLineString", "coordinates": [[[1.123456, 2.0], [3.0, 4.0]]]}}]}
        details = {"c1": {"name": "Kabel 1", "is_planned": True, "rfs": "2027", "owners": "A, B",
                          "landing_points": [{"id": "lp1", "name": "Ort A"}, {"id": "lp2", "name": "Ort B"}]}}
        cable = internet.cables(geo, details)[0]["properties"]
        self.assertEqual(cable, {"id": "c1", "name": "Kabel 1", "color": "#123456", "planned": True, "owners": "A, B",
                                 "rfs": "2027", "landings": "Ort A · Ort B"})
        self.assertEqual(internet.cables(geo, {})[0]["geometry"]["coordinates"], [[[1.1235, 2.0], [3.0, 4.0]]])
        lp = internet.landings({"features": [{"properties": {"id": "lp1", "name": "Ort A", "is_tbd": False},
                                              "geometry": {"coordinates": [1, 2]}}]}, details)
        self.assertEqual(lp[0]["properties"], {"id": "lp1", "name": "Ort A", "planned": False, "cables": "Kabel 1",
                                               "count": 1})

    def test_ixp_at_largest_site(self):
        facs = [{"id": 1, "name": "RZ klein", "latitude": 50.0, "longitude": 8.0, "net_count": 5},
                {"id": 2, "name": "RZ groß", "latitude": 50.1, "longitude": 8.7, "net_count": 300, "org_name": "X"},
                {"id": 3, "name": "ohne Lage", "latitude": None, "longitude": None},
                {"id": 4, "name": "RZ Zürich", "latitude": 47.4, "longitude": 8.5, "net_count": 20, "country": "CH"}]
        for fac in facs[:2]:
            fac["country"] = "DE"
        ixs = [{"id": 10, "name": "IX", "net_count": 800, "city": "Frankfurt", "country": "DE"},
               {"id": 11, "name": "ohne Standort", "net_count": 3},
               {"id": 12, "name": "verwaist", "net_count": 0},
               {"id": 13, "name": "Zürich-IX", "net_count": 50, "country": "CH"}]
        ix, dc = internet.peering(ixs, facs, [{"ix_id": 10, "fac_id": 1}, {"ix_id": 10, "fac_id": 2},
                                              {"ix_id": 11, "fac_id": 3}, {"ix_id": 12, "fac_id": 2},
                                              {"ix_id": 13, "fac_id": 2}, {"ix_id": 13, "fac_id": 4}])
        self.assertEqual([i["properties"]["name"] for i in ix], ["IX", "Zürich-IX"])
        self.assertEqual(ix[0]["geometry"]["coordinates"], [8.7, 50.1])  # größter Standort
        self.assertEqual(ix[0]["properties"]["sites"], 2)
        self.assertEqual(ix[1]["geometry"]["coordinates"], [8.5, 47.4])  # im eigenen Land, nicht der größte
        self.assertEqual([d["properties"]["name"] for d in dc], ["RZ groß", "RZ Zürich", "RZ klein"])


class WeatherFilm(unittest.TestCase):
    def test_frames_every_ten_minutes(self):
        analysis = 1_792_132_800  # 06:40 UTC
        times = weathermaps.plan(analysis + 120, 2 * 3600, 2 * 3600)  # krumme Zeit wird auf 10 Minuten gerundet
        self.assertEqual((times[0], times[-1], len(times)), (analysis - 7200, analysis + 7200, 25))
        self.assertEqual(weathermaps.corners((1, 45, 18, 56)), [[1, 56], [18, 56], [18, 45], [1, 45]])
        width, height = weathermaps.size(weathermaps.RADAR["bbox"], 1100)
        self.assertEqual(width, 1100)
        self.assertTrue(1000 < height < 1150)  # Mercator: Deutschland ist etwa so hoch wie breit

    def test_forecast_frames_belong_to_their_run(self):
        import httpx
        png = b"PNG-Bild"
        http = httpx.Client(transport=httpx.MockTransport(
            lambda req: httpx.Response(200, content=png, headers={"content-type": "image/png"})))
        self.addCleanup(http.close)
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            layer = weathermaps._layer(http, folder, "radar", weathermaps.RADAR, [0, 600, 1200], 600, 600)
            self.assertEqual([(f["file"], f["forecast"]) for f in layer["frames"]],
                             [("radar-obs-0.png", False), ("radar-obs-600.png", False), ("radar-fc-1200-600.png", True)])
            self.assertTrue((folder / "radar-fc-1200-600.png").exists())


class Wind(unittest.TestCase):
    def test_components(self):
        self.assertEqual(wind.components(10, 270), (100, 0))  # Westwind weht nach Osten
        self.assertEqual(wind.components(5, 0), (0, -50))  # Nordwind weht nach Süden
        self.assertEqual(wind.components(None, 90), (0, 0))
        lats, lons = wind.grid()
        self.assertEqual((lats[0], lats[-1], lons[0], lons[-1], len(lats) * len(lons)), (46.0, 55.5, 5.0, 16.0, 460))


class Update(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "install"
        for rel, text in {"openrisk/__init__.py": f'__version__ = "{__version__}"', "openrisk/__main__.py": "alt",
                          "openrisk/entfaellt.py": "x", "web/app.js": "alt", "web/vendor/fonts/a.pbf": "schrift",
                          "requirements.txt": "httpx", "data/openrisk.db": "daten", "config.toml": "port = 1"}.items():
            (self.root / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.root / rel).write_text(text)

    def tearDown(self):
        self.tmp.cleanup()

    def package(self, version, files=None, prefix=""):
        import zipfile
        path = Path(self.tmp.name) / f"paket-{version}.zip"
        files = files or {"openrisk/__main__.py": "neu", "web/app.js": "neu", "requirements.txt": "httpx"}
        with zipfile.ZipFile(path, "w") as z:
            z.writestr(f"{prefix}openrisk/__init__.py", f'__version__ = "{version}"')
            for rel, text in files.items():
                z.writestr(prefix + rel, text)
            z.writestr(f"{prefix}../boese.py", "x")  # Pfade außerhalb werden ignoriert
            z.writestr(f"{prefix}data/openrisk.db", "überschrieben")  # Daten gehören nicht ins Paket
        return path

    def test_install_keeps_data_and_vendor(self):
        result = updater.install(self.package("9999.1.1", prefix="OpenRisk-main/"), self.root, pip=self.fail)
        self.assertEqual(result["to"], "9999.1.1")
        read = lambda rel: (self.root / rel).read_text()  # noqa: E731
        self.assertEqual((read("openrisk/__main__.py"), read("web/app.js")), ("neu", "neu"))
        self.assertEqual((read("web/vendor/fonts/a.pbf"), read("data/openrisk.db"), read("config.toml")),
                         ("schrift", "daten", "port = 1"))
        self.assertFalse((self.root / "openrisk/entfaellt.py").exists())
        self.assertFalse((self.root.parent / "boese.py").exists())
        self.assertEqual((self.root / "data/update/vorher/openrisk/__main__.py").read_text(), "alt")

    def test_failed_dependencies_roll_back(self):
        def pip(root):
            raise updater.UpdateError("kein Internet")
        package = self.package("9999.1.1", {"openrisk/__main__.py": "neu", "requirements.txt": "neues-paket"})
        with self.assertRaises(updater.UpdateError):
            updater.install(package, self.root, pip=pip)
        self.assertEqual((self.root / "openrisk/__main__.py").read_text(), "alt")
        self.assertEqual((self.root / "openrisk/entfaellt.py").read_text(), "x")
        self.assertEqual((self.root / "requirements.txt").read_text(), "httpx")

    def test_rejects_old_versions_and_foreign_files(self):
        with self.assertRaises(updater.UpdateError):
            updater.install(self.package("1.0"), self.root)
        foreign = Path(self.tmp.name) / "fremd.zip"
        foreign.write_bytes(b"kein zip")
        with self.assertRaises(updater.UpdateError):
            updater.inspect(foreign)
        (self.root / "tests").mkdir()  # Entwicklungsordner wird nie überschrieben
        with self.assertRaises(updater.UpdateError):
            updater.install(self.package("9999.1.1"), self.root)

    def test_source_folder(self):
        import hashlib
        import json
        source = Path(self.tmp.name) / "quelle"
        source.mkdir()
        package = self.package("9999.2.0")
        (source / "OpenRisk-9999.2.0.zip").write_bytes(package.read_bytes())
        (source / "latest.json").write_text(json.dumps({"version": "9999.2.0", "file": "OpenRisk-9999.2.0.zip",
                                                        "sha256": hashlib.sha256(package.read_bytes()).hexdigest()}))
        latest = updater.check(str(source), http=None)
        self.assertTrue(latest["newer"])
        fetched = updater.fetch(str(source), latest, Path(self.tmp.name) / "geholt.zip", http=None)
        self.assertEqual(updater.inspect(fetched)["version"], "9999.2.0")
        with self.assertRaises(updater.UpdateError):  # beschädigtes Paket
            updater.fetch(str(source), {**latest, "sha256": "0" * 64}, Path(self.tmp.name) / "x.zip", http=None)


if __name__ == "__main__":
    unittest.main()
