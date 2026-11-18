"""Shared settings for Dagster: which tenants to process.

Row-level security means there is no query that lists "all tenants' data", by design.
So the orchestrator is told explicitly which tenants it serves (BASELINE_TENANTS).
"""

import os

from dagster import ConfigurableResource


class BaselineResource(ConfigurableResource):
    tenants: str = "demo_owner,demo_gc"

    @property
    def tenant_list(self) -> list[str]:
        return [t.strip() for t in self.tenants.split(",") if t.strip()]


def default_resource() -> BaselineResource:
    return BaselineResource(tenants=os.getenv("BASELINE_TENANTS", "demo_owner,demo_gc"))