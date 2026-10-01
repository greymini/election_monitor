"""Knowledge cards keep their constituency through a re-seed.

0014 set ac_id = Giridih on the four Giridih-specific cards, but none of the
card files named an `ac`, and the seed upsert writes `ac_id = EXCLUDED.ac_id`
(NULL) - so every `apply_migrations --seed` turned them into "general" cards
shown on every constituency's Factors page.
"""

from __future__ import annotations

from tests.e2e.conftest import requires_db

pytestmark = requires_db

GIRIDIH_CARDS = {"baseline-2024", "ls-vs-split-2024", "bypoll-context", "geography"}
GENERAL_CARDS = {"caste-guardrails", "data-provenance"}


def test_giridih_cards_stay_giridih_after_seeding(conn, loaded_dataset):
    with conn.cursor() as cur:
        cur.execute("SELECT k.slug, a.ac_number FROM knowledge_card k "
                    "LEFT JOIN ac a ON a.ac_id = k.ac_id")
        by_slug = {r["slug"]: r["ac_number"] for r in cur.fetchall()}
    for slug in GIRIDIH_CARDS:
        assert by_slug[slug] == 32, slug
    for slug in GENERAL_CARDS:
        assert by_slug[slug] is None, slug


def test_another_constituency_does_not_show_giridih_cards(client, tokens):
    headers = {"Authorization": f"Bearer {tokens['admin']}"}
    body = client.get("/acs/42/knowledge-cards", headers=headers).json()
    rows = body["cards"]
    slugs = {c["slug"] for c in rows}
    assert not slugs & GIRIDIH_CARDS
    assert GENERAL_CARDS <= slugs
