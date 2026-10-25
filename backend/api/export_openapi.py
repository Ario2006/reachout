"""Write the OpenAPI schema to a file:  python -m backend.api.export_openapi <out.json>"""
from __future__ import annotations

import json
import sys

from backend.api.main import create_app


def main() -> None:
    out = sys.argv[1] if len(sys.argv) > 1 else "openapi.json"
    schema = create_app(token="x" * 32, engine=object()).openapi()
    with open(out, "w") as f:
        json.dump(schema, f, indent=2, sort_keys=True)
        f.write("\n")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
