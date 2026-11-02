"""Hourly payload monitor: is today's puzzle (and its neighbours) actually servable?

Checks, for each language, the UTC dates yesterday/today/tomorrow: the Pages URL returns 200
and the body decodes (gzip + JSON) to a payload for that date and language. Also checks that
the live game origin serves its shell and that manifest.json parses. Players' "today" comes
from their device clock, so UTC±1 day covers every timezone.

Runway: counts published dates after today (UTC) per language in manifest.json and warns when
fewer than RUNWAY_DAYS remain -- the 2026-08-19 outage was a schedule that simply ran out.

Alerting is one GitHub issue labelled `payload-alert`: opened on the first problem, its body
edited in place while the problem persists (no comment per run), and closed with a single
comment on recovery. Needs `gh` and GH_TOKEN with issues:write.

Env: FORCE_FAIL=1 adds a check for a date that is never published (end-to-end alert test).
"""

from __future__ import annotations

import gzip
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, date, datetime, timedelta

PAGES = "https://gaurav-gandhi-2411.github.io/mindmeld-payloads"
GAME = "https://playwarmer.vercel.app/"
LANGS = ("en", "hi-en")
RUNWAY_DAYS = 14
LABEL = "payload-alert"
TITLE = "Warmer payload alert"


def fetch(url: str, attempts: int = 3) -> tuple[int, bytes]:
    """GET with retry + backoff so one transient blip does not open an issue."""
    last: tuple[int, bytes] = (0, b"")
    for i in range(attempts):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "warmer-payload-monitor"})
            with urllib.request.urlopen(req, timeout=20) as r:  # noqa: S310 -- fixed https URLs
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            last = (e.code, b"")
            if e.code == 404:
                return last  # a missing file is not transient
        except (urllib.error.URLError, TimeoutError) as e:
            last = (0, str(e).encode())
        time.sleep(2**i)
    return last


def check_payload(lang: str, d: date) -> str | None:
    url = f"{PAGES}/{lang}/{d.isoformat()}.json.gz"
    status, body = fetch(url)
    if status != 200:
        return f"{lang} {d}: HTTP {status or 'error'} for {url}"
    try:
        raw = gzip.decompress(body) if body[:2] == b"\x1f\x8b" else body
        p = json.loads(raw)
    except (OSError, ValueError) as e:
        return f"{lang} {d}: body does not decode ({e})"
    if p.get("date") != d.isoformat() or p.get("lang") != lang or not p.get("words"):
        return f"{lang} {d}: decoded payload is for {p.get('date')}/{p.get('lang')}, not {d}/{lang}"
    return None


def run_checks(today: date, force_fail: bool) -> tuple[list[str], list[str], list[str]]:
    """Returns (failures, warnings, passed-check lines)."""
    failures: list[str] = []
    warnings: list[str] = []
    ok: list[str] = []
    for lang in LANGS:
        for d in (today - timedelta(days=1), today, today + timedelta(days=1)):
            err = check_payload(lang, d)
            (failures.append(err) if err else ok.append(f"{lang} {d}: 200, decodes"))
    if force_fail:
        err = check_payload("en", date(1999, 1, 1))
        failures.append(f"FORCED (alert test): {err or 'unexpectedly passed'}")
    status, body = fetch(GAME)
    if status != 200 or b"flutter_bootstrap.js" not in body:
        failures.append(f"game origin {GAME}: HTTP {status or 'error'} or app shell missing")
    else:
        ok.append("game origin: 200, app shell present")
    status, body = fetch(f"{PAGES}/manifest.json")
    try:
        langs = json.loads(body)["langs"] if status == 200 else {}
    except (ValueError, KeyError):
        langs = {}
    for lang in LANGS:
        days = [x.get("date", "") for x in langs.get(lang, {}).get("days", [])]
        if not days:
            failures.append(f"manifest.json: no '{lang}' section (HTTP {status})")
            continue
        future = sum(1 for x in days if x > today.isoformat())
        last = max(days)
        line = f"{lang}: {future} future day(s) published, last {last}"
        if future < RUNWAY_DAYS:
            warnings.append(f"runway low -- {line} (< {RUNWAY_DAYS}); publish the next drop")
        else:
            ok.append(f"runway {line}")
    return failures, warnings, ok


def gh(*args: str) -> str:
    repo = os.environ["GITHUB_REPOSITORY"]
    return subprocess.run(
        ["gh", *args, "--repo", repo], capture_output=True, text=True, check=True
    ).stdout.strip()


def open_alert_number() -> str | None:
    out = gh(
        "issue",
        "list",
        "--label",
        LABEL,
        "--state",
        "open",
        "--json",
        "number",
        "--jq",
        ".[0].number // empty",
    )
    return out or None


def sync_issue(failures: list[str], warnings: list[str], ok: list[str], run_url: str) -> str:
    existing = open_alert_number()
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    if not failures and not warnings:
        if existing:
            gh(
                "issue",
                "comment",
                existing,
                "--body",
                f"Recovered at {now}: all checks pass. {run_url}",
            )
            gh("issue", "close", existing)
            return f"closed #{existing}"
        return "no issue"
    severity = "DOWN" if failures else "runway low"
    body = "\n".join(
        [
            f"**{severity}** -- last checked {now} ([run]({run_url})).",
            "",
            "Failing:",
            *(f"- {f}" for f in failures or ["(none)"]),
            "",
            "Warnings:",
            *(f"- {w}" for w in warnings or ["(none)"]),
            "",
            "Passing:",
            *(f"- {o}" for o in ok),
            "",
            "_Updated in place each hour while open; closed automatically on recovery._",
        ]
    )
    title = f"{TITLE}: {severity}"
    if existing:
        gh("issue", "edit", existing, "--title", title, "--body", body)
        return f"updated #{existing}"
    gh(
        "label",
        "create",
        LABEL,
        "--color",
        "B60205",
        "--force",
        "--description",
        "Automated: hourly payload monitor",
    )
    url = gh("issue", "create", "--title", title, "--label", LABEL, "--body", body)
    return f"opened {url}"


def main() -> int:
    today = datetime.now(UTC).date()
    force = os.environ.get("FORCE_FAIL") == "1"
    failures, warnings, ok = run_checks(today, force)
    for line in ok:
        print(f"ok    {line}")
    for line in warnings:
        print(f"::warning::{line}")
    for line in failures:
        print(f"::error::{line}")
    if os.environ.get("GITHUB_ACTIONS") == "true":
        run_url = (
            f"{os.environ['GITHUB_SERVER_URL']}/{os.environ['GITHUB_REPOSITORY']}"
            f"/actions/runs/{os.environ['GITHUB_RUN_ID']}"
        )
        print("issue:", sync_issue(failures, warnings, ok, run_url))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
