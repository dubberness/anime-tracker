"""Single source of truth for the application version."""

import os

VERSION = "4.0.0"

# Injected at image build time by the Docker build args; falls back to "dev"
# when running straight from a checkout.
BUILD_SHA = os.environ.get("BUILD_SHA", "dev")
BUILD_DATE = os.environ.get("BUILD_DATE", "")


def version_string():
    if BUILD_SHA and BUILD_SHA != "dev":
        return f"{VERSION} ({BUILD_SHA})"
    return f"{VERSION}-dev"
