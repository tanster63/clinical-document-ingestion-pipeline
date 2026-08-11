# Relative, not `from agent.agent import ...`: `adk deploy cloud_run` copies
# ./agent to /app/agents/<app_name>/, so the package is named clinical_query_agent
# there and an absolute self-import cannot resolve.
from .agent import root_agent  # noqa: F401  — ADK discovers the agent through this import
