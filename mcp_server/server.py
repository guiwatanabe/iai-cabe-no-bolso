"""MCP server: curated, read-only tools over BigQuery. No LLM logic lives here.

Every tool returns a `Result` envelope. BigQuery computes every number the answer
needs and exposes it as a `Fact`; the agent never does arithmetic.
Argument constraints (Field) are validated here, before any query runs.
"""

from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from mcp_server import bq

mcp = MCPServer("cabe-no-bolso", instructions="Read-only analytics tools backed by BigQuery.")
READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=False)


class Fact(BaseModel):
    label: str = Field(description="What the value means, e.g. 'total gasto em ago/2026'")
    value: int | float | str
    unit: Literal["BRL", "pct"] | None = Field(None, description="pct = percentage points (12.3 means 12.3%)")


class Result(BaseModel):
    status: Literal["ok", "empty"]
    facts: dict[str, Fact] = {}
    rows: list[dict[str, Any]] = []
    meta: dict[str, Any] = {}


# PLACEHOLDER on a public dataset to prove the wiring end to end. Replace with domain tools.
@mcp.tool(annotations=READ_ONLY)
def top_names(
    state: Annotated[str, Field(pattern="^[A-Z]{2}$", description="US state code, e.g. CA")],
    year: Annotated[int, Field(ge=1910, le=2025)],
    limit: Annotated[int, Field(ge=1, le=50)] = 10,
) -> Result:
    """Most common baby names registered in a US state in a given year."""
    source = "bigquery-public-data.usa_names.usa_1910_current"
    rows = bq.run(
        f"""
        SELECT name, SUM(number) AS total
        FROM `{source}`
        WHERE state = @state AND year = @year
        GROUP BY name ORDER BY total DESC LIMIT @limit
        """,
        state=state,
        year=year,
        limit=limit,
    )
    meta = {"source": source, "row_count": len(rows)}
    if not rows:
        return Result(status="empty", meta=meta)
    top = rows[0]
    return Result(
        status="ok",
        facts={
            "top_name": Fact(label=f"nome mais registrado em {state} em {year}", value=top["name"]),
            "top_total": Fact(label=f"registros de {top['name']} em {state} em {year}", value=top["total"]),
        },
        rows=rows,
        meta=meta,
    )


if __name__ == "__main__":
    mcp.run()
