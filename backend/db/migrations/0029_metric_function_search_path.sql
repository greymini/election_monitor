-- Supabase (and some poolers) refresh materialized views with a restricted
-- search_path. PL/pgSQL metric helpers that call sibling functions by bare
-- name then fail at refresh time even though ad-hoc SELECT works.

CREATE OR REPLACE FUNCTION public.metric_margin_pct(
  winner_votes bigint, runner_votes bigint, contestants bigint, valid_votes bigint
) RETURNS numeric
LANGUAGE sql IMMUTABLE SET search_path = public, pg_temp AS $$
  SELECT ROUND((100.0 * public.metric_margin_votes(winner_votes, runner_votes, contestants)
                / NULLIF(valid_votes, 0))::NUMERIC, 2);
$$;

CREATE OR REPLACE FUNCTION public.metric_signed_margin_pct(
  winner_party text, winner_votes bigint, runner_votes bigint, contestants bigint,
  valid_votes bigint, party_a text, party_b text
) RETURNS numeric
LANGUAGE plpgsql IMMUTABLE SET search_path = public, pg_temp AS $fn$
BEGIN
  IF winner_party IS NULL THEN RETURN NULL;
  ELSIF winner_party = party_a THEN
    RETURN public.metric_margin_pct(winner_votes, runner_votes, contestants, valid_votes);
  ELSIF winner_party = party_b THEN
    RETURN -public.metric_margin_pct(winner_votes, runner_votes, contestants, valid_votes);
  END IF;
  RETURN NULL;
END;
$fn$;

CREATE OR REPLACE FUNCTION public.metric_swing_pct(
  share_now numeric, share_prev numeric, crosswalk_confidence real,
  crosswalk_reviewed boolean, lineage_kind text, lineage_aggregated boolean
) RETURNS numeric
LANGUAGE plpgsql IMMUTABLE SET search_path = public, pg_temp AS $fn$
BEGIN
  IF share_now IS NULL OR share_prev IS NULL THEN RETURN NULL; END IF;
  IF NOT public.metric_comparison_allowed(crosswalk_confidence, crosswalk_reviewed,
                                  lineage_kind, lineage_aggregated) THEN
    RETURN NULL;
  END IF;
  RETURN ROUND((share_now - share_prev)::NUMERIC, 2);
END;
$fn$;

CREATE OR REPLACE FUNCTION public.metric_priority_score(
  closeness double precision, new_voter double precision,
  floating double precision, volatility double precision
) RETURNS numeric
LANGUAGE plpgsql IMMUTABLE SET search_path = public, pg_temp AS $fn$
DECLARE w NUMERIC;
BEGIN
  w := public.metric_priority_weight(closeness, new_voter, floating, volatility);
  IF w IS NULL OR w <= 0 THEN RETURN NULL; END IF;
  RETURN ROUND(((COALESCE(0.35 * closeness, 0)
                 + COALESCE(0.25 * new_voter, 0)
                 + COALESCE(0.20 * floating, 0)
                 + COALESCE(0.20 * volatility, 0)) / w)::NUMERIC, 4);
END;
$fn$;

ALTER FUNCTION public.metric_margin_votes(bigint, bigint, bigint)
    SET search_path = public, pg_temp;
