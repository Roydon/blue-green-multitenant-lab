-- Layer 0: the application role. Deliberately NOT a superuser and without
-- BYPASSRLS, because a superuser silently ignores row level security and the
-- isolation proof in tests/test_isolation.py would be worthless.
CREATE ROLE app_user LOGIN PASSWORD 'app_pw' NOSUPERUSER NOBYPASSRLS;

-- Tenant registry lives in public; it is the only cross-tenant table.
CREATE TABLE public.tenants (
    id          uuid PRIMARY KEY,
    slug        text UNIQUE NOT NULL,
    name        text NOT NULL,
    schema_name text UNIQUE NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);
GRANT USAGE ON SCHEMA public TO app_user;
GRANT SELECT ON public.tenants TO app_user;

-- Creates one tenant's schema (isolation layer 1) with tables that carry a
-- tenant_id and a FORCED row level security policy (isolation layer 2), but
-- does NOT touch public.tenants. Used to provision structure on db-green:
-- its tenants row and all row data arrive one-way via logical replication
-- from db-blue (db/replication/setup.sh) -- if this function inserted into
-- public.tenants on green too, the replication's initial COPY would hit a
-- primary-key conflict against that independently-inserted row and the
-- subscription would hang (this happened; see git history).
CREATE OR REPLACE FUNCTION public.provision_tenant_schema(p_slug text)
RETURNS void LANGUAGE plpgsql AS $fn$
DECLARE
    s text := 'tenant_' || p_slug;
BEGIN
    EXECUTE format('CREATE SCHEMA IF NOT EXISTS %I', s);
    EXECUTE format('GRANT USAGE ON SCHEMA %I TO app_user', s);

    EXECUTE format($ddl$
        CREATE TABLE IF NOT EXISTS %I.documents (
            id         uuid PRIMARY KEY,
            tenant_id  uuid NOT NULL,
            title      text NOT NULL,
            body       text NOT NULL DEFAULT '',
            created_at timestamptz NOT NULL DEFAULT now()
        )$ddl$, s);

    EXECUTE format($ddl$
        CREATE TABLE IF NOT EXISTS %I.jobs (
            id           uuid PRIMARY KEY,
            tenant_id    uuid NOT NULL,
            prompt       text NOT NULL,
            status       text NOT NULL DEFAULT 'queued',
            result       text,
            created_at   timestamptz NOT NULL DEFAULT now(),
            completed_at timestamptz
        )$ddl$, s);

    -- FORCE matters: without it the table owner bypasses the policy.
    FOREACH s IN ARRAY ARRAY['tenant_' || p_slug] LOOP
        EXECUTE format('ALTER TABLE %I.documents ENABLE ROW LEVEL SECURITY', s);
        EXECUTE format('ALTER TABLE %I.documents FORCE ROW LEVEL SECURITY', s);
        EXECUTE format('ALTER TABLE %I.jobs ENABLE ROW LEVEL SECURITY', s);
        EXECUTE format('ALTER TABLE %I.jobs FORCE ROW LEVEL SECURITY', s);

        EXECUTE format($pol$
            CREATE POLICY tenant_isolation ON %I.documents
            USING (tenant_id::text = current_setting('app.tenant_id', true))
            WITH CHECK (tenant_id::text = current_setting('app.tenant_id', true))
        $pol$, s);
        EXECUTE format($pol$
            CREATE POLICY tenant_isolation ON %I.jobs
            USING (tenant_id::text = current_setting('app.tenant_id', true))
            WITH CHECK (tenant_id::text = current_setting('app.tenant_id', true))
        $pol$, s);

        EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA %I TO app_user', s);
    END LOOP;
END;
$fn$;

-- Full provisioning for the live (blue) side: structure plus the registry
-- row. Only ever run against db-blue; db-green gets its registry row by
-- replication, never by calling this function directly.
CREATE OR REPLACE FUNCTION public.provision_tenant(p_id uuid, p_slug text, p_name text)
RETURNS void LANGUAGE plpgsql AS $fn$
BEGIN
    PERFORM public.provision_tenant_schema(p_slug);
    INSERT INTO public.tenants (id, slug, name, schema_name)
    VALUES (p_id, p_slug, p_name, 'tenant_' || p_slug)
    ON CONFLICT (slug) DO NOTHING;
END;
$fn$;
