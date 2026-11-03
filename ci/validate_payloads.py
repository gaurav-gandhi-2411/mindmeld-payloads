"""Validate payload changes between two commits. Stdlib only; run in CI on every PR and push.

Two rules, both hard failures:

1. Immutability. A dated puzzle file ({lang}/YYYY-MM-DD.json.gz) that exists at the base
   commit may never be modified or deleted. Published dates are what players already played;
   a byte change silently rewrites history and breaks shared results.
2. Content. Every added or modified .json.gz must decode (gzip + JSON) and satisfy:
   - the schema (required keys and types);
   - ranks: `words` maps n distinct hashes to ranks that are exactly 1..n, and `n` matches;
   - the decoded answer hashes to the rank-1 entry (so it is in the vocab);
   - `alt` ranks are in 1..n and never collide with `words` keys;
   - hints point at the rank stored for that word;
   - no blocklisted word (ci/blocklist.txt) appears hashed in `words`/`alt`, or as a token in
     the clue or hints.

Also checks that manifest.json (when changed) lists files that exist with matching byte sizes.

Usage: python ci/validate_payloads.py <base-sha> [<head-sha>]   (head defaults to HEAD)
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATED = re.compile(r"^[a-z]{2}(-[a-z]{2})?/\d{4}-\d{2}-\d{2}\.json\.gz$")
REQUIRED = {
    "date": str,
    "lang": str,
    "v": int,
    "n": int,
    "salt": str,
    "model": str,
    "maxGuesses": int,
    "parGuesses": int,
    "hints": list,
    "words": dict,
    "clue": str,
    "s": str,
}
# The client's answer obfuscation key. It ships in the public web bundle; not a secret.
OBFUSCATION_KEY = "mindmeld-secret-v1"
TOKEN = re.compile(r"[^\W\d_]+")


def word_hash(word: str, salt: str) -> str:
    return hashlib.sha1(f"{word.lower()}{salt}".encode()).hexdigest()[:12]  # noqa: S324


def decode_answer(p: dict) -> str:
    raw = base64.b64decode(p["s"])
    seed = hashlib.sha1((p["salt"] + OBFUSCATION_KEY).encode()).digest()  # noqa: S324
    return bytes(b ^ seed[i % 20] for i, b in enumerate(raw)).decode()


def load_blocklist() -> set[str]:
    lines = (ROOT / "ci" / "blocklist.txt").read_text(encoding="utf-8").splitlines()
    return {ln.strip().lower() for ln in lines if ln.strip() and not ln.startswith("#")}


def validate_payload(p: object, blocklist: set[str], path: str = "") -> list[str]:
    """Return a list of human-readable errors (empty means valid). Pure; unit-tested."""
    if not isinstance(p, dict):
        return [f"{path}: top level is {type(p).__name__}, expected object"]
    errs = [
        f"{path}: missing or wrong-typed key '{k}' (want {t.__name__})"
        for k, t in REQUIRED.items()
        if not isinstance(p.get(k), t)
    ]
    if errs:
        return errs
    m = DATED.match(path)
    if m and (p["date"] != path.rsplit("/", 1)[1][:10] or p["lang"] != path.split("/", 1)[0]):
        errs.append(f"{path}: date/lang fields {p['date']}/{p['lang']} do not match the path")
    if not re.fullmatch(r"[0-9a-f]{16}", p["salt"]):
        errs.append(f"{path}: salt is not 16 hex chars")
    words, alt, n, salt = p["words"], p.get("alt") or {}, p["n"], p["salt"]
    try:
        ranks = [v[0] for v in words.values()]
    except (TypeError, IndexError, KeyError):
        return [*errs, f"{path}: words values must be [rank, ...] lists"]
    if len(words) != n or sorted(ranks) != list(range(1, n + 1)):
        errs.append(
            f"{path}: ranks are not a permutation of 1..n "
            f"(n={n}, entries={len(words)}, distinct ranks={len(set(ranks))})"
        )
    try:
        answer = decode_answer(p)
    except (ValueError, UnicodeDecodeError) as e:
        return [*errs, f"{path}: answer does not decode: {e}"]
    if words.get(word_hash(answer, salt), [None])[0] != 1:
        errs.append(f"{path}: decoded answer is not the rank-1 vocab entry")
    for k, v in alt.items():
        if k in words:
            errs.append(f"{path}: alt key {k} collides with a words key")
        if not (isinstance(v, list) and v and isinstance(v[0], int) and 1 <= v[0] <= n):
            errs.append(f"{path}: alt rank out of range for {k}")
    for h in p["hints"]:
        if not (isinstance(h, list) and len(h) >= 2):
            errs.append(f"{path}: malformed hint {h!r}")
            continue
        if words.get(word_hash(str(h[1]), salt), [None])[0] != h[0]:
            errs.append(f"{path}: hint '{h[1]}' rank {h[0]} does not match words")
    single = {w for w in blocklist if " " not in w}
    blocked_hashes = {word_hash(w, salt) for w in single}
    hits = [k for k in (*words, *alt) if k in blocked_hashes]
    if hits:
        errs.append(f"{path}: {len(hits)} blocklisted word(s) hashed in words/alt")
    text = " ".join([p["clue"], *(str(h[1]) for h in p["hints"] if isinstance(h, list))]).lower()
    tokens = set(TOKEN.findall(text))
    bad = sorted((tokens & single) | {w for w in blocklist if " " in w and w in text})
    if bad:
        errs.append(f"{path}: blocklisted token(s) in clue/hints: {len(bad)}")
    if answer.lower() in blocklist:
        errs.append(f"{path}: the answer itself is blocklisted")
    return errs


def git(*args: str) -> bytes:
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, check=True).stdout


def changed_files(base: str, head: str) -> list[tuple[str, str]]:
    """(status, path) pairs. Renames are split into a delete of the old path + add of the new."""
    out = git("diff", "--name-status", "--no-renames", base, head).decode()
    return [(ln.split("\t")[0][0], ln.split("\t")[-1]) for ln in out.splitlines() if ln.strip()]


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    base, head = argv[0], (argv[1] if len(argv) > 1 else "HEAD")
    errors: list[str] = []
    changes = changed_files(base, head)
    blocklist = load_blocklist()
    checked = 0
    for status, path in changes:
        if DATED.match(path) and status in ("M", "D"):
            verb = "modified" if status == "M" else "deleted"
            errors.append(f"{path}: published date file {verb} -- published dates are immutable")
            continue
        if path.endswith(".json.gz") and status in ("A", "M"):
            try:
                p = json.loads(gzip.decompress(git("show", f"{head}:{path}")))
            except (OSError, ValueError, subprocess.CalledProcessError) as e:
                errors.append(f"{path}: does not decode as gzip JSON: {e}")
                continue
            errors.extend(validate_payload(p, blocklist, path))
            checked += 1
    if any(path == "manifest.json" for _, path in changes):
        manifest = json.loads(git("show", f"{head}:manifest.json"))
        for lang, entry in manifest.get("langs", {}).items():
            for day in entry.get("days", []):
                try:
                    size = len(git("show", f"{head}:{day['file']}"))
                except subprocess.CalledProcessError:
                    errors.append(
                        f"manifest.json: {lang} {day.get('date')} lists missing {day['file']}"
                    )
                    continue
                if size != day.get("bytes"):
                    errors.append(
                        f"manifest.json: {day['file']} bytes {day.get('bytes')} != {size}"
                    )
    print(f"changed paths: {len(changes)}; payloads validated: {checked}; errors: {len(errors)}")
    for e in errors:
        print(f"::error::{e}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
