"""Create the demo customers. Safe to run any number of times."""

from baseline.db.session import tenant_connection
from baseline.db.writer import ensure_tenant

DEMO_TENANTS = [
    # tenant_id,     name,                       region (where their data must live)
    ("demo_owner", "Demo Data Center Owner", "us-east-1"),
    ("demo_gc", "Demo General Contractor", "eu-west-1"),
]


def main() -> None:
    for tenant_id, name, region in DEMO_TENANTS:
        # RLS: each tenant row can only be written while acting as that tenant.
        with tenant_connection(tenant_id) as conn:
            ensure_tenant(conn, tenant_id, name, region)
        print(f"tenant ready: {tenant_id} ({region})")


if __name__ == "__main__":
    main()