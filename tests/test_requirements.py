import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_agent_requirements_are_pinned_to_the_lock():
    locked = {p["name"]: p["version"] for p in tomllib.loads((ROOT / "uv.lock").read_text())["package"]}
    for line in (ROOT / "agents/cabe/requirements.txt").read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        pin = re.fullmatch(r"([\w.-]+)(?:\[[^]]*\])?==(\S+)", line)
        assert pin, f"pin with ==: {line}"
        name, version = pin.groups()
        assert locked[name.lower().replace("_", "-")] == version, line


def test_analytics_plugin_imports():
    # .agent_engine_config.json turns on BQ_ANALYTICS_DATASET; the plugin's deps come from the adk extra.
    import google.adk.plugins.bigquery_agent_analytics_plugin  # noqa: F401
