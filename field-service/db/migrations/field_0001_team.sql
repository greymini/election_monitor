-- field_0001: field schema, team tree and member scopes (LLD §2.1; LLD migration 0018).
-- Core ids (ac_id, user_id, block_id, area_id) are plain text columns holding the core's ids,
-- validated through CoreAdapter; no foreign keys leave the field schema.

CREATE SCHEMA IF NOT EXISTS field;

CREATE TABLE field.member (
    member_id        uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    ac_id            text        NOT NULL,
    user_id          text        NOT NULL UNIQUE,
    display_name     text        NOT NULL CHECK (btrim(display_name) <> ''),
    parent_member_id uuid        NULL REFERENCES field.member (member_id),
    is_principal     boolean     NOT NULL DEFAULT false,
    finance_scope    text        NOT NULL DEFAULT 'none' CHECK (finance_scope IN ('none', 'own', 'team', 'all')),
    can_give_advance boolean     NOT NULL DEFAULT false,
    active           boolean     NOT NULL DEFAULT true,
    created_at       timestamptz NOT NULL DEFAULT now(),
    deactivated_at   timestamptz NULL,
    -- reports-to is NULL only for the principal
    CONSTRAINT member_parent_iff_not_principal CHECK (is_principal = (parent_member_id IS NULL)),
    CONSTRAINT member_not_own_parent CHECK (parent_member_id IS DISTINCT FROM member_id),
    CONSTRAINT member_deactivated_at_matches CHECK (active = (deactivated_at IS NULL))
);

-- exactly one principal per AC
CREATE UNIQUE INDEX member_one_principal_per_ac ON field.member (ac_id) WHERE is_principal;
CREATE INDEX member_ac_idx ON field.member (ac_id);
CREATE INDEX member_parent_idx ON field.member (parent_member_id);

-- one row per block or panchayat/ward a member handles
CREATE TABLE field.member_scope (
    member_id uuid NOT NULL REFERENCES field.member (member_id),
    block_id  text NULL,
    area_id   text NULL,
    CONSTRAINT member_scope_has_target CHECK (block_id IS NOT NULL OR area_id IS NOT NULL),
    CONSTRAINT member_scope_unique UNIQUE NULLS NOT DISTINCT (member_id, block_id, area_id)
);
CREATE INDEX member_scope_member_idx ON field.member_scope (member_id);
