-- 0024: news scope - Jharkhand-wide political news, tagged to constituencies.
--
-- The crawler kept only items matching a Giridih keyword and never wrote
-- `news_item.ac_ids`, while /news filters on it, so nothing it collected could
-- appear on any page. News is now collected for Jharkhand politics as a whole
-- and each item says which ACs it concerns:
--
--   scope          'ac' when at least one constituency matched, else 'state'
--                  (Jharkhand political news with no specific seat).
--   relevance      0-1, how squarely the item is about elections/politics in
--                  the matched place. Set by the crawler from the terms it hit
--                  and refined by the labeller.
--   matched_terms  the keywords that matched, so a reader - or a reviewer
--                  tuning the lists - can see why an item was kept and tagged.
--   label_method   'rules' (news/label_rules.py, keywords only) or 'llm'
--                  (news/label_batch.py). NULL until labelled. A rules label is
--                  re-labelled by the LLM once a key is configured.
--   feed           which configured source found the item. `source` now holds
--                  the publisher (Jagran, Amar Ujala, ...), which is what a
--                  reader wants to see; Google News is not a publisher.

ALTER TABLE news_item
    ADD COLUMN scope         TEXT CHECK (scope IN ('ac', 'state')),
    ADD COLUMN relevance     REAL CHECK (relevance BETWEEN 0 AND 1),
    ADD COLUMN matched_terms TEXT[],
    ADD COLUMN label_method  TEXT CHECK (label_method IN ('rules', 'llm')),
    ADD COLUMN feed          TEXT;

-- Items already labelled came from the LLM path, the only one that existed.
UPDATE news_item SET label_method = 'llm' WHERE labelled_at IS NOT NULL;
UPDATE news_item SET scope = CASE WHEN cardinality(ac_ids) > 0 THEN 'ac' ELSE 'state' END;

CREATE INDEX news_item_scope_idx ON news_item (scope, published DESC);
