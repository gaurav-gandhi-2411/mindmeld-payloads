"""Tests for ci/validate_payloads.py. Stdlib unittest: python -m unittest discover -s ci"""

from __future__ import annotations

import base64
import copy
import gzip
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import validate_payloads as vp  # noqa: E402

SALT = "0123456789abcdef"
VOCAB = ["dream", "sleep", "night", "pillow", "cinema", "badword"]
BLOCK = {"badword", "two words"}


def encode_answer(answer: str, salt: str) -> str:
    seed = hashlib.sha1((salt + vp.OBFUSCATION_KEY).encode()).digest()  # noqa: S324
    return base64.b64encode(bytes(b ^ seed[i % 20] for i, b in enumerate(answer.encode()))).decode()


def make_payload(vocab: list[str] = VOCAB[:5], date: str = "2026-11-02", lang: str = "en") -> dict:
    return {
        "date": date,
        "lang": lang,
        "v": 1,
        "n": len(vocab),
        "salt": SALT,
        "model": "m",
        "maxGuesses": 12,
        "parGuesses": 8,
        "clue": "The mind's own cinema",
        "hints": [[2, vocab[1]]],
        "rival": [],
        "words": {vp.word_hash(w, SALT): [i + 1] for i, w in enumerate(vocab)},
        "s": encode_answer(vocab[0], SALT),
    }


class ValidatePayloadTest(unittest.TestCase):
    def check(self, p: dict) -> list[str]:
        return vp.validate_payload(p, BLOCK, "en/2026-11-02.json.gz")

    def test_valid_payload_passes(self) -> None:
        self.assertEqual(self.check(make_payload()), [])

    def test_missing_key_fails(self) -> None:
        p = make_payload()
        del p["words"]
        self.assertTrue(any("words" in e for e in self.check(p)))

    def test_path_date_mismatch_fails(self) -> None:
        self.assertTrue(
            any("do not match the path" in e for e in self.check(make_payload(date="2026-11-03")))
        )

    def test_duplicate_rank_fails(self) -> None:
        p = make_payload()
        first, second = list(p["words"])[:2]
        p["words"][second] = [1]  # two words at rank 1, rank 2 missing
        self.assertTrue(any("permutation" in e for e in self.check(p)))

    def test_n_mismatch_fails(self) -> None:
        p = make_payload()
        p["n"] = 6
        self.assertTrue(any("permutation" in e for e in self.check(p)))

    def test_answer_not_rank_one_fails(self) -> None:
        p = make_payload()
        p["s"] = encode_answer("sleep", SALT)
        self.assertTrue(any("rank-1" in e for e in self.check(p)))

    def test_blocklisted_word_in_vocab_fails(self) -> None:
        p = make_payload(vocab=VOCAB)  # includes "badword" at rank 6
        self.assertTrue(any("blocklisted word(s) hashed" in e for e in self.check(p)))

    def test_blocklisted_alt_key_fails(self) -> None:
        p = make_payload()
        p["alt"] = {vp.word_hash("badword", SALT): [3]}
        self.assertTrue(any("hashed in words/alt" in e for e in self.check(p)))

    def test_blocklisted_clue_token_fails(self) -> None:
        p = make_payload()
        p["clue"] = "a badword clue"
        self.assertTrue(any("clue/hints" in e for e in self.check(p)))

    def test_blocklisted_phrase_in_clue_fails(self) -> None:
        p = make_payload()
        p["clue"] = "this has two words in it"
        self.assertTrue(any("clue/hints" in e for e in self.check(p)))

    def test_alt_collision_fails(self) -> None:
        p = make_payload()
        p["alt"] = {list(p["words"])[2]: [3]}
        self.assertTrue(any("collides" in e for e in self.check(p)))

    def test_hint_rank_mismatch_fails(self) -> None:
        p = make_payload()
        p["hints"] = [[4, "sleep"]]
        self.assertTrue(any("hint" in e for e in self.check(p)))

    def test_hostile_non_object_fails(self) -> None:
        self.assertTrue(vp.validate_payload(["not", "a", "dict"], BLOCK, "x.json.gz"))


class ImmutabilityTest(unittest.TestCase):
    """End-to-end through main() against a throwaway git repo."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.orig_root = vp.ROOT
        vp.ROOT = self.root
        self.git("init", "-q")
        self.git("config", "user.email", "ci@example.invalid")
        self.git("config", "user.name", "ci")
        (self.root / "ci").mkdir()
        (self.root / "ci" / "blocklist.txt").write_text("# test\nbadword\n", encoding="utf-8")
        self.write("en/2026-11-01.json.gz", make_payload(date="2026-11-01"))
        self.base = self.commit("base")

    def tearDown(self) -> None:
        vp.ROOT = self.orig_root
        self.tmp.cleanup()

    def git(self, *args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(self.root), *args], capture_output=True, check=True, text=True
        ).stdout.strip()

    def write(self, rel: str, payload: dict) -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(gzip.compress(json.dumps(payload).encode(), mtime=0))

    def commit(self, msg: str) -> str:
        self.git("add", "-A")
        self.git("commit", "-q", "-m", msg)
        return self.git("rev-parse", "HEAD")

    def test_adding_a_valid_new_date_passes(self) -> None:
        self.write("en/2026-11-02.json.gz", make_payload())
        self.commit("add")
        self.assertEqual(vp.main([self.base]), 0)

    def test_modifying_a_published_date_fails(self) -> None:
        p = make_payload(date="2026-11-01")
        p["clue"] = "changed"
        self.write("en/2026-11-01.json.gz", p)
        self.commit("modify")
        self.assertEqual(vp.main([self.base]), 1)

    def test_deleting_a_published_date_fails(self) -> None:
        (self.root / "en" / "2026-11-01.json.gz").unlink()
        self.commit("delete")
        self.assertEqual(vp.main([self.base]), 1)

    def test_renaming_a_published_date_fails(self) -> None:
        self.git("mv", "en/2026-11-01.json.gz", "en/2026-11-09.json.gz")
        self.commit("rename")
        self.assertEqual(vp.main([self.base]), 1)

    def test_adding_an_invalid_new_date_fails(self) -> None:
        bad = make_payload()
        bad["words"] = copy.deepcopy(bad["words"])
        bad["words"][list(bad["words"])[1]] = [1]
        self.write("en/2026-11-02.json.gz", bad)
        self.commit("bad add")
        self.assertEqual(vp.main([self.base]), 1)

    def test_manifest_byte_mismatch_fails(self) -> None:
        (self.root / "manifest.json").write_text(
            json.dumps(
                {
                    "langs": {
                        "en": {
                            "days": [
                                {"date": "2026-11-01", "file": "en/2026-11-01.json.gz", "bytes": 1}
                            ]
                        }
                    }
                }
            )
        )
        self.commit("manifest")
        self.assertEqual(vp.main([self.base]), 1)


if __name__ == "__main__":
    unittest.main()
