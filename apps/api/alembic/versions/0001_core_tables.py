"""Core tables: organizations, app_users, vendors, memberships, audit_log.

Tables, constraints and indexes only. Row level security, grants and triggers are added in
0003 (after the helper functions of 0002), and nothing is usable by vt_app until then.

Revision ID: 0001_core_tables
Revises:
"""

from alembic import op

revision = "0001_core_tables"
down_revision = None
branch_labels = None
depends_on = None

ROLES = "'org_admin', 'analyst', 'reviewer', 'auditor', 'vendor_user'"

UPGRADE = f"""
CREATE TABLE vt.organizations (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name        text NOT NULL,
    status      text NOT NULL DEFAULT 'active',
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_organizations_name CHECK (length(btrim(name)) BETWEEN 1 AND 200),
    CONSTRAINT ck_organizations_status CHECK (status IN ('active', 'suspended'))
);

-- id is the Supabase Auth subject; it is never generated here.
CREATE TABLE vt.app_users (
    id            uuid PRIMARY KEY,
    email         text NOT NULL,
    display_name  text,
    status        text NOT NULL DEFAULT 'active',
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_app_users_email CHECK (length(email) BETWEEN 3 AND 320),
    CONSTRAINT ck_app_users_display_name CHECK (display_name IS NULL OR length(display_name) <= 200),
    CONSTRAINT ck_app_users_status CHECK (status IN ('active', 'disabled'))
);

CREATE TABLE vt.vendors (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id      uuid NOT NULL,
    name        text NOT NULL,
    status      text NOT NULL DEFAULT 'active',
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT fk_vendors_org FOREIGN KEY (org_id) REFERENCES vt.organizations (id),
    -- target of the composite foreign key below: a membership cannot name another org's vendor
    CONSTRAINT uq_vendors_org_id_id UNIQUE (org_id, id),
    CONSTRAINT ck_vendors_name CHECK (length(btrim(name)) BETWEEN 1 AND 200),
    CONSTRAINT ck_vendors_status CHECK (status IN ('active', 'archived'))
);
CREATE INDEX ix_vendors_org_id_name ON vt.vendors (org_id, name);

CREATE TABLE vt.memberships (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id      uuid NOT NULL,
    user_id     uuid NOT NULL,
    role        text NOT NULL,
    vendor_id   uuid,
    status      text NOT NULL DEFAULT 'active',
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT fk_memberships_org FOREIGN KEY (org_id) REFERENCES vt.organizations (id),
    CONSTRAINT fk_memberships_user FOREIGN KEY (user_id) REFERENCES vt.app_users (id),
    -- MATCH SIMPLE: not enforced while vendor_id is NULL (internal roles)
    CONSTRAINT fk_memberships_org_vendor FOREIGN KEY (org_id, vendor_id)
        REFERENCES vt.vendors (org_id, id),
    CONSTRAINT ck_memberships_role CHECK (role IN ({ROLES})),
    CONSTRAINT ck_memberships_status CHECK (status IN ('active', 'suspended', 'revoked')),
    CONSTRAINT ck_memberships_vendor_scope CHECK ((role = 'vendor_user') = (vendor_id IS NOT NULL))
);
-- one live (active or suspended) membership per user and organization
CREATE UNIQUE INDEX uq_memberships_live_org_user ON vt.memberships (org_id, user_id)
    WHERE status <> 'revoked';
CREATE INDEX ix_memberships_user_id ON vt.memberships (user_id);
CREATE INDEX ix_memberships_org_id ON vt.memberships (org_id);

CREATE TABLE vt.audit_log (
    id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    org_id         uuid NOT NULL,
    actor_user_id  uuid NOT NULL,
    actor_role     text NOT NULL,
    action         varchar NOT NULL,
    entity_type    varchar NOT NULL,
    entity_id      uuid,
    before_data    jsonb,
    after_data     jsonb,
    created_at     timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT fk_audit_log_org FOREIGN KEY (org_id) REFERENCES vt.organizations (id),
    CONSTRAINT fk_audit_log_actor FOREIGN KEY (actor_user_id) REFERENCES vt.app_users (id),
    CONSTRAINT ck_audit_log_actor_role CHECK (actor_role IN ({ROLES})),
    CONSTRAINT ck_audit_log_action CHECK (length(action) BETWEEN 1 AND 100),
    CONSTRAINT ck_audit_log_entity_type CHECK (length(entity_type) BETWEEN 1 AND 100)
);
CREATE INDEX ix_audit_log_org_id_id ON vt.audit_log (org_id, id);
CREATE INDEX ix_audit_log_entity ON vt.audit_log (org_id, entity_type, entity_id);
"""

DOWNGRADE = """
DROP TABLE vt.audit_log;
DROP TABLE vt.memberships;
DROP TABLE vt.vendors;
DROP TABLE vt.app_users;
DROP TABLE vt.organizations;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
