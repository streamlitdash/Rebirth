"""Runtime configuration for local, JupyterHub, and WSGI launches."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


TRUTHY = {"1", "true", "yes", "on"}


def env_flag(
    name: str,
    default: bool = False,
    *,
    environ: Mapping[str, str] | None = None,
) -> bool:
    """Read a boolean environment flag with predictable accepted values."""
    values = os.environ if environ is None else environ
    raw = values.get(name)
    return default if raw is None else raw.strip().casefold() in TRUTHY


def normalize_path_prefix(value: str | None, *, name: str) -> str:
    """Return a Dash pathname prefix with one leading and trailing slash."""
    prefix = (value or "/").strip() or "/"
    if "://" in prefix or "?" in prefix or "#" in prefix:
        raise ValueError(f"{name} must be a URL path, not a complete URL")
    return f"/{prefix.strip('/')}/" if prefix.strip("/") else "/"


def resolve_data_path(value: str | None, default: Path, *, root: Path) -> Path:
    """Resolve configured relative paths against the app rather than the shell."""
    candidate = (
        default if value is None or not value.strip() else Path(value).expanduser()
    )
    return (root / candidate if not candidate.is_absolute() else candidate).resolve()


@dataclass(frozen=True)
class RuntimeSettings:
    """Validated settings shared by development and WSGI launches."""

    host: str
    port: int
    debug: bool
    requests_pathname_prefix: str
    routes_pathname_prefix: str

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "RuntimeSettings":
        values = dict(os.environ if environ is None else environ)
        try:
            port = int(values.get("PORT", "8050"))
        except ValueError as exc:
            raise ValueError("PORT must be an integer between 1 and 65535") from exc
        if not 1 <= port <= 65535:
            raise ValueError("PORT must be an integer between 1 and 65535")

        request_override = values.get("DASH_REQUESTS_PATHNAME_PREFIX")
        route_override = values.get("DASH_ROUTES_PATHNAME_PREFIX")
        hub_prefix = values.get("JUPYTERHUB_SERVICE_PREFIX")
        hub_mode = values.get("DASH_JUPYTERHUB_MODE", "proxy").strip().casefold()
        if hub_mode not in {"proxy", "service"}:
            raise ValueError("DASH_JUPYTERHUB_MODE must be either 'proxy' or 'service'")

        if request_override:
            requests_prefix = normalize_path_prefix(
                request_override,
                name="DASH_REQUESTS_PATHNAME_PREFIX",
            )
        elif hub_prefix and hub_mode == "service":
            requests_prefix = normalize_path_prefix(
                hub_prefix,
                name="JUPYTERHUB_SERVICE_PREFIX",
            )
        elif hub_prefix:
            hub_base = normalize_path_prefix(
                hub_prefix,
                name="JUPYTERHUB_SERVICE_PREFIX",
            ).rstrip("/")
            requests_prefix = normalize_path_prefix(
                f"{hub_base}/proxy/{port}/",
                name="derived JupyterHub proxy prefix",
            )
        else:
            requests_prefix = "/"

        if route_override:
            routes_prefix = normalize_path_prefix(
                route_override,
                name="DASH_ROUTES_PATHNAME_PREFIX",
            )
        elif hub_prefix and hub_mode == "service":
            routes_prefix = normalize_path_prefix(
                hub_prefix,
                name="JUPYTERHUB_SERVICE_PREFIX",
            )
        else:
            # jupyter-server-proxy strips its public prefix before forwarding.
            routes_prefix = "/"

        default_host = "127.0.0.1"
        return cls(
            host=values.get("HOST", default_host).strip() or default_host,
            port=port,
            debug=env_flag("DASH_DEBUG", False, environ=values),
            requests_pathname_prefix=requests_prefix,
            routes_pathname_prefix=routes_prefix,
        )

    @property
    def dash_kwargs(self) -> dict[str, str]:
        """Build the Dash constructor pathname arguments."""
        return {
            "requests_pathname_prefix": self.requests_pathname_prefix,
            "routes_pathname_prefix": self.routes_pathname_prefix,
        }
