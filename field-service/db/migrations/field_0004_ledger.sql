-- field_0004: the ledger (LLD §3.2; LLD 0021). All kinds and columns, so slice 1c needs no table change.
-- pulse/ledger/service.py is the only application code that writes field.money_txn.

CREATE TABLE field.money_txn (
    txn_id             uuid           PRIMARY KEY DEFAULT gen_random_uuid(),
    txn_no             text           NOT NULL UNIQUE,         -- e.g. 32-E-000412
    ac_id              text           NOT NULL,
    kind               text           NOT NULL CHECK (kind IN ('inflow', 'advance', 'expense', 'return', 'adjustment')),
    amount             numeric(12, 2) NOT NULL CHECK (amount > 0),
    txn_date           date           NOT NULL,                -- when it happened (Asia/Kolkata calendar date)
    from_member_id     uuid           NULL REFERENCES field.member (member_id),
    to_member_id       uuid           NULL REFERENCES field.member (member_id),
    bucket_id          integer        NULL REFERENCES field.exp_bucket (bucket_id),
    custom_label       text           NULL CHECK (char_length(custom_label) BETWEEN 1 AND 40),
    block_id           text           NULL,
    area_id            text           NULL,
    fund_source        text           NULL CHECK (fund_source IN ('own', 'family_business', 'party', 'supporter_cash',
                                                  'supporter_in_kind', 'advance', 'own_pocket_reimbursable', 'other')),
    pay_mode           text           NULL CHECK (pay_mode IN ('cash', 'upi', 'bank_transfer', 'cheque', 'credit', 'in_kind')),
    payee_kind         text           NULL CHECK (payee_kind IN ('vendor', 'individual', 'institution', 'group')),
    payee_label        text           NULL,                    -- PII-screened
    reach_count        integer        NULL CHECK (reach_count > 0),
    reach_unit         text           NULL CHECK (reach_unit IN ('person', 'family', 'village', 'event')),
    remark             text           NULL,                    -- PII-screened
    receipt_asset_id   uuid           NULL,                    -- media_asset arrives in slice 2a; no FK yet
    report_id          uuid           NULL,
    issue_id           uuid           NULL,                    -- core public_issue; linked at merge
    political_event_id text           NULL,                    -- core political_event; linked at merge
    in_election_window boolean        NOT NULL,
    counts_to_ceiling  boolean        NULL,                    -- set by treasurer; NULL = not yet decided
    suggest_source     text           NULL CHECK (suggest_source IN ('user', 'rule', 'llm')),
    suggest_conf       numeric(4, 3)  NULL CHECK (suggest_conf BETWEEN 0 AND 1),
    status             text           NOT NULL CHECK (status IN ('posted', 'pending_ack', 'disputed', 'void')),
    flagged            text[]         NOT NULL DEFAULT '{}'
                       CHECK (flagged <@ ARRAY['large', 'cash_over_limit', 'sensitive_in_window', 'backdated']::text[]),
    voids_txn_id       uuid           NULL REFERENCES field.money_txn (txn_id),  -- this entry replaces that voided one
    void_reason        text           NULL,
    voided_by          uuid           NULL REFERENCES field.member (member_id),
    voided_at          timestamptz    NULL,
    created_by         uuid           NOT NULL REFERENCES field.member (member_id),
    created_via        text           NOT NULL CHECK (created_via IN ('telegram', 'web')),
    created_at         timestamptz    NOT NULL DEFAULT now(),

    -- per-kind invariants (LLD §3.2 table)
    CONSTRAINT money_txn_expense_shape CHECK (kind <> 'expense' OR
        (from_member_id IS NOT NULL AND to_member_id IS NULL AND bucket_id IS NOT NULL AND suggest_source IS NOT NULL)),
    CONSTRAINT money_txn_advance_shape CHECK (kind <> 'advance' OR
        (from_member_id IS NOT NULL AND to_member_id IS NOT NULL AND from_member_id <> to_member_id AND bucket_id IS NULL)),
    CONSTRAINT money_txn_return_shape CHECK (kind <> 'return' OR
        (from_member_id IS NOT NULL AND to_member_id IS NOT NULL AND from_member_id <> to_member_id AND bucket_id IS NULL)),
    CONSTRAINT money_txn_inflow_shape CHECK (kind <> 'inflow' OR
        (from_member_id IS NULL AND to_member_id IS NOT NULL AND bucket_id IS NULL AND fund_source IS NOT NULL)),
    CONSTRAINT money_txn_adjustment_shape CHECK (kind <> 'adjustment' OR
        ((from_member_id IS NOT NULL OR to_member_id IS NOT NULL) AND bucket_id IS NULL
         AND remark IS NOT NULL AND btrim(remark) <> '')),
    -- only transfers inside the team wait for acknowledgement
    CONSTRAINT money_txn_status_by_kind CHECK (kind IN ('advance', 'return') OR status IN ('posted', 'void')),
    CONSTRAINT money_txn_label_only_on_expense CHECK (custom_label IS NULL OR kind = 'expense'),
    CONSTRAINT money_txn_reach_pair CHECK ((reach_count IS NULL) = (reach_unit IS NULL)),
    CONSTRAINT money_txn_conf_needs_machine CHECK (suggest_conf IS NULL OR suggest_source IN ('rule', 'llm')),
    CONSTRAINT money_txn_void_fields CHECK ((status = 'void') =
        (voided_at IS NOT NULL AND voided_by IS NOT NULL AND void_reason IS NOT NULL)),
    CONSTRAINT money_txn_not_replacing_self CHECK (voids_txn_id IS DISTINCT FROM txn_id)
);
CREATE INDEX money_txn_ac_date_idx ON field.money_txn (ac_id, txn_date);
CREATE INDEX money_txn_from_idx ON field.money_txn (from_member_id);
CREATE INDEX money_txn_to_idx ON field.money_txn (to_member_id);
CREATE INDEX money_txn_bucket_idx ON field.money_txn (bucket_id);

-- Append-only: no DELETE; UPDATE may only touch status/void fields, bucket re-classification (with
-- the flags it implies, e.g. sensitive_in_window) and the treasurer's ceiling decision.
CREATE FUNCTION field.money_txn_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'money_txn is append-only: void instead of deleting' USING ERRCODE = 'restrict_violation';
    END IF;
    IF (NEW.txn_id, NEW.txn_no, NEW.ac_id, NEW.kind, NEW.amount, NEW.txn_date, NEW.from_member_id, NEW.to_member_id,
        NEW.block_id, NEW.area_id, NEW.fund_source, NEW.pay_mode, NEW.payee_kind, NEW.payee_label,
        NEW.reach_count, NEW.reach_unit, NEW.remark, NEW.receipt_asset_id, NEW.report_id, NEW.issue_id,
        NEW.political_event_id, NEW.in_election_window, NEW.voids_txn_id,
        NEW.created_by, NEW.created_via, NEW.created_at)
       IS DISTINCT FROM
       (OLD.txn_id, OLD.txn_no, OLD.ac_id, OLD.kind, OLD.amount, OLD.txn_date, OLD.from_member_id, OLD.to_member_id,
        OLD.block_id, OLD.area_id, OLD.fund_source, OLD.pay_mode, OLD.payee_kind, OLD.payee_label,
        OLD.reach_count, OLD.reach_unit, OLD.remark, OLD.receipt_asset_id, OLD.report_id, OLD.issue_id,
        OLD.political_event_id, OLD.in_election_window, OLD.voids_txn_id,
        OLD.created_by, OLD.created_via, OLD.created_at) THEN
        RAISE EXCEPTION 'money_txn is append-only: only status, void, bucket re-classification and ceiling may change'
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF OLD.status = 'void' THEN
        RAISE EXCEPTION 'money_txn: a void entry cannot change' USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER money_txn_append_only BEFORE UPDATE OR DELETE ON field.money_txn
    FOR EACH ROW EXECUTE FUNCTION field.money_txn_append_only();

-- voids_txn_id ("this entry replaces that one") may only point at a void entry of the same AC.
CREATE FUNCTION field.money_txn_replaces_void() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.voids_txn_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM field.money_txn WHERE txn_id = NEW.voids_txn_id AND status = 'void' AND ac_id = NEW.ac_id) THEN
        RAISE EXCEPTION 'money_txn: voids_txn_id must reference a void entry of the same AC'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER money_txn_replaces_void BEFORE INSERT ON field.money_txn
    FOR EACH ROW EXECUTE FUNCTION field.money_txn_replaces_void();

-- Per-AC, per-series counter for txn_no. Incremented with INSERT … ON CONFLICT DO UPDATE, whose row
-- lock serialises concurrent writers; a rolled-back insert rolls the counter back too (no gaps).
CREATE TABLE field.txn_counter (
    ac_id   text   NOT NULL,
    series  text   NOT NULL CHECK (series IN ('E', 'A', 'L', 'I', 'J')),
    last_no bigint NOT NULL CHECK (last_no > 0),
    PRIMARY KEY (ac_id, series)
);

CREATE TABLE field.txn_audit (
    audit_id        bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    txn_id          uuid        NOT NULL REFERENCES field.money_txn (txn_id),
    at              timestamptz NOT NULL DEFAULT now(),
    actor_member_id uuid        NOT NULL REFERENCES field.member (member_id),
    action          text        NOT NULL CHECK (action IN ('void', 'reclassify', 'ack', 'dispute', 'ceiling', 'export')),
    old             jsonb       NULL,
    new             jsonb       NULL,
    note            text        NULL
);
CREATE INDEX txn_audit_txn_idx ON field.txn_audit (txn_id);
