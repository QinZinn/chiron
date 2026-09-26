"""KNOWN limits of trigram duplicate detection — locked in by tests, the threshold is NOT tuned.

Blind tuning is guessing; lowering the threshold surely brings false negatives elsewhere.
The upgrade decision (pgvector/embeddings) waits for real data from Mnemosyne.

EVIDENCE-POINT RULE (see NOTES.md): every number must come with BOTH strings
VERBATIM, the pg_trgm version, and the DB collation. A bare number cannot be
reinterpreted later — we nearly paid for exactly that once.

The strings are Vietnamese physics terms from before the English switch; they
stay verbatim because the recorded numbers depend on their exact characters.
"""

from __future__ import annotations

import pytest

from ks import settings
from ks.ingest import ingest_concepts
from ks.models import ConceptDraft, SourceModule


def _sim(conn, a: str, b: str) -> float:
    with conn.cursor() as cur:
        cur.execute("SELECT similarity(%s, %s)", (a, b))
        return float(cur.fetchone()[0])


def _draft(title: str) -> ConceptDraft:
    return ConceptDraft(title, "Vật lý", "x", SourceModule.MNEMOSYNE)


# ---------------------------------------------------------------- environment fingerprint


def test_environment_fingerprint_of_the_evidence_points(conn):
    """The numbers below only mean something in this environment. Red = the environment changed,
    NOT broken code — read NOTES.md before changing any number."""
    with conn.cursor() as cur:
        cur.execute("SELECT extversion FROM pg_extension WHERE extname = 'pg_trgm'")
        assert cur.fetchone()[0] == "1.6"
        cur.execute(
            "SELECT datcollate, datctype FROM pg_database WHERE datname = current_database()"
        )
        assert cur.fetchone() == ("en_US.UTF-8", "en_US.UTF-8")


# ---------------------------------------------------------------- evidence point


@pytest.mark.parametrize(
    "a, b, expected, merged",
    [
        # The previous build's three original evidence points, measured on the verbatim strings.
        ("Định luật Newton 1", "Định luật Newton 2", 0.8095, True),
        ("Định luật khúc xạ ánh sáng", "Định luật phản xạ ánh sáng", 0.6774, True),
        ("Định luật Ohm (curl-nodes)", "Định luật Newton 2 (curl-nodes)", 0.6364, True),
        # The same concepts without the shared prefix/suffix → drop below the threshold.
        # Evidence that similarity depends on the SHARED part, not the differing part.
        ("khúc xạ", "phản xạ", 0.2308, False),
        ("Định luật Ohm", "Định luật Newton 2", 0.4348, False),
    ],
)
def test_evidence_point_similarity(conn, a, b, expected, merged):
    score = _sim(conn, a, b)
    assert score == pytest.approx(expected, abs=0.001)
    assert (score >= settings.DUPLICATE_THRESHOLD) is merged


# ---------------------------------------------------------------- wrong-merge behaviour


def test_numbered_variants_are_wrongly_deduped(conn):
    """KNOWN BUG: two different laws merge into one because their names differ by a single digit."""
    ingest_concepts(conn, [_draft("Định luật Newton 1")])
    result = ingest_concepts(conn, [_draft("Định luật Newton 2")])
    assert result.ingested[0].created is False, (
        "If this test is red: dedup behaviour changed; read NOTES.md before fixing"
    )


def test_opposite_concepts_merge_when_titles_share_a_suffix(conn):
    """khúc xạ (refraction) and phản xạ (reflection) are OPPOSITE phenomena, yet merge — because the prefix
    'Định luật ' ("law of") plus the suffix ' ánh sáng' ("of light") make up most trigrams (0.6774)."""
    ingest_concepts(conn, [_draft("Định luật khúc xạ ánh sáng")])
    result = ingest_concepts(conn, [_draft("Định luật phản xạ ánh sáng")])
    assert result.ingested[0].created is False


def test_a_SHARED_SUFFIX_is_the_most_dangerous_thing(conn):
    """The same pair of concepts: without the shared suffix they do NOT merge, with it they MERGE.

    The differing part is identical both times — only the SHARED part changes. Real titles
    from Mnemosyne easily carry a shared suffix (chapter, subject, question-set names), so
    this is the false-positive path most worth watching once there is real data.
    """
    without_suffix = ingest_concepts(
        conn, [_draft("Định luật Ohm"), _draft("Định luật Newton 2")]
    ).ingested
    assert [i.created for i in without_suffix] == [True, True]

    with_suffix = ingest_concepts(
        conn,
        [_draft("Định luật Ohm (curl-nodes)"), _draft("Định luật Newton 2 (curl-nodes)")],
    ).ingested
    assert [i.created for i in with_suffix] == [True, False]


def test_merge_threshold_is_still_0_6(conn):
    """Locks the constant: changing the threshold is Agent A's decision, not the code's."""
    assert settings.DUPLICATE_THRESHOLD == 0.6
