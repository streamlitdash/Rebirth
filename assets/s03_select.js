(() => {
  "use strict";

  let gesture = null;
  let suppressTrustedClickUntil = 0;

  const cellFromTarget = (target) =>
    target?.closest?.(".pl-send-editor-table td.dash-cell") || null;

  const cellCenter = (cell) => {
    const rect = cell.getBoundingClientRect();
    return {
      clientX: rect.left + (rect.width / 2),
      clientY: rect.top + (rect.height / 2),
    };
  };

  const dispatchNativeSelectionClick = (cell, shiftKey) => {
    const point = cellCenter(cell);
    cell.dispatchEvent(new MouseEvent("click", {
      bubbles: true,
      cancelable: true,
      view: window,
      button: 0,
      shiftKey,
      ...point,
    }));
  };

  const cancelGesture = () => {
    gesture = null;
    document.body.classList.remove("is-pl-datatable-range-dragging");
  };

  const clearSelection = (table) => {
    if (!table?.id) return;
    if (table.querySelectorAll("td.cell--selected").length < 2) return;
    document.getElementById(`${table.id}-selection-clear`)?.click();
  };

  const clearOtherSelections = (keptTable = null) => {
    document.querySelectorAll(
      ".pl-send-editor-table .dash-table-container[id]",
    ).forEach((table) => {
      if (table !== keptTable) clearSelection(table);
    });
  };

  document.addEventListener("mousedown", (event) => {
    const cell = cellFromTarget(event.target);
    if (!cell || event.button !== 0) {
      if (!event.target?.closest?.(".pl-editor-selection-summary")) {
        clearOtherSelections();
      }
      return;
    }
    const table = cell.closest(".dash-table-container");
    clearOtherSelections(table);
    gesture = {
      start: cell,
      end: cell,
      table,
      startX: event.clientX,
      startY: event.clientY,
      moved: false,
    };
  }, true);

  document.addEventListener("mousemove", (event) => {
    if (!gesture) return;
    const cell = cellFromTarget(event.target);
    if (!cell || cell.closest(".dash-table-container") !== gesture.table) return;
    const distance = Math.hypot(
      event.clientX - gesture.startX,
      event.clientY - gesture.startY,
    );
    if (distance < 6 && cell === gesture.start) return;
    gesture.moved = true;
    gesture.end = cell;
    document.body.classList.add("is-pl-datatable-range-dragging");
    event.preventDefault();
  }, true);

  document.addEventListener("mouseup", (event) => {
    if (!gesture) return;
    const finished = gesture;
    const endCell = cellFromTarget(event.target);
    if (
      endCell
      && endCell.closest(".dash-table-container") === finished.table
    ) {
      finished.end = endCell;
    }
    cancelGesture();
    if (!finished.moved || finished.start === finished.end) return;

    event.preventDefault();
    suppressTrustedClickUntil = Date.now() + 250;
    window.setTimeout(() => {
      if (
        !document.body.contains(finished.start)
        || !document.body.contains(finished.end)
      ) return;
      dispatchNativeSelectionClick(finished.start, false);
      window.setTimeout(() => {
        if (document.body.contains(finished.end)) {
          dispatchNativeSelectionClick(finished.end, true);
        }
      }, 0);
    }, 0);
  }, true);

  document.addEventListener("click", (event) => {
    if (
      event.isTrusted
      && Date.now() < suppressTrustedClickUntil
      && cellFromTarget(event.target)
    ) {
      event.preventDefault();
      event.stopImmediatePropagation();
    }
  }, true);

  document.addEventListener("dragstart", (event) => {
    if (gesture?.moved && cellFromTarget(event.target)) event.preventDefault();
  }, true);
  document.addEventListener("copy", () => {
    window.setTimeout(() => clearOtherSelections(), 0);
  }, true);
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      cancelGesture();
      clearOtherSelections();
    }
  }, true);
  window.addEventListener("blur", () => {
    cancelGesture();
    clearOtherSelections();
  });
})();
