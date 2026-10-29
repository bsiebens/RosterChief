"""Write the API's OpenAPI document (public and protected routes) to ``docs/app-api/openapi-v1.json``.

The committed snapshot is what app developers generate their Swift/Kotlin client from, and
``api.protected.tests`` fails when it no longer matches the code -- so an API change and its
snapshot land in the same commit, and the file's history doubles as the API changelog.
"""

import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from api.v1 import api

DEFAULT_OUTPUT = Path(settings.BASE_DIR) / "docs" / "app-api" / "openapi-v1.json"


def render_schema() -> str:
    return json.dumps(api.get_openapi_schema(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


class Command(BaseCommand):
    help = "Write the API's OpenAPI document (the snapshot clients are generated from)."

    def add_arguments(self, parser):
        parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Where to write it (default: %(default)s).")

    def handle(self, *args, output, **options):
        Path(output).write_text(render_schema(), encoding="utf-8")
        self.stdout.write(self.style.SUCCESS(f"Wrote {output}"))
