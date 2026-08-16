"""Durable shared saved filter-view repository.

The repository stores only small validated JSON documents.  Financial frames
remain in the refresh manager and browser/session selections remain in Dash
components; neither belongs in this filesystem boundary.  Named views form one
catalogue shared by Risk, Stock, and P&L.  The caller's page scope is retained
only on the returned value so each page can validate its own apply request.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import unicodedata
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from threading import Lock, RLock
from time import monotonic, sleep
from typing import Final

if os.name == "nt":
    import msvcrt
else:
    import fcntl


SAVED_VIEW_VERSION: Final = 1
SHARED_SAVED_VIEW_SCOPE: Final = "shared"
_SCOPE_PATTERN: Final = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_IDENTIFIER_PATTERN: Final = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,47})--[0-9a-f]{12}$")
_SLUG_CHARACTERS: Final = re.compile(r"[^a-z0-9]+")
_LOCK_POLL_SECONDS: Final = 0.02
_LOCK_TIMEOUT_SECONDS: Final = 5.0
_MAX_NAME_LENGTH: Final = 80
_MAX_FILTER_VALUES: Final = 1_000
_MAX_FILTER_VALUE_LENGTH: Final = 500


class SavedViewError(ValueError):
    """Base error for invalid or conflicting saved filter views."""


class SavedViewConflictError(SavedViewError):
    """Raised when ``Save New`` receives an existing catalogue name."""


class SavedViewValidationError(SavedViewError):
    """Raised when a saved-view document does not match the contract."""


@dataclass(frozen=True)
class SavedFilterView:
    """One immutable saved selection adapted to a consumer page scope."""

    identifier: str
    scope: str
    name: str
    filters: dict[str, tuple[str, ...]]
    exclude_selected: bool

    def option(self) -> dict[str, str]:
        """Return the compact Dash dropdown representation."""

        return {"label": self.name, "value": self.identifier}


_PATH_LOCKS: dict[Path, RLock] = {}
_PATH_LOCKS_GUARD = Lock()


def normalize_saved_view_name(value: object) -> str:
    """Return one safe display name without turning paths into filenames."""

    if not isinstance(value, str):
        raise SavedViewValidationError("Saved view name must be text")
    name = " ".join(unicodedata.normalize("NFKC", value).split())
    if not name:
        raise SavedViewValidationError("Enter a name before saving the view")
    if len(name) > _MAX_NAME_LENGTH:
        raise SavedViewValidationError(
            f"Saved view name must be at most {_MAX_NAME_LENGTH} characters"
        )
    if name in {".", ".."} or any(character in name for character in ("/", "\\")):
        raise SavedViewValidationError("Saved view name must not contain a path")
    if any(unicodedata.category(character).startswith("C") for character in name):
        raise SavedViewValidationError(
            "Saved view name contains unsupported characters"
        )
    return name


def _saved_view_identifier(name: str) -> str:
    folded = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    slug = _SLUG_CHARACTERS.sub("-", folded.casefold()).strip("-") or "view"
    slug = slug[:48].rstrip("-") or "view"
    digest = hashlib.sha256(name.casefold().encode("utf-8")).hexdigest()[:12]
    return f"{slug}--{digest}"


def _normalize_scope(value: object) -> str:
    scope = str(value).strip().casefold()
    if not _SCOPE_PATTERN.fullmatch(scope):
        raise SavedViewValidationError(
            "Saved view scope must start with a letter and contain only "
            "lowercase letters, numbers, underscores, or hyphens"
        )
    return scope


def _normalize_identifier(value: object) -> str:
    identifier = str(value).strip().casefold()
    if not _IDENTIFIER_PATTERN.fullmatch(identifier):
        raise SavedViewValidationError("Saved view identifier is invalid")
    return identifier


def _path_lock(path: Path) -> RLock:
    with _PATH_LOCKS_GUARD:
        return _PATH_LOCKS.setdefault(path, RLock())


class SavedFilterViewRepository:
    """Read and atomically mutate validated JSON under one governed root.

    A short-lived lock file serializes writers across Plotly/Gunicorn workers,
    while an in-process re-entrant lock avoids competing threads.  Readers see
    either the previous complete JSON file or its complete replacement.
    """

    def __init__(self, root: str | Path, filter_keys: Sequence[str]) -> None:
        self._root = Path(root).resolve()
        keys = tuple(str(key).strip() for key in filter_keys)
        if not keys or any(not key for key in keys) or len(keys) != len(set(keys)):
            raise SavedViewValidationError(
                "Saved view filter keys must be a non-empty unique sequence"
            )
        self._filter_keys = keys

    @property
    def root(self) -> Path:
        return self._root

    @property
    def filter_keys(self) -> tuple[str, ...]:
        return self._filter_keys

    def _scope_directory(self, scope: object) -> tuple[str, Path]:
        """Validate a consumer scope and return the one shared catalogue."""

        normalized = _normalize_scope(scope)
        directory = (self._root / SHARED_SAVED_VIEW_SCOPE).resolve()
        if directory.parent != self._root:
            raise SavedViewValidationError(
                "Saved view catalogue escapes its repository"
            )
        return normalized, directory

    @staticmethod
    def _for_scope(view: SavedFilterView, scope: str) -> SavedFilterView:
        """Adapt stored shared metadata to one page-local request authority."""

        return SavedFilterView(
            identifier=view.identifier,
            scope=scope,
            name=view.name,
            filters=view.filters,
            exclude_selected=view.exclude_selected,
        )

    @contextmanager
    def _writer_lock(self, directory: Path) -> Iterator[None]:
        directory.mkdir(parents=True, exist_ok=True)
        local_lock = _path_lock(directory)
        with local_lock:
            lock_path = directory / ".write.lock"
            deadline = monotonic() + _LOCK_TIMEOUT_SECONDS
            handle = lock_path.open("a+b")
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()

            def try_lock() -> None:
                handle.seek(0)
                if os.name == "nt":
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

            def unlock() -> None:
                handle.seek(0)
                if os.name == "nt":
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

            acquired = False
            while not acquired:
                try:
                    try_lock()
                    acquired = True
                except OSError:
                    if monotonic() >= deadline:
                        handle.close()
                        raise TimeoutError(
                            f"Timed out waiting to save a view in {directory.name!r}"
                        )
                    sleep(_LOCK_POLL_SECONDS)
            try:
                yield
            finally:
                unlock()
                handle.close()

    def _normalize_filters(
        self,
        value: Mapping[str, Sequence[str] | None],
    ) -> dict[str, tuple[str, ...]]:
        if not isinstance(value, Mapping):
            raise SavedViewValidationError("Saved view filters must be a mapping")
        actual_keys = set(value)
        expected_keys = set(self._filter_keys)
        if actual_keys != expected_keys:
            missing = sorted(expected_keys - actual_keys)
            unknown = sorted(actual_keys - expected_keys)
            raise SavedViewValidationError(
                f"Saved view filters must use the configured keys; "
                f"missing={missing}, unknown={unknown}"
            )

        result: dict[str, tuple[str, ...]] = {}
        for key in self._filter_keys:
            raw_values = value[key]
            if raw_values is None:
                values: Sequence[str] = ()
            elif isinstance(raw_values, (str, bytes)) or not isinstance(
                raw_values, Sequence
            ):
                raise SavedViewValidationError(
                    f"Saved view filter {key!r} must be a sequence of text values"
                )
            else:
                values = raw_values
            if len(values) > _MAX_FILTER_VALUES:
                raise SavedViewValidationError(
                    f"Saved view filter {key!r} contains too many values"
                )
            normalized_values: list[str] = []
            seen: set[str] = set()
            for raw_value in values:
                if not isinstance(raw_value, str):
                    raise SavedViewValidationError(
                        f"Saved view filter {key!r} values must be text"
                    )
                selected = unicodedata.normalize("NFKC", raw_value).strip()
                if not selected or len(selected) > _MAX_FILTER_VALUE_LENGTH:
                    raise SavedViewValidationError(
                        f"Saved view filter {key!r} contains an invalid value"
                    )
                if any(
                    unicodedata.category(character).startswith("C")
                    for character in selected
                ):
                    raise SavedViewValidationError(
                        f"Saved view filter {key!r} contains unsupported characters"
                    )
                if selected not in seen:
                    seen.add(selected)
                    normalized_values.append(selected)
            result[key] = tuple(
                sorted(normalized_values, key=lambda item: (item.casefold(), item))
            )
        return result

    def _decode(self, path: Path, expected_scope: str) -> SavedFilterView:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise SavedViewValidationError(
                f"Could not read saved view {path.name!r}: {exc}"
            ) from exc
        if not isinstance(payload, dict):
            raise SavedViewValidationError(
                f"Saved view {path.name!r} must contain one JSON object"
            )
        expected_document_keys = {
            "version",
            "id",
            "scope",
            "name",
            "filters",
            "exclude_selected",
        }
        if set(payload) != expected_document_keys:
            raise SavedViewValidationError(
                f"Saved view {path.name!r} has unexpected document fields"
            )
        if payload["version"] != SAVED_VIEW_VERSION:
            raise SavedViewValidationError(
                f"Saved view {path.name!r} uses an unsupported version"
            )
        scope = _normalize_scope(payload["scope"])
        if scope != expected_scope:
            raise SavedViewValidationError(
                f"Saved view {path.name!r} belongs to another catalogue"
            )
        name = normalize_saved_view_name(payload["name"])
        identifier = _normalize_identifier(payload["id"])
        if path.stem != identifier or identifier != _saved_view_identifier(name):
            raise SavedViewValidationError(
                f"Saved view {path.name!r} has inconsistent identity metadata"
            )
        exclude_selected = payload["exclude_selected"]
        if not isinstance(exclude_selected, bool):
            raise SavedViewValidationError(
                f"Saved view {path.name!r} has an invalid include/exclude mode"
            )
        filters = self._normalize_filters(payload["filters"])
        return SavedFilterView(
            identifier=identifier,
            scope=scope,
            name=name,
            filters=filters,
            exclude_selected=exclude_selected,
        )

    def _stored_views(self, directory: Path) -> tuple[SavedFilterView, ...]:
        """Read and validate the shared catalogue without adapting page scope."""

        if not directory.exists():
            return ()
        if not directory.is_dir():
            raise SavedViewValidationError(
                "Saved view shared catalogue is not a directory"
            )
        views = [
            self._decode(path, SHARED_SAVED_VIEW_SCOPE)
            for path in sorted(directory.glob("*.json"), key=lambda item: item.name)
        ]
        duplicate_names = len({view.name.casefold() for view in views}) != len(views)
        if duplicate_names:
            raise SavedViewValidationError(
                "Saved view shared catalogue contains duplicate names"
            )
        return tuple(
            sorted(
                views,
                key=lambda view: (view.name.casefold(), view.name, view.identifier),
            )
        )

    def _write_atomic(self, directory: Path, view: SavedFilterView) -> None:
        """Replace one complete JSON document without exposing partial bytes."""

        document = {
            "version": SAVED_VIEW_VERSION,
            "id": view.identifier,
            "scope": SHARED_SAVED_VIEW_SCOPE,
            "name": view.name,
            "filters": {key: list(view.filters[key]) for key in self._filter_keys},
            "exclude_selected": view.exclude_selected,
        }
        descriptor, temporary_name = tempfile.mkstemp(
            dir=directory,
            prefix=f".{view.identifier}.",
            suffix=".tmp",
            text=True,
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(
                    document,
                    handle,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, directory / f"{view.identifier}.json")
        finally:
            if temporary.exists():
                temporary.unlink()

    def list(self, scope: object) -> tuple[SavedFilterView, ...]:
        """Return the shared catalogue adapted to one consumer page scope."""

        normalized_scope, directory = self._scope_directory(scope)
        return tuple(
            self._for_scope(view, normalized_scope)
            for view in self._stored_views(directory)
        )

    def get(self, scope: object, identifier: object) -> SavedFilterView:
        """Load one validated view without trusting a browser-supplied path."""

        normalized_scope, directory = self._scope_directory(scope)
        normalized_identifier = _normalize_identifier(identifier)
        path = directory / f"{normalized_identifier}.json"
        if not path.is_file():
            raise FileNotFoundError(
                f"Saved view {normalized_identifier!r} does not exist"
            )
        return self._for_scope(
            self._decode(path, SHARED_SAVED_VIEW_SCOPE),
            normalized_scope,
        )

    def save_new(
        self,
        scope: object,
        name: object,
        filters: Mapping[str, Sequence[str] | None],
        *,
        exclude_selected: bool,
    ) -> SavedFilterView:
        """Atomically create one new view; existing names are never overwritten."""

        normalized_scope, directory = self._scope_directory(scope)
        normalized_name = normalize_saved_view_name(name)
        if not isinstance(exclude_selected, bool):
            raise SavedViewValidationError("Saved view include/exclude mode is invalid")
        normalized_filters = self._normalize_filters(filters)
        identifier = _saved_view_identifier(normalized_name)
        stored_view = SavedFilterView(
            identifier=identifier,
            scope=SHARED_SAVED_VIEW_SCOPE,
            name=normalized_name,
            filters=normalized_filters,
            exclude_selected=exclude_selected,
        )
        with self._writer_lock(directory):
            existing = self._stored_views(directory)
            if any(
                item.name.casefold() == normalized_name.casefold() for item in existing
            ):
                raise SavedViewConflictError(
                    f"A saved view named {normalized_name!r} already exists"
                )
            self._write_atomic(directory, stored_view)
        return self._for_scope(stored_view, normalized_scope)

    def update(
        self,
        scope: object,
        identifier: object,
        filters: Mapping[str, Sequence[str] | None],
        *,
        exclude_selected: bool,
    ) -> SavedFilterView:
        """Atomically overwrite one named view's filters without renaming it."""

        normalized_scope, directory = self._scope_directory(scope)
        normalized_identifier = _normalize_identifier(identifier)
        if not isinstance(exclude_selected, bool):
            raise SavedViewValidationError("Saved view include/exclude mode is invalid")
        normalized_filters = self._normalize_filters(filters)
        with self._writer_lock(directory):
            path = directory / f"{normalized_identifier}.json"
            if not path.is_file():
                raise FileNotFoundError(
                    f"Saved view {normalized_identifier!r} does not exist"
                )
            current = self._decode(path, SHARED_SAVED_VIEW_SCOPE)
            stored_view = SavedFilterView(
                identifier=current.identifier,
                scope=SHARED_SAVED_VIEW_SCOPE,
                name=current.name,
                filters=normalized_filters,
                exclude_selected=exclude_selected,
            )
            self._write_atomic(directory, stored_view)
        return self._for_scope(stored_view, normalized_scope)

    def delete(self, scope: object, identifier: object) -> SavedFilterView:
        """Delete one exact shared view while holding the writer guard."""

        normalized_scope, directory = self._scope_directory(scope)
        normalized_identifier = _normalize_identifier(identifier)
        with self._writer_lock(directory):
            path = directory / f"{normalized_identifier}.json"
            if not path.is_file():
                raise FileNotFoundError(
                    f"Saved view {normalized_identifier!r} does not exist"
                )
            view = self._decode(path, SHARED_SAVED_VIEW_SCOPE)
            path.unlink()
        return self._for_scope(view, normalized_scope)


__all__ = [
    "SAVED_VIEW_VERSION",
    "SHARED_SAVED_VIEW_SCOPE",
    "SavedFilterView",
    "SavedFilterViewRepository",
    "SavedViewConflictError",
    "SavedViewError",
    "SavedViewValidationError",
    "normalize_saved_view_name",
]
