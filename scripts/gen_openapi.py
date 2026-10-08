"""Write the backend OpenAPI schema to frontend/openapi.json (the TS client is generated from it)."""

import json
from pathlib import Path

from schemashift.api.main import create_app
from schemashift.api.settings import Settings

target = Path(__file__).resolve().parents[1] / "frontend" / "openapi.json"
spec = create_app(Settings(app_database_url="sqlite://")).openapi()
target.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8", newline="\n")
print(f"wrote {target}")
