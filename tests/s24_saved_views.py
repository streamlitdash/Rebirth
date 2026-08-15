"""Page-local saved filter-view storage and component contracts."""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from threading import Thread

import pytest
from dash import Dash, dcc, html

from core.s09_saved_views import (
    SavedFilterViewRepository,
    SavedViewConflictError,
    SavedViewValidationError,
)
from ui.s02_constants import FILTER_DIMENSION_FIELDS
from ui.s04_components import RISK_SAVED_VIEW_CONTROLS
from ui.s10_stock import STOCK_SAVED_VIEW_CONTROLS
from ui.s11_saved_views import (
    build_saved_filter_view_bar,
    register_saved_filter_view_callbacks,
    saved_view_apply_request,
    saved_view_request_matches_base,
    saved_view_request_values,
)


FILTER_KEYS = tuple(field.key for field in FILTER_DIMENSION_FIELDS)


def _filters(activity: str = "Macro") -> dict[str, list[str]]:
    return {
        "activity": [activity],
        "signoffgroup": ["SOG-A"],
        "portfolio": ["BOOK-A"],
        "category": ["Core"],
        "subcategory": ["Rates"],
    }


def _walk(component: object) -> Iterable[object]:
    yield component
    children = getattr(component, "children", None)
    if isinstance(children, (list, tuple)):
        for child in children:
            yield from _walk(child)
    elif children is not None:
        yield from _walk(children)


def _outputs(metadata: dict) -> list[object]:
    output = metadata["output"]
    return list(output) if isinstance(output, (list, tuple)) else [output]


def test_filter_order_is_the_same_explicit_five_column_contract() -> None:
    assert FILTER_KEYS == (
        "activity",
        "signoffgroup",
        "portfolio",
        "category",
        "subcategory",
    )
    assert tuple(field.label for field in FILTER_DIMENSION_FIELDS) == (
        "Activity",
        "Signoff Group",
        "Portfolio",
        "Category",
        "Sub Category",
    )
    assert tuple(field.key for field in STOCK_SAVED_VIEW_CONTROLS.fields) == FILTER_KEYS
    assert tuple(field.key for field in RISK_SAVED_VIEW_CONTROLS.fields) == FILTER_KEYS
    css = (Path(__file__).parents[1] / "assets" / "s01_style.css").read_text()
    assert ".controls.filter-controls" in css
    assert "grid-template-columns: repeat(5, minmax(120px, 1fr));" in css


def test_repository_is_page_scoped_deterministic_and_atomic(tmp_path: Path) -> None:
    root = tmp_path / "saved_views"
    repository = SavedFilterViewRepository(root, FILTER_KEYS)

    later = repository.save_new(
        "risk",
        "Zulu view",
        _filters(),
        exclude_selected=False,
    )
    first = repository.save_new(
        "risk",
        " alpha   view ",
        {**_filters(), "portfolio": ["BOOK-B", "BOOK-A", "BOOK-A"]},
        exclude_selected=True,
    )
    stock = repository.save_new(
        "stock",
        "Alpha view",
        _filters("Hedge"),
        exclude_selected=False,
    )

    assert [view.name for view in repository.list("risk")] == [
        "alpha view",
        "Zulu view",
    ]
    assert repository.get("risk", first.identifier).filters["portfolio"] == (
        "BOOK-A",
        "BOOK-B",
    )
    assert repository.get("stock", stock.identifier).filters["activity"] == ("Hedge",)
    assert later.identifier != first.identifier
    assert {path.parent.name for path in root.rglob("*.json")} == {"risk", "stock"}
    assert not list(root.rglob("*.tmp"))
    assert {path.parent.name for path in root.rglob(".write.lock")} == {
        "risk",
        "stock",
    }

    document = json.loads((root / "risk" / f"{first.identifier}.json").read_text())
    assert document == {
        "version": 1,
        "id": first.identifier,
        "scope": "risk",
        "name": "alpha view",
        "filters": {
            "activity": ["Macro"],
            "signoffgroup": ["SOG-A"],
            "portfolio": ["BOOK-A", "BOOK-B"],
            "category": ["Core"],
            "subcategory": ["Rates"],
        },
        "exclude_selected": True,
    }


def test_repository_rejects_duplicates_paths_and_invalid_documents(
    tmp_path: Path,
) -> None:
    repository = SavedFilterViewRepository(tmp_path, FILTER_KEYS)
    view = repository.save_new(
        "risk",
        "Morning",
        _filters(),
        exclude_selected=False,
    )

    with pytest.raises(SavedViewConflictError, match="already exists"):
        repository.save_new(
            "risk",
            " morning ",
            _filters(),
            exclude_selected=False,
        )
    with pytest.raises(SavedViewValidationError, match="path"):
        repository.save_new(
            "risk",
            "../escape",
            _filters(),
            exclude_selected=False,
        )
    with pytest.raises(SavedViewValidationError, match="scope"):
        repository.list("../risk")
    with pytest.raises(SavedViewValidationError, match="identifier"):
        repository.get("risk", "../escape")

    path = tmp_path / "risk" / f"{view.identifier}.json"
    payload = json.loads(path.read_text())
    payload["filters"]["unknown"] = []
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(SavedViewValidationError, match="configured keys"):
        repository.list("risk")


def test_repository_serializes_concurrent_writers(tmp_path: Path) -> None:
    repository = SavedFilterViewRepository(tmp_path, FILTER_KEYS)
    errors: list[BaseException] = []

    def save(index: int) -> None:
        try:
            repository.save_new(
                "risk",
                f"View {index:02d}",
                _filters(str(index)),
                exclude_selected=bool(index % 2),
            )
        except BaseException as error:  # pragma: no cover - thread handoff
            errors.append(error)

    threads = [Thread(target=save, args=(index,)) for index in range(12)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert not any(thread.is_alive() for thread in threads)
    assert errors == []
    assert [view.name for view in repository.list("risk")] == [
        f"View {index:02d}" for index in range(12)
    ]


def test_saved_view_bar_supports_immediate_save_and_ephemeral_disclosure() -> None:
    bar = build_saved_filter_view_bar(RISK_SAVED_VIEW_CONTROLS)
    components = list(_walk(bar))
    name = next(
        item
        for item in components
        if isinstance(item, dcc.Input) and item.id == RISK_SAVED_VIEW_CONTROLS.name_id
    )
    copy = " ".join(str(getattr(item, "children", "")) for item in components)

    assert name.debounce is False
    assert RISK_SAVED_VIEW_CONTROLS.apply_request_id in {
        getattr(item, "id", None) for item in components
    }
    assert RISK_SAVED_VIEW_CONTROLS.applied_request_id in {
        getattr(item, "id", None) for item in components
    }
    assert "restart or redeploy" in copy


def test_request_store_is_validated_and_detects_later_manual_edits(
    tmp_path: Path,
) -> None:
    repository = SavedFilterViewRepository(tmp_path, FILTER_KEYS)
    view = repository.save_new(
        "stock",
        "Macro",
        _filters(),
        exclude_selected=True,
    )
    base = {key: [] for key in FILTER_KEYS}
    request = saved_view_apply_request(
        view,
        base_filters=base,
        base_exclude_selected=False,
    )

    values, exclude = saved_view_request_values(request, STOCK_SAVED_VIEW_CONTROLS)
    assert values[0] == ["Macro"]
    assert exclude == ["exclude"]
    assert saved_view_request_matches_base(
        request,
        STOCK_SAVED_VIEW_CONTROLS,
        tuple([] for _key in FILTER_KEYS),
        [],
    )
    manually_edited = [[], [], ["BOOK-B"], [], []]
    assert not saved_view_request_matches_base(
        request,
        STOCK_SAVED_VIEW_CONTROLS,
        manually_edited,
        [],
    )

    request["scope"] = "risk"
    with pytest.raises(ValueError, match="another page"):
        saved_view_request_values(request, STOCK_SAVED_VIEW_CONTROLS)


def test_generic_callbacks_never_own_filter_dropdown_values(tmp_path: Path) -> None:
    repository = SavedFilterViewRepository(tmp_path, FILTER_KEYS)
    app = Dash(__name__, suppress_callback_exceptions=True)
    app.layout = html.Div(
        [
            build_saved_filter_view_bar(RISK_SAVED_VIEW_CONTROLS),
            *[
                dcc.Dropdown(id=RISK_SAVED_VIEW_CONTROLS.filter_ids[field.key])
                for field in FILTER_DIMENSION_FIELDS
            ],
            dcc.Checklist(id=RISK_SAVED_VIEW_CONTROLS.exclude_id),
        ]
    )
    register_saved_filter_view_callbacks(
        app,
        repository,
        RISK_SAVED_VIEW_CONTROLS,
    )

    outputs = [
        (str(output.component_id), output.component_property)
        for metadata in app.callback_map.values()
        for output in _outputs(metadata)
    ]
    assert (RISK_SAVED_VIEW_CONTROLS.apply_request_id, "data") in outputs
    assert (RISK_SAVED_VIEW_CONTROLS.applied_request_id, "data") in outputs
    assert not any(
        (component_id, "value") in outputs
        for component_id in RISK_SAVED_VIEW_CONTROLS.filter_ids.values()
    )
    assert (RISK_SAVED_VIEW_CONTROLS.exclude_id, "value") not in outputs


def test_repository_delete_is_exact_and_page_local(tmp_path: Path) -> None:
    repository = SavedFilterViewRepository(tmp_path, FILTER_KEYS)
    risk = repository.save_new(
        "risk",
        "Shared label",
        _filters(),
        exclude_selected=False,
    )
    stock = repository.save_new(
        "stock",
        "Shared label",
        _filters(),
        exclude_selected=False,
    )

    deleted = repository.delete("risk", risk.identifier)

    assert deleted == risk
    assert repository.list("risk") == ()
    assert repository.get("stock", stock.identifier) == stock
