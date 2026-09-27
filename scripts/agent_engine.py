"""Print the Agent Engine resource name for the `cabe` agent, creating an empty instance on first run.

Usage: python scripts/agent_engine.py PROJECT REGION
The runtime service account is set here, once; `adk deploy agent_engine` updates keep it.
"""

import sys

import vertexai

NAME = "cabe"

project, region = sys.argv[1:3]
client = vertexai.Client(project=project, location=region)
found = list(client.agent_engines.list(config={"filter": f'display_name="{NAME}"'}))
engine = found[0] if found else client.agent_engines.create(
    config={"display_name": NAME, "service_account": f"cabe-agent@{project}.iam.gserviceaccount.com"}
)
print(engine.api_resource.name)
