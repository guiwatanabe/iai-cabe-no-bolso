"""BigQuery access: parameterised queries only, capped by bytes billed and rows."""

import os
from datetime import date
from functools import cache

from google.cloud import bigquery

MAX_BYTES_BILLED = int(os.getenv("BQ_MAX_BYTES_BILLED", 1_000_000_000))
MAX_ROWS = int(os.getenv("BQ_MAX_ROWS", 200))

_BQ_TYPES = {bool: "BOOL", int: "INT64", float: "FLOAT64", str: "STRING", date: "DATE"}


@cache
def client() -> bigquery.Client:
    return bigquery.Client(project=os.getenv("GOOGLE_CLOUD_PROJECT"))


def run(sql: str, **params) -> list[dict]:
    """Run `sql` with `@name` params bound from kwargs. Never format values into `sql`."""
    config = bigquery.QueryJobConfig(
        maximum_bytes_billed=MAX_BYTES_BILLED,
        query_parameters=[bigquery.ScalarQueryParameter(k, _BQ_TYPES[type(v)], v) for k, v in params.items()],
    )
    rows = client().query(sql, job_config=config).result(max_results=MAX_ROWS)
    return [dict(r) for r in rows]
