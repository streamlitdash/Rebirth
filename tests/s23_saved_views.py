"""Page-local saved filter-view storage and component contracts."""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from threading import Thread
from types import SimpleNamespace

import pandas as pd
import pytest
from dash import Dash, dcc, html, no_update

from core.s08_saved_views import (
    SHARED_SAVED_VIEW_SCOPE,
    SavedFilterViewRepository,
    SavedViewConflictError,
    SavedViewValidationError,
)
from feeds.s01_sources import build_production_refresh_manager
from ui import s11_saved_views as saved_views_module
from ui import s09_factory as factory_module
from ui.s02_constants import FILTER_DIMENSION_FIELDS
from ui.s04_components import RISK_SAVED_VIEW_CONTROLS
from ui.s06_plview import PL_SAVED_VIEW_CONTROLS
from ui.s10_stock import STOCK_SAVED_VIEW_CONTROLS
from ui.s11_saved_views import (
    BASE_SAVED_VIEW_ID,
    BASE_SAVED_VIEW_LABEL,
    base_saved_filter_view,
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


def _callback_for_output(app: Dash, component_id: str, component_property: str):
    metadata = next(
        item
        for item in app.callback_map.values()
        if any(
            str(output.component_id) == component_id
            and output.component_property == component_property
            for output in _outputs(item)
        )
    )
    return metadata["callback"].__wrapped__


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
    assert tuple(field.key for field in PL_SAVED_VIEW_CONTROLS.fields) == FILTER_KEYS
    assert {
        RISK_SAVED_VIEW_CONTROLS.selector_id,
        STOCK_SAVED_VIEW_CONTROLS.selector_id,
        PL_SAVED_VIEW_CONTROLS.selector_id,
    } == {
        "risk-saved-view-selector",
        "stock-saved-view-selector",
        "pnl-saved-view-selector",
    }
    css = (Path(__file__).parents[1] / "assets" / "s01_style.css").read_text()
    assert ".controls.filter-controls" in css
    assert "grid-template-columns: repeat(5, minmax(120px, 1fr));" in css
    assert ".saved-filter-view-disclosure[open]" in css
    assert ".saved-filter-view-panel" in css


def test_repository_is_shared_deterministic_and_atomic(tmp_path: Path) -> None:
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
    hedge = repository.save_new(
        "stock",
        "Hedge view",
        _filters("Hedge"),
        exclude_selected=False,
    )

    expected_names = [
        "alpha view",
        "Hedge view",
        "Zulu view",
    ]
    assert [view.name for view in repository.list("risk")] == expected_names
    assert [view.name for view in repository.list("stock")] == expected_names
    assert [view.name for view in repository.list("pnl")] == expected_names
    assert repository.get("risk", first.identifier).filters["portfolio"] == (
        "BOOK-A",
        "BOOK-B",
    )
    stock_view = repository.get("stock", first.identifier)
    assert stock_view.scope == "stock"
    assert stock_view.filters == first.filters
    assert repository.get("pnl", hedge.identifier).filters["activity"] == ("Hedge",)
    assert later.identifier != first.identifier
    assert {path.parent.name for path in root.rglob("*.json")} == {
        SHARED_SAVED_VIEW_SCOPE
    }
    assert not list(root.rglob("*.tmp"))
    assert {path.parent.name for path in root.rglob(".write.lock")} == {
        SHARED_SAVED_VIEW_SCOPE,
    }

    document = json.loads(
        (root / SHARED_SAVED_VIEW_SCOPE / f"{first.identifier}.json").read_text()
    )
    assert document == {
        "version": 1,
        "id": first.identifier,
        "scope": SHARED_SAVED_VIEW_SCOPE,
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


def test_repository_update_is_shared_atomic_and_preserves_identity(
    tmp_path: Path,
) -> None:
    repository = SavedFilterViewRepository(tmp_path, FILTER_KEYS)
    created = repository.save_new(
        "risk",
        "Morning",
        _filters(),
        exclude_selected=False,
    )

    updated = repository.update(
        "pnl",
        created.identifier,
        _filters("Updated"),
        exclude_selected=True,
    )

    assert updated.identifier == created.identifier
    assert updated.name == created.name
    assert updated.scope == "pnl"
    assert repository.get("risk", created.identifier).filters["activity"] == (
        "Updated",
    )
    assert repository.get("stock", created.identifier).exclude_selected is True
    assert not list(tmp_path.rglob("*.tmp"))

    errors: list[BaseException] = []

    def update(index: int) -> None:
        try:
            repository.update(
                ("risk", "stock", "pnl")[index % 3],
                created.identifier,
                _filters(f"Concurrent {index}"),
                exclude_selected=bool(index % 2),
            )
        except BaseException as error:  # pragma: no cover - thread handoff
            errors.append(error)

    threads = [Thread(target=update, args=(index,)) for index in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert not any(thread.is_alive() for thread in threads)
    assert errors == []
    final = repository.get("risk", created.identifier)
    assert final.filters["activity"][0].startswith("Concurrent ")
    assert len(repository.list("pnl")) == 1
    assert not list(tmp_path.rglob("*.tmp"))


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
            "stock",
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

    path = tmp_path / SHARED_SAVED_VIEW_SCOPE / f"{view.identifier}.json"
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
                ("risk", "stock", "pnl")[index % 3],
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
    assert [view.identifier for view in repository.list("stock")] == [
        view.identifier for view in repository.list("pnl")
    ]


def test_saved_view_editor_is_collapsed_with_an_always_present_base() -> None:
    bar = build_saved_filter_view_bar(RISK_SAVED_VIEW_CONTROLS)
    components = list(_walk(bar))
    selector = next(
        item
        for item in components
        if isinstance(item, dcc.Dropdown)
        and item.id == RISK_SAVED_VIEW_CONTROLS.selector_id
    )
    name = next(
        item
        for item in components
        if isinstance(item, dcc.Input) and item.id == RISK_SAVED_VIEW_CONTROLS.name_id
    )
    copy = " ".join(str(getattr(item, "children", "")) for item in components)

    assert isinstance(bar, html.Details)
    assert bar.open is False
    assert any(isinstance(item, html.Summary) for item in components)
    assert selector.value == BASE_SAVED_VIEW_ID
    assert selector.clearable is False
    assert selector.options[0] == {
        "label": BASE_SAVED_VIEW_LABEL,
        "value": BASE_SAVED_VIEW_ID,
    }
    assert name.debounce is False
    assert RISK_SAVED_VIEW_CONTROLS.apply_request_id in {
        getattr(item, "id", None) for item in components
    }
    assert RISK_SAVED_VIEW_CONTROLS.applied_request_id in {
        getattr(item, "id", None) for item in components
    }
    assert "shared across Risk, Stock, and P&L" in copy
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

    current = _filters("Manual")
    base_request = saved_view_apply_request(
        base_saved_filter_view(STOCK_SAVED_VIEW_CONTROLS),
        base_filters=current,
        base_exclude_selected=True,
    )
    base_values, base_exclude = saved_view_request_values(
        base_request,
        STOCK_SAVED_VIEW_CONTROLS,
    )
    assert base_values == ([], [], [], [], [])
    assert base_exclude == []
    assert saved_view_request_matches_base(
        base_request,
        STOCK_SAVED_VIEW_CONTROLS,
        tuple(current[key] for key in FILTER_KEYS),
        ["exclude"],
    )


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


def test_factory_shares_one_catalogue_without_sharing_live_page_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = SavedFilterViewRepository(tmp_path, FILTER_KEYS)
    named = repository.save_new(
        "risk",
        "Credit books",
        {
            **_filters("Credit"),
            "portfolio": ["BOOK-B", "BOOK-D"],
        },
        exclude_selected=True,
    )
    registered: list[tuple[SavedFilterViewRepository, object]] = []
    register = factory_module.register_saved_filter_view_callbacks

    def capture_registration(app, shared_repository, controls) -> None:
        registered.append((shared_repository, controls))
        register(app, shared_repository, controls)

    monkeypatch.setattr(
        factory_module,
        "register_saved_filter_view_callbacks",
        capture_registration,
    )
    app = factory_module.build_app(
        refresh_manager=build_production_refresh_manager(),
        stock_source=lambda _date: pd.DataFrame(),
        stock_portfolio_source=lambda _date: pd.DataFrame(),
        saved_view_root=tmp_path,
    )

    assert [controls for _repository, controls in registered] == [
        RISK_SAVED_VIEW_CONTROLS,
        PL_SAVED_VIEW_CONTROLS,
        STOCK_SAVED_VIEW_CONTROLS,
    ]
    assert (
        len({id(shared_repository) for shared_repository, _controls in registered}) == 1
    )
    assert registered[0][0]._root == tmp_path.resolve()

    for controls in (
        RISK_SAVED_VIEW_CONTROLS,
        STOCK_SAVED_VIEW_CONTROLS,
        PL_SAVED_VIEW_CONTROLS,
    ):
        refresh_catalogue = _callback_for_output(
            app,
            controls.selector_id,
            "options",
        )
        options, selected, _name, _status = refresh_catalogue(
            1,
            0,
            0,
            BASE_SAVED_VIEW_ID,
            "",
            *([[]] * len(FILTER_KEYS)),
            [],
        )
        assert options == [
            {"label": BASE_SAVED_VIEW_LABEL, "value": BASE_SAVED_VIEW_ID},
            {"label": named.name, "value": named.identifier},
        ]
        assert selected == BASE_SAVED_VIEW_ID

    risk_apply = _callback_for_output(
        app,
        RISK_SAVED_VIEW_CONTROLS.apply_request_id,
        "data",
    )
    risk_request = risk_apply(
        named.identifier,
        *([[]] * len(FILTER_KEYS)),
        [],
    )
    risk_values, risk_exclude = saved_view_request_values(
        risk_request,
        RISK_SAVED_VIEW_CONTROLS,
    )
    assert risk_values == (
        ["Credit"],
        ["SOG-A"],
        ["BOOK-B", "BOOK-D"],
        ["Core"],
        ["Rates"],
    )
    assert risk_exclude == ["exclude"]
    with pytest.raises(ValueError, match="another page"):
        saved_view_request_values(risk_request, STOCK_SAVED_VIEW_CONTROLS)

    stock_apply = _callback_for_output(
        app,
        STOCK_SAVED_VIEW_CONTROLS.apply_request_id,
        "data",
    )
    stock_request = stock_apply(
        BASE_SAVED_VIEW_ID,
        *(["keep-this-page"] for _key in FILTER_KEYS),
        ["exclude"],
    )
    stock_values, stock_exclude = saved_view_request_values(
        stock_request,
        STOCK_SAVED_VIEW_CONTROLS,
    )
    assert stock_values == ([], [], [], [], [])
    assert stock_exclude == []

    pnl_apply = _callback_for_output(
        app,
        PL_SAVED_VIEW_CONTROLS.apply_request_id,
        "data",
    )
    pnl_request = pnl_apply(
        BASE_SAVED_VIEW_ID,
        *(["pnl-local"] for _key in FILTER_KEYS),
        [],
    )
    pnl_values, pnl_exclude = saved_view_request_values(
        pnl_request,
        PL_SAVED_VIEW_CONTROLS,
    )
    assert pnl_values == ([], [], [], [], [])
    assert pnl_exclude == []
    assert {
        RISK_SAVED_VIEW_CONTROLS.apply_request_id,
        STOCK_SAVED_VIEW_CONTROLS.apply_request_id,
        PL_SAVED_VIEW_CONTROLS.apply_request_id,
    } == {
        "risk-saved-view-apply-request",
        "stock-saved-view-apply-request",
        "pnl-saved-view-apply-request",
    }


def test_callbacks_save_update_delete_and_apply_base(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = SavedFilterViewRepository(tmp_path, FILTER_KEYS)
    controls = RISK_SAVED_VIEW_CONTROLS
    app = Dash(__name__, suppress_callback_exceptions=True)
    app.layout = html.Div(
        [
            build_saved_filter_view_bar(controls),
            *[
                dcc.Dropdown(id=controls.filter_ids[field.key], value=[])
                for field in FILTER_DIMENSION_FIELDS
            ],
            dcc.Checklist(id=controls.exclude_id, value=[]),
        ]
    )
    register_saved_filter_view_callbacks(app, repository, controls)
    mutate = _callback_for_output(app, controls.selector_id, "options")
    apply = _callback_for_output(app, controls.apply_request_id, "data")
    actions = _callback_for_output(app, controls.save_id, "children")
    selected_values = [_filters()[key] for key in FILTER_KEYS]

    assert actions(BASE_SAVED_VIEW_ID) == ("Save New", True, False)
    monkeypatch.setattr(
        saved_views_module,
        "ctx",
        SimpleNamespace(triggered_id=controls.save_id),
    )
    saved = mutate(
        0,
        1,
        0,
        BASE_SAVED_VIEW_ID,
        "Morning",
        *selected_values,
        ["exclude"],
    )
    identifier = saved[1]
    assert saved[0][0]["value"] == BASE_SAVED_VIEW_ID
    assert saved[2] == ""
    assert "Saved new view: Morning" in saved[3]
    assert actions(identifier) == ("Update View", False, True)
    assert repository.get("stock", identifier).exclude_selected is True

    updated_filters = _filters("Updated")
    updated = mutate(
        0,
        2,
        0,
        identifier,
        "Ignored while updating",
        *(updated_filters[key] for key in FILTER_KEYS),
        [],
    )
    assert updated[1] == identifier
    assert updated[2] is no_update
    assert "Updated view: Morning" in updated[3]
    assert repository.get("pnl", identifier).filters["activity"] == ("Updated",)
    assert repository.get("risk", identifier).name == "Morning"

    base_request = apply(
        BASE_SAVED_VIEW_ID,
        *(updated_filters[key] for key in FILTER_KEYS),
        ["exclude"],
    )
    base_values, exclude = saved_view_request_values(base_request, controls)
    assert base_values == ([], [], [], [], [])
    assert exclude == []

    monkeypatch.setattr(
        saved_views_module,
        "ctx",
        SimpleNamespace(triggered_id=controls.delete_id),
    )
    deleted = mutate(
        0,
        2,
        1,
        identifier,
        "",
        *(updated_filters[key] for key in FILTER_KEYS),
        [],
    )
    assert deleted[0] == [{"label": BASE_SAVED_VIEW_LABEL, "value": BASE_SAVED_VIEW_ID}]
    assert deleted[1] == BASE_SAVED_VIEW_ID
    assert "Deleted view: Morning" in deleted[3]
    assert repository.list("stock") == ()


def test_repository_delete_is_exact_and_shared(tmp_path: Path) -> None:
    repository = SavedFilterViewRepository(tmp_path, FILTER_KEYS)
    risk = repository.save_new(
        "risk",
        "Shared label",
        _filters(),
        exclude_selected=False,
    )
    assert repository.get("stock", risk.identifier).name == "Shared label"

    deleted = repository.delete("pnl", risk.identifier)

    assert deleted.identifier == risk.identifier
    assert deleted.scope == "pnl"
    assert repository.list("risk") == ()
    assert repository.list("stock") == ()
    assert repository.list("pnl") == ()
