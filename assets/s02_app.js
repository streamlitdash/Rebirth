(() => {
  const selectedCells = new Set();
  let rangeGesture = null;
  let suppressedMetricClick = null;
  let plotlyThemeTimer = null;
  let pendingPlotlyTheme = null;
  let pendingPlotlyThemeAll = false;
  const pendingPlotlyThemeGraphs = new Set();
  const plotlyThemeRetries = new WeakMap();
  let selectionSummaryTimer = null;
  let riskActionSequence = 0;
  let pendingRiskRowState = null;
  const THEME_KEY = "cube-theme-v1";
  const SELECTION_SUMMARY_TIMEOUT_MS = 4000;

  const prefersReducedMotion = () => window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches || false;

  const CUBE_ROLL_POINTS = Object.freeze([
    [0, -1],
    [0.8660254, -0.5],
    [0.8660254, 0.5],
    [0, 1],
    [-0.8660254, 0.5],
    [-0.8660254, -0.5],
    [0, -1],
  ]);
  const CUBE_ROLL_STEP_MS = 1200;
  const CUBE_ROLL_FRAME_MS = 1000 / 30;
  const CUBE_ROLL_ACTIVE_FRACTION = 1;
  const CUBE_ROLL_MAX_LIFT = 0.2071068;
  const cubeRollStates = new Map();
  let cubeRollFrame = null;
  let cubeRollWakeTimer = null;
  let cubeRollStopped = false;

  const multiplyQuaternion = (left, right) => ({
    w: left.w * right.w - left.x * right.x - left.y * right.y - left.z * right.z,
    x: left.w * right.x + left.x * right.w + left.y * right.z - left.z * right.y,
    y: left.w * right.y - left.x * right.z + left.y * right.w + left.z * right.x,
    z: left.w * right.z + left.x * right.y - left.y * right.x + left.z * right.w,
  });

  const normalizedQuaternion = (quaternion) => {
    const length = Math.hypot(quaternion.w, quaternion.x, quaternion.y, quaternion.z) || 1;
    return {
      w: quaternion.w / length,
      x: quaternion.x / length,
      y: quaternion.y / length,
      z: quaternion.z / length,
    };
  };

  const quaternionPower = (quaternion, exponent) => {
    let result = { w: 1, x: 0, y: 0, z: 0 };
    let factor = normalizedQuaternion(quaternion);
    let remaining = Math.max(0, Math.floor(exponent));
    while (remaining > 0) {
      if (remaining % 2 === 1) result = normalizedQuaternion(multiplyQuaternion(factor, result));
      factor = normalizedQuaternion(multiplyQuaternion(factor, factor));
      remaining = Math.floor(remaining / 2);
    }

    return result;
  };

  const axisQuaternion = (axis, angle) => {
    const length = Math.hypot(axis[0], axis[1], axis[2]) || 1;
    const half = angle / 2;
    const sine = Math.sin(half) / length;
    return {
      w: Math.cos(half),
      x: axis[0] * sine,
      y: axis[1] * sine,
      z: axis[2] * sine,
    };
  };

  const cubeRollSteps = CUBE_ROLL_POINTS.slice(0, -1).map((start, index) => {
    const end = CUBE_ROLL_POINTS[index + 1];
    const dx = end[0] - start[0];
    const dy = end[1] - start[1];
    return Object.freeze({ start, end, dx, dy, axis: Object.freeze([-dy, dx, 0]) });
  });

  const cubeRollBoundaries = [Object.freeze({ w: 1, x: 0, y: 0, z: 0 })];
  cubeRollSteps.forEach((step, index) => {
    cubeRollBoundaries.push(Object.freeze(normalizedQuaternion(multiplyQuaternion(
      axisQuaternion(step.axis, Math.PI / 2),
      cubeRollBoundaries[index],
    ))));
  });
  Object.freeze(cubeRollBoundaries);
  const cubeRollCircuitOrientation = cubeRollBoundaries[cubeRollBoundaries.length - 1];

  const quaternionMatrix = (quaternion) => {
    const { w, x, y, z } = normalizedQuaternion(quaternion);
    const values = [
      1 - 2 * (y * y + z * z),
      2 * (x * y + w * z),
      2 * (x * z - w * y),
      0,
      2 * (x * y - w * z),
      1 - 2 * (x * x + z * z),
      2 * (y * z + w * x),
      0,
      2 * (x * z + w * y),
      2 * (y * z - w * x),
      1 - 2 * (x * x + y * y),
      0,
      0,
      0,
      0,
      1,
    ];
    return `matrix3d(${values.map((value) => Math.abs(value) < 1e-9 ? 0 : value.toFixed(7)).join(",")})`;
  };

  const cubeRootIsVisible = (root) => {
    if (!root.isConnected || root.closest("[hidden]")) return false;
    if (root.closest("#refresh-progress.is-complete, #refresh-progress.is-error")) return false;
    const style = window.getComputedStyle(root);
    return style.display !== "none" && style.visibility !== "hidden" && root.getClientRects().length > 0;
  };

  const applyStaticCube = (state) => {
    state.traveller.style.transform = "translate3d(0, 0, 0)";
    state.lift.style.transform = "translate3d(0, 0, 0)";
    state.roller.style.transform = quaternionMatrix(cubeRollBoundaries[0]);
    state.shadow.style.opacity = "0.42";
    state.shadow.style.transform = "translateX(-50%) scale(1)";
    state.staticApplied = true;
  };

  const applyCubeRollFrame = (state, elapsed) => {
    const stepMs = CUBE_ROLL_STEP_MS;
    const circuits = stepMs * cubeRollSteps.length;
    const completedCircuits = Math.floor(elapsed / circuits);
    const circuitElapsed = elapsed % circuits;
    const stepPosition = circuitElapsed / stepMs;
    const stepIndex = Math.min(cubeRollSteps.length - 1, Math.floor(stepPosition));
    const stepFraction = stepPosition - stepIndex;
    const active = Math.min(stepFraction / CUBE_ROLL_ACTIVE_FRACTION, 1);
    const eased = active * active * (3 - 2 * active);
    const theta = eased * Math.PI / 2;
    const along = 0.5 * (1 - Math.cos(theta) + Math.sin(theta));
    const lift = 0.5 * (Math.cos(theta) + Math.sin(theta) - 1);
    const step = cubeRollSteps[stepIndex];

    const size = state.size;
    const x = (step.start[0] + step.dx * along) * size;
    const y = (step.start[1] + step.dy * along) * size;
    const orientation = multiplyQuaternion(
      axisQuaternion(step.axis, theta),
      multiplyQuaternion(
        cubeRollBoundaries[stepIndex],
        quaternionPower(cubeRollCircuitOrientation, completedCircuits),
      ),
    );

    state.traveller.style.transform = `translate3d(${x.toFixed(3)}px, ${y.toFixed(3)}px, 0)`;
    state.lift.style.transform = `translate3d(0, ${(-lift * size).toFixed(3)}px, 0)`;
    state.roller.style.transform = quaternionMatrix(orientation);
    const liftRatio = Math.max(0, Math.min(1, lift / CUBE_ROLL_MAX_LIFT));
    state.shadow.style.opacity = (0.42 - liftRatio * 0.18).toFixed(3);
    state.shadow.style.transform = `translateX(-50%) scale(${(1 - liftRatio * 0.28).toFixed(3)})`;

    state.staticApplied = false;
  };

  const registerCubeRollers = (scope = document) => {
    scope.querySelectorAll?.(".cube-motion").forEach((root) => {
      if (cubeRollStates.has(root)) return;
      const state = {
        root,
        traveller: root.querySelector(".cube-motion__traveller"),
        lift: root.querySelector(".cube-motion__lift"),
        roller: root.querySelector(".cube-motion__roller"),
        shadow: root.querySelector(".cube-motion__shadow"),
        elapsed: 0,
        lastTimestamp: null,
        lastPaintTimestamp: null,
        visible: false,
        staticApplied: false,
        size: parseFloat(window.getComputedStyle(root).getPropertyValue("--cube-size")) || 18,
      };
      if (!state.traveller || !state.lift || !state.roller || !state.shadow) return;
      cubeRollStates.set(root, state);
      applyCubeRollFrame(state, 0);
    });
    if (!cubeRollStopped && cubeRollFrame === null && cubeRollWakeTimer === null) {
      cubeRollFrame = requestAnimationFrame(runCubeRollers);
    }
  };

  function runCubeRollers(timestamp) {
    cubeRollFrame = null;
    let activeRoots = 0;
    const reduced = prefersReducedMotion();
    cubeRollStates.forEach((state, root) => {
      if (!root.isConnected) {
        cubeRollStates.delete(root);
        return;
      }

      const visible = cubeRootIsVisible(root);
      if (!visible) {
        state.visible = false;
        state.elapsed = 0;
        state.lastTimestamp = null;
        state.lastPaintTimestamp = null;
        return;
      }

      if (reduced) {
        if (!state.staticApplied) applyStaticCube(state);
        state.visible = false;
        state.elapsed = 0;
        state.lastTimestamp = null;
        state.lastPaintTimestamp = null;
        return;
      }
      activeRoots += 1;
      if (!state.visible) {
        state.visible = true;
        state.elapsed = 0;
        state.lastTimestamp = timestamp;
        state.lastPaintTimestamp = null;
        state.size = parseFloat(window.getComputedStyle(root).getPropertyValue("--cube-size")) || 18;
      } else {
        const delta = Math.min(100, Math.max(0, timestamp - (state.lastTimestamp ?? timestamp)));
        state.elapsed += delta;
        state.lastTimestamp = timestamp;
      }
      if (
        state.lastPaintTimestamp === null
        || timestamp - state.lastPaintTimestamp >= CUBE_ROLL_FRAME_MS
      ) {
        applyCubeRollFrame(state, state.elapsed);
        state.lastPaintTimestamp = timestamp;
      }
    });

    if (cubeRollStopped) return;
    if (activeRoots) {
      cubeRollFrame = requestAnimationFrame(runCubeRollers);
    } else {
      cubeRollWakeTimer = setTimeout(() => {
        cubeRollWakeTimer = null;
        registerCubeRollers();
      }, 250);
    }
  }

  const refreshCubeRollSizes = () => {
    cubeRollStates.forEach((state, root) => {
      if (!root.isConnected) return;
      const size = parseFloat(window.getComputedStyle(root).getPropertyValue("--cube-size")) || 18;
      if (size === state.size) return;
      state.size = size;
      if (state.visible && !prefersReducedMotion()) applyCubeRollFrame(state, state.elapsed);
    });
  };

  const savedTheme = () => {
    try {
      const value = window.localStorage.getItem(THEME_KEY);
      return value === "dark" || value === "light" ? value : null;
    } catch (_error) {
      return null;
    }
  };

  const themePalette = (theme) => theme === "dark"
    ? { background: "#0b1c13", ink: "#f1f9f3", grid: "#355642", line: "#6f8f7c" }
    : { background: "#ffffff", ink: "#111111", grid: "#e2e6ea", line: "#c7cdd4" };

  const syncPlotlyTheme = (theme, graphs) => {
    if (!window.Plotly?.relayout) return [];
    const palette = themePalette(theme);
    const retryGraphs = [];
    const themedAxis = (axis = {}) => {
      const currentTitle = typeof axis.title === "string" ? { text: axis.title } : (axis.title || {});
      return {
        ...axis,
        gridcolor: palette.grid,
        linecolor: palette.line,
        zerolinecolor: palette.line,
        tickfont: { ...(axis.tickfont || {}), color: palette.ink },
        title: { ...currentTitle, font: { ...(currentTitle.font || {}), color: palette.ink } },
      };
    };
    Array.from(graphs || []).forEach((graph) => {
      if (!graph?.isConnected) return;
      if (!graph.data || !graph.layout) {
        const retryCount = (plotlyThemeRetries.get(graph) || 0) + 1;
        if (retryCount <= 5) {
          plotlyThemeRetries.set(graph, retryCount);
          retryGraphs.push(graph);
        } else {
          plotlyThemeRetries.delete(graph);
        }
        return;
      }
      plotlyThemeRetries.delete(graph);
      const layoutUpdate = {
        paper_bgcolor: "rgba(0,0,0,0)",
        plot_bgcolor: palette.background,
        "font.color": palette.ink,
        xaxis: themedAxis(graph.layout.xaxis),
        yaxis: themedAxis(graph.layout.yaxis),
        "legend.bgcolor": "rgba(0,0,0,0)",
        "legend.bordercolor": palette.line,
        "legend.font.color": palette.ink,
      };
      if (graph.layout.yaxis2) layoutUpdate.yaxis2 = themedAxis(graph.layout.yaxis2);
      const update = window.Plotly.relayout(graph, layoutUpdate);
      update?.catch?.(() => {});
    });
    return retryGraphs;
  };

  const schedulePlotlyTheme = (theme, graphs = null, delay = 40) => {
    pendingPlotlyTheme = theme;
    if (graphs === null) {
      pendingPlotlyThemeAll = true;
      pendingPlotlyThemeGraphs.clear();
    } else if (!pendingPlotlyThemeAll) {
      Array.from(graphs).forEach((graph) => {
        if (graph?.isConnected) pendingPlotlyThemeGraphs.add(graph);
      });
    }
    if (plotlyThemeTimer) clearTimeout(plotlyThemeTimer);
    plotlyThemeTimer = setTimeout(() => {
      plotlyThemeTimer = null;
      const nextTheme = pendingPlotlyTheme || theme;
      const targets = pendingPlotlyThemeAll
        ? document.querySelectorAll(".js-plotly-plot")
        : Array.from(pendingPlotlyThemeGraphs);
      pendingPlotlyTheme = null;
      pendingPlotlyThemeAll = false;
      pendingPlotlyThemeGraphs.clear();
      const retryGraphs = syncPlotlyTheme(nextTheme, targets);
      if (retryGraphs.length) schedulePlotlyTheme(nextTheme, retryGraphs, 100);
    }, delay);
  };

  const updateThemeButton = (theme) => {
    const button = document.getElementById("theme-toggle");
    if (!button) return;
    const dark = theme === "dark";
    const symbol = dark ? "☀" : "☾";
    if (button.textContent !== symbol) button.textContent = symbol;
    button.setAttribute("aria-label", dark ? "Switch to light mode" : "Switch to dark mode");
    button.title = dark ? "Switch to light mode" : "Switch to dark mode";
    button.setAttribute("aria-pressed", String(dark));
  };

  const applyTheme = (theme, persist = false, relayoutAllPlots = false) => {
    const nextTheme = theme === "dark" ? "dark" : "light";
    document.documentElement.dataset.theme = nextTheme;
    if (document.body) document.body.dataset.theme = nextTheme;
    if (persist) {
      try {
        window.localStorage.setItem(THEME_KEY, nextTheme);
      } catch (_error) {
        // Storage can be unavailable in privacy-restricted browser sessions.
      }
    }
    updateThemeButton(nextTheme);
    if (relayoutAllPlots) schedulePlotlyTheme(nextTheme);
    return nextTheme;
  };

  let activeTheme = applyTheme(savedTheme() || (window.matchMedia?.("(prefers-color-scheme: dark)")?.matches ? "dark" : "light"));

  const ensureGlobalLoader = () => {
    let loader = document.getElementById("cube-global-loader");
    if (loader || !document.body) return loader;
    loader = document.createElement("div");
    loader.id = "cube-global-loader";
    loader.className = "cube-risk-loader cube-global-loader";
    loader.hidden = true;
    loader.setAttribute("role", "status");
    loader.setAttribute("aria-live", "polite");
    const faces = ["front", "back", "right", "left", "top", "bottom"]
      .map((face) => `<i class="cube-motion__face cube-motion__face--${face}"></i>`)
      .join("");
    loader.innerHTML = `<span class="cube-motion cube-motion--loader" aria-hidden="true"><span class="cube-motion__scene"><span class="cube-motion__traveller"><span class="cube-motion__shadow"></span><span class="cube-motion__lift"><span class="cube-motion__roller"><span class="cube-motion__view"><span class="cube-motion__solid">${faces}</span></span></span></span></span></span></span><span class="cube-loader-label">Loading Cube&hellip;</span>`;
    document.body.appendChild(loader);
    registerCubeRollers(loader);
    return loader;
  };

  const dashIsLoading = () => Boolean(document.querySelector('[data-dash-is-loading="true"]'));

  const setGlobalLoaderVisible = (visible) => {
    // The global cube is refresh feedback, not generic Dash callback
    // feedback. Creating it for a tab/filter callback both obscures
    // the current table and starts a requestAnimationFrame loop while
    // React is replacing a large hierarchy.
    const loader = visible
      ? ensureGlobalLoader()
      : document.getElementById("cube-global-loader");
    if (!loader) return;
    if (!visible) {
      loader.hidden = true;
      loader.classList.remove("is-visible");
      document.body.classList.remove("cube-is-loading");
      return;
    }

    const customLoader = Array.from(document.querySelectorAll(".cube-risk-loader:not(#cube-global-loader)")).find((candidate) => {
      if (candidate.closest("#refresh-progress[hidden], [hidden]")) return false;
      const style = window.getComputedStyle(candidate);
      return style.display !== "none" && style.visibility !== "hidden" && candidate.getClientRects().length > 0;
    });
    const shouldShow = !customLoader;
    loader.hidden = !shouldShow;
    loader.classList.toggle("is-visible", shouldShow);
    document.body.classList.add("cube-is-loading");
  };

  const numberFromCell = (cell) => {
    const raw = (
      cell.dataset.copyValue
      ?? cell.querySelector(".copy-value, .metric-cell-button")?.textContent
      ?? ""
    ).replace(/[$£€¥,\s]/g, "");
    if (!raw) return null;
    const value = Number(raw);
    return Number.isFinite(value) ? value : null;
  };

  const formatNumber = (value, decimals, minimumDecimals = decimals) => {
    return value.toLocaleString(undefined, {
      minimumFractionDigits: minimumDecimals,
      maximumFractionDigits: decimals,
    });
  };

  const setSelected = (cell, selected, update = true) => {
    if (!cell) return;
    if (selected) {
      selectedCells.add(cell);
      cell.classList.add("risk-cell-selected");
    } else {
      selectedCells.delete(cell);
      cell.classList.remove("risk-cell-selected");
    }
    if (update) updateSelectionSummary();
  };

  const clearSelection = (update = true) => {
    selectedCells.forEach((cell) => cell.classList.remove("risk-cell-selected"));
    selectedCells.clear();
    if (update) updateSelectionSummary();
  };

  const hideSelectionSummaries = (clearText = false) => {
    if (selectionSummaryTimer) clearTimeout(selectionSummaryTimer);
    selectionSummaryTimer = null;
    document.querySelectorAll(".selection-summary").forEach((box) => {
      box.classList.remove("is-visible");
      if (clearText) box.textContent = "";
    });
  };

  const scheduleSelectionSummaryDismiss = () => {
    if (selectionSummaryTimer) clearTimeout(selectionSummaryTimer);
    selectionSummaryTimer = setTimeout(() => {
      selectionSummaryTimer = null;
      document.querySelectorAll(".selection-summary").forEach((box) => {
        box.classList.remove("is-visible");
      });
    }, SELECTION_SUMMARY_TIMEOUT_MS);
  };

  const updateSelectionSummary = () => {
    for (const cell of Array.from(selectedCells)) {
      if (!document.body.contains(cell) || cell.closest("tr")?.hidden) {
        selectedCells.delete(cell);
        cell.classList.remove("risk-cell-selected");
      }
    }
    hideSelectionSummaries(true);
    const selectionsByWrap = new Map();
    selectedCells.forEach((cell) => {
      const wrap = cell.closest(".risk-table-wrap");
      const metric = cell.dataset.metric || "value";
      const value = numberFromCell(cell);
      if (!wrap || value === null) return;
      if (!selectionsByWrap.has(wrap)) selectionsByWrap.set(wrap, { metrics: new Set(), values: [] });
      const selection = selectionsByWrap.get(wrap);
      selection.metrics.add(metric);
      selection.values.push(value);
    });
    selectionsByWrap.forEach(({ metrics, values }, wrap) => {
      const box = wrap.querySelector(".selection-summary");
      if (!box) return;
      const sum = values.reduce((total, value) => total + value, 0);
      const average = sum / values.length;
      const minimum = Math.min(...values);
      const maximum = Math.max(...values);
      const baseMetrics = Array.from(metrics, (metric) => metric.toLowerCase().split(":", 1)[0]);
      const decimals = baseMetrics.some((metric) => metric === "move")
        ? 6
        : baseMetrics.some((metric) => metric === "open" || metric === "current")
          ? 4
          : 2;
      const label = metrics.size === 1 ? Array.from(metrics)[0] : "Selection";
      box.textContent = `${label} | Count ${values.length} · Sum ${formatNumber(sum, decimals, 0)} · Average ${formatNumber(average, decimals, 0)} · Min ${formatNumber(minimum, decimals, 0)} · Max ${formatNumber(maximum, decimals, 0)} · Ctrl/Cmd+C to copy · Esc to clear`;
      box.classList.add("is-visible");
    });
    if (selectionsByWrap.size) scheduleSelectionSummaryDismiss();
  };

  const clipboardValueFromCell = (cell) => {
    if (!cell) return "";
    const explicitValue = cell.dataset.copyValue;
    let displayed = explicitValue === undefined
      ? (cell.querySelector(".copy-value, .row-label-text, .metric-cell-button")?.textContent || "").trim()
      : explicitValue.trim();
    if (!displayed) {
      const copy = cell.cloneNode(true);
      copy.querySelectorAll("button, .promotion-badge").forEach((node) => node.remove());
      displayed = (copy.textContent || "").trim();
    }
    if (!displayed) return "";
    const normalized = displayed
      .replace(/\u2212/g, "-")
      .replace(/[$£€¥,\s]/g, "");
    return normalized && Number.isFinite(Number(normalized))
      ? normalized
      : displayed.replace(/[\t\r\n]+/g, " ");
  };

  const selectedCellsAsTsv = () => {
    const selected = new Set(
      Array.from(selectedCells).filter(
        (cell) => document.body.contains(cell) && !cell.closest("tr")?.hidden,
      ),
    );
    if (!selected.size) return "";
    return Array.from(document.querySelectorAll(".risk-table, .cell-selection-table"))
      .map((table) => {
        const tableCells = Array.from(selected).filter(
          (cell) => cell.closest(".risk-table, .cell-selection-table") === table,
        );
        if (!tableCells.length) return null;
        const rowIndexes = tableCells.map((cell) => cell.parentElement?.rowIndex);
        const columnIndexes = tableCells.map((cell) => cell.cellIndex);
        const rowMin = Math.min(...rowIndexes);
        const rowMax = Math.max(...rowIndexes);
        const columnMin = Math.min(...columnIndexes);
        const columnMax = Math.max(...columnIndexes);
        const cellByPosition = new Map(
          tableCells.map((cell) => [
            `${cell.parentElement?.rowIndex}:${cell.cellIndex}`,
            cell,
          ]),
        );
        const visibleRowIndexes = Array.from(
          table.querySelectorAll("tbody tr"),
        ).filter((row) => (
          !row.hidden
          && row.rowIndex >= rowMin
          && row.rowIndex <= rowMax
        )).map((row) => row.rowIndex);
        const rows = [];
        for (const row of visibleRowIndexes) {
          const values = [];
          for (let column = columnMin; column <= columnMax; column += 1) {
            values.push(
              clipboardValueFromCell(cellByPosition.get(`${row}:${column}`)),
            );
          }
          rows.push(values.join("\t"));
        }
        return rows.join("\r\n");
      })
      .filter(Boolean)
      .join("\r\n\r\n");
  };

  const isEditableClipboardTarget = (target) => Boolean(
    target?.closest?.(
      "input, textarea, [contenteditable]:not([contenteditable='false'])",
    ),
  );

  const cellsInRectangle = (start, end) => {
    const table = start?.closest?.(".risk-table, .cell-selection-table");
    if (!table || end?.closest?.(".risk-table, .cell-selection-table") !== table) return [];
    const startRow = start.parentElement?.rowIndex;
    const endRow = end.parentElement?.rowIndex;
    if (!Number.isInteger(startRow) || !Number.isInteger(endRow)) return [];
    const rowMin = Math.min(startRow, endRow);
    const rowMax = Math.max(startRow, endRow);
    const columnMin = Math.min(start.cellIndex, end.cellIndex);
    const columnMax = Math.max(start.cellIndex, end.cellIndex);
    return Array.from(table.querySelectorAll(
      "tbody td.metric-cell, tbody th.index-cell, tbody th.aggregate-index",
    )).filter((cell) => {
      const row = cell.parentElement?.rowIndex;
      return !cell.parentElement?.hidden
        && Number.isInteger(row)
        && row >= rowMin
        && row <= rowMax
        && cell.cellIndex >= columnMin
        && cell.cellIndex <= columnMax
        && clipboardValueFromCell(cell) !== "";
    });
  };

  const applyRangeSelection = (gesture, end) => {
    clearSelection(false);
    gesture.base.forEach((cell) => {
      if (document.body.contains(cell)) setSelected(cell, true, false);
    });
    cellsInRectangle(gesture.start, end).forEach((cell) => setSelected(cell, true, false));
    updateSelectionSummary();
  };

  const finishRangeGesture = () => {
    if (!rangeGesture) return;
    if (!rangeGesture.moved && rangeGesture.toggleSingle) {
      clearSelection(false);
      rangeGesture.base.forEach((cell) => {
        if (document.body.contains(cell)) setSelected(cell, true, false);
      });
      setSelected(
        rangeGesture.start,
        !rangeGesture.base.has(rangeGesture.start),
        false,
      );
      updateSelectionSummary();
    }
    if (rangeGesture.moved || rangeGesture.append) {
      suppressedMetricClick = {
        cells: new Set([rangeGesture.start, rangeGesture.end]),
        expires: Date.now() + 250,
      };
    }
    rangeGesture = null;
    document.body.classList.remove("is-range-selecting");
  };

  const selectVisibleColumn = (metric, append, table) => {
    if (!table) return;
    if (!append) clearSelection(false);
    table.querySelectorAll(
      "tbody td.metric-cell, tbody th.index-cell, tbody th.aggregate-index",
    ).forEach((cell) => {
      if (
        !cell.closest("tr")?.hidden
        && cell.dataset.metric === metric
        && clipboardValueFromCell(cell) !== ""
      ) {
        setSelected(cell, true, false);
      }
      });
      updateSelectionSummary();
    };

    const riskTablesWithin = (scope) => {
      const tables = [];
      if (scope?.matches?.(".risk-table")) tables.push(scope);
      scope?.querySelectorAll?.(".risk-table").forEach((table) => tables.push(table));
      return tables;
    };

    const attachResizeHandles = (scope = document) => {
      riskTablesWithin(scope).forEach((table) => table.querySelectorAll("thead th").forEach((header, index) => {
        if (Array.from(header.children).some((child) => child.classList.contains("col-resize-handle"))) return;
        const handle = document.createElement("span");
        handle.className = "col-resize-handle";
        handle.tabIndex = 0;
        handle.setAttribute("role", "separator");
        handle.setAttribute("aria-orientation", "vertical");
        handle.setAttribute("aria-label", `Resize column ${header.textContent.trim() || index + 1}`);
        handle.title = "Drag or use arrow keys to resize; double-click to reset";
        header.appendChild(handle);
        const resizeColumn = (width) => {
          table.querySelectorAll(`tr > *:nth-child(${index + 1})`).forEach((cell) => {
            cell.style.width = `${width}px`;
            cell.style.minWidth = `${width}px`;
            cell.style.maxWidth = `${width}px`;
          });
        };
        handle.addEventListener("mousedown", (event) => {
          event.preventDefault();
          event.stopPropagation();
          const startX = event.clientX;
          const startWidth = header.getBoundingClientRect().width;

          const onMove = (moveEvent) => {
            const width = Math.max(82, startWidth + moveEvent.clientX - startX);
            resizeColumn(width);
          };

          const onUp = () => {
            document.removeEventListener("mousemove", onMove, true);
            document.removeEventListener("mouseup", onUp, true);
          };

          document.addEventListener("mousemove", onMove, true);
          document.addEventListener("mouseup", onUp, true);
        });
        handle.addEventListener("keydown", (event) => {
          if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
          event.preventDefault();
          const step = event.shiftKey ? 24 : 8;
          const direction = event.key === "ArrowRight" ? 1 : -1;
          resizeColumn(Math.max(82, header.getBoundingClientRect().width + direction * step));
        });
        handle.addEventListener("dblclick", () => {
          table.querySelectorAll(`tr > *:nth-child(${index + 1})`).forEach((cell) => {
            cell.style.removeProperty("width");
            cell.style.removeProperty("min-width");
            cell.style.removeProperty("max-width");
          });
        });
      }));
    };

    let uiHookTimer = null;
    const syncUiHooks = () => {
      if (uiHookTimer) clearTimeout(uiHookTimer);
      uiHookTimer = setTimeout(() => {
        uiHookTimer = null;
        document.body.dataset.theme = activeTheme;
        updateThemeButton(activeTheme);
      }, 30);
    };

    let cubeHookTimer = null;
    const scheduleCubeRegistration = () => {
      if (cubeHookTimer) clearTimeout(cubeHookTimer);
      cubeHookTimer = setTimeout(() => {
        cubeHookTimer = null;
        registerCubeRollers();
      }, 30);
    };

    const uiHookObserver = new MutationObserver((mutations) => {
      let themeButtonAdded = false;
      let cubeAdded = false;
      const plotlyGraphsAdded = new Set();
      const inspectHook = (element) => {
        if (element.matches?.("#theme-toggle")) themeButtonAdded = true;
        if (element.matches?.(".cube-motion")) cubeAdded = true;
        if (element.matches?.(".js-plotly-plot")) plotlyGraphsAdded.add(element);
      };

      mutations.forEach((mutation) => Array.from(mutation.addedNodes).forEach((node) => {
        if (node.nodeType !== 1) return;
        inspectHook(node);
        node.querySelectorAll?.("#theme-toggle, .cube-motion, .js-plotly-plot")
          .forEach(inspectHook);
      }));

      if (themeButtonAdded) syncUiHooks();
      if (cubeAdded) scheduleCubeRegistration();
      if (plotlyGraphsAdded.size)
        schedulePlotlyTheme(activeTheme, plotlyGraphsAdded, 40);
    });
    uiHookObserver.observe(document.body, { childList: true, subtree: true });
    syncUiHooks();
    scheduleCubeRegistration();
    const initialPlotlyGraphs = document.querySelectorAll(".js-plotly-plot");
    if (initialPlotlyGraphs.length) {
      schedulePlotlyTheme(activeTheme, initialPlotlyGraphs, 40);
    }

    const systemThemeQuery = window.matchMedia?.("(prefers-color-scheme: dark)");
    systemThemeQuery?.addEventListener?.("change", (event) => {
      if (savedTheme()) return;
      activeTheme = applyTheme(event.matches ? "dark" : "light", false, true);
    });

    const pendingResizeScopes = new Set();
    let resizeRefreshFrame = null;
    const scheduleResizeRefresh = (scope = document) => {
      pendingResizeScopes.add(scope);
      if (resizeRefreshFrame !== null) return;
      resizeRefreshFrame = requestAnimationFrame(() => {
        resizeRefreshFrame = null;
        pendingResizeScopes.forEach((pendingScope) => {
          if (pendingScope === document || pendingScope?.isConnected) {
            attachResizeHandles(pendingScope);
          }
        });
        pendingResizeScopes.clear();
        updateSelectionSummary();
      });
    };

    // The two hierarchy output nodes are stable Dash components.  A
    // scoped observer on each is enough to enhance a replacement table;
    // observing every mutation under document.body caused thousands of
    // callbacks while React mounted an IR hierarchy.
    const riskGridObservers = new Map();
    let riskGridObserverAttempts = 0;
    let riskGridObserverRetry = null;

    const connectRiskGridObservers = () => {
      riskGridObserverRetry = null;
      ["risk-grid", "alt-risk-grid"].forEach((id) => {
        const grid = document.getElementById(id);
        if (!grid || riskGridObservers.has(grid)) return;
        const observer = new MutationObserver((mutations) => {
          const tableChanged = mutations.some((mutation) => Array.from(mutation.addedNodes).some((node) => {
            if (node.nodeType !== 1 || node.matches?.(".col-resize-handle")) return false;
            return Boolean(
              node.matches?.(".risk-table")
              || node.closest?.(".risk-table")
              || node.querySelector?.(".risk-table")
            );
          }));
          if (tableChanged) scheduleResizeRefresh(grid);
        });
        observer.observe(grid, { childList: true, subtree: true });
        riskGridObservers.set(grid, observer);
        scheduleResizeRefresh(grid);
      });

      riskGridObserverAttempts += 1;
      if (riskGridObservers.size < 2 && riskGridObserverAttempts < 40) {
        riskGridObserverRetry = setTimeout(connectRiskGridObservers, 100);
      }
    };
    connectRiskGridObservers();

    let refreshProgressState = null;
    let refreshProgressClock = null;
    let backendProgressRequest = null;
    let backendProgressNextPoll = 0;
    let backendProgressAvailable = null;
    let lastBackendProgress = null;
    let backendProgressFailures = 0;
    let backendProgressLastError = "";
    let backendProgressLastSuccessAt = 0;
    let backendStartRequest = null;
    let backendStartNextAttempt = 0;
    let backendStartFailures = 0;
    const BACKEND_PROGRESS_POLL_MS = 1000;
    const BACKEND_PROGRESS_REQUEST_TIMEOUT_MS = 30000;
    const BACKEND_PROGRESS_FAILURE_LIMIT = 2;
    const BACKEND_RETRY_MAX_MS = 30000;

    const clearRefreshProgressTimers = () => {
      if (refreshProgressClock) clearInterval(refreshProgressClock);
      refreshProgressClock = null;
    };

    const backendEndpointUrl = (name) => {
      try {
        const endpointNode = document.getElementById("backend-endpoints");
        const configured = name === "start"
          ? endpointNode?.dataset.startUrl
          : endpointNode?.dataset.progressUrl;
        if (configured) return new URL(configured, window.location.origin).toString();
        const configNode = document.getElementById("_dash-config");
        const config = configNode?.textContent ? JSON.parse(configNode.textContent) : {};
        const prefix = String(config.requests_pathname_prefix || window.location.pathname || "/");
        const normalizedPrefix = `${prefix.replace(/\/+$/, "")}/`;
        return new URL(`${normalizedPrefix}${name}`, window.location.origin).toString();
      } catch (_error) {
        return new URL(`${name}`, document.baseURI).toString();
      }
    };

    const progressEndpointUrl = () => backendEndpointUrl("progress");
    const startEndpointUrl = () => backendEndpointUrl("start");

    const transportErrorText = (error, endpoint) => {
      if (error?.name === "AbortError")
        return `${endpoint} timed out after ${BACKEND_PROGRESS_REQUEST_TIMEOUT_MS / 1000}s`;
      const detail = String(error?.message || error || "unknown transport error")
        .replace(/\s+/g, " ")
        .trim();
      return `${endpoint}: ${detail}`;
    };

    const REFRESH_STAGES = ["readiness", "risk", "market", "pl", "final"];

    const normalizeProgressStage = (stage, functionName = "") => {
      const value = `${stage || ""} ${functionName || ""}`
        .trim()
        .toLowerCase()
        .replace(/_/g, " ")
        .replace(/-/g, " ")
        .replace(/\//g, " ")
        .replace(/\s+/g, " ");
      if (!value) return null;
      if (/\b(final|publish|commit|validate|complete|combine|config|threshold|merge|group|aggregate|aggregation|dashboard)\b/.test(value)) return "final";
      if (/\b(p&l|pnl|pl|profit|calculate)\b/.test(value)) return "pl";
      if (/\b(market|official|open|live|current|price)\b/.test(value)) return "market";
      if (/\b(readiness|ready|status|starting|start)\b/.test(value)) return "readiness";
      if (/\b(risk|snapshot|connector|position)\b/.test(value)) return "risk";
      return null;
    };

    const progressStartedAt = (value) => {
      if (value === null || value === undefined || value === "") return null;
      if (typeof value === "number" || /^\d+(\.\d+)?$/.test(String(value))) {
        const numeric = Number(value);
        if (!Number.isFinite(numeric)) return null;
        return numeric < 10_000_000_000 ? numeric * 1000 : numeric;
      }
      const parsed = Date.parse(String(value));
      return Number.isFinite(parsed) ? parsed : null;
    };

    const progressStartedDuringAttempt = (progress, browserStartedAt) => {
      if (!progress || progress.started_at === null) return false;
      // Compare clocks using the server timestamp carried by the same
      // response. A user's computer and Plotly worker need not agree
      // within the old 500 ms window.
      const clockOffset = progress.server_time === null
        ? 0
        : progress.received_at - progress.server_time;
      return progress.started_at + clockOffset >= browserStartedAt - 1000;
    };

    const progressFingerprint = (progress) => progress ? JSON.stringify([
      progress.attempt_id,
      progress.started_at,
      progress.running,
      progress.function_name,
      progress.stage,
      progress.source_type,
      progress.underlying,
      progress.product_label,
      progress.product_index,
      progress.product_total,
      progress.hold_seconds,
      progress.current,
      progress.total,
      progress.message,
      progress.error,
      progress.updated_at,
      progress.revision,
      progress.startup_phase,
      progress.startup_attempt_id,
      progress.server_boot_id,
    ]) : "";

    const requestBackendProgress = async (force = false) => {
      const now = Date.now();
      if (!force && now < backendProgressNextPoll) {
        return backendProgressAvailable === false ? null : lastBackendProgress;
      }
      if (backendProgressRequest) return backendProgressRequest;
      backendProgressNextPoll = now + BACKEND_PROGRESS_POLL_MS;
      backendProgressRequest = (async () => {
        const controller = new AbortController();
        const timeout = setTimeout(
          () => controller.abort(),
          BACKEND_PROGRESS_REQUEST_TIMEOUT_MS,
        );
        try {
          const response = await fetch(progressEndpointUrl(), {
            cache: "no-store",
            credentials: "same-origin",
            headers: { Accept: "application/json" },
            signal: controller.signal,
          });
          if (!response.ok) throw new Error(`progressz returned ${response.status}`);
          const contentType = response.headers.get("content-type") || "";
          if (!contentType.toLowerCase().includes("application/json")) {
            throw new Error(`progressz returned ${contentType || "non-JSON content"}`);
          }
          const payload = await response.json();
          if (!payload || typeof payload !== "object") throw new Error("progressz did not return an object");
          const numberOrNull = (value) => {
            if (value === null || value === undefined || value === "") return null;
            const parsed = Number(value);
            return Number.isFinite(parsed) ? parsed : null;
          };
          const progress = {
        running: payload.running === true || payload.running === 1 || payload.running === "true",
            attempt_id: String(payload.attempt_id || ""),
            function_name: String(payload.function_name || ""),
            stage: String(payload.stage || ""),
            source_type: String(payload.source_type || ""),
            underlying: String(payload.underlying || ""),
            product_label: String(payload.product_label || ""),
            product_index: numberOrNull(payload.product_index),
            product_total: numberOrNull(payload.product_total),
            hold_seconds: numberOrNull(payload.hold_seconds),
            current: numberOrNull(payload.current),
            total: numberOrNull(payload.total),
            message: String(payload.message || ""),
            started_at: progressStartedAt(payload.started_at),
            updated_at: progressStartedAt(payload.updated_at),
            revision: numberOrNull(payload.revision) || 0,
            startup_phase: String(payload.startup_phase || ""),
            startup_attempt_id: String(payload.startup_attempt_id || ""),
            server_boot_id: String(payload.server_boot_id || ""),
            server_time: progressStartedAt(payload.server_time),
            received_at: Date.now(),
            error: typeof payload.error === "string"
              ? payload.error
              : payload.error ? String(payload.message || "Backend refresh failed") : "",
          };
          backendProgressFailures = 0;
          backendProgressAvailable = true;
          backendProgressLastError = "";
          backendProgressLastSuccessAt = Date.now();
          backendProgressNextPoll = Date.now() + BACKEND_PROGRESS_POLL_MS;
          lastBackendProgress = progress;
          return progress;
        } catch (error) {
          backendProgressFailures += 1;
          backendProgressLastError = transportErrorText(error, "progressz");
          const retryDelay = Math.min(
            BACKEND_RETRY_MAX_MS,
            BACKEND_PROGRESS_POLL_MS * (2 ** Math.min(backendProgressFailures - 1, 3)),
          );
          backendProgressNextPoll = Date.now() + retryDelay;
          if (backendProgressFailures >= BACKEND_PROGRESS_FAILURE_LIMIT) {
            backendProgressAvailable = false;
            return null;
          }
          return lastBackendProgress;
        } finally {
          clearTimeout(timeout);
          backendProgressRequest = null;
        }
      })();
      return backendProgressRequest;
    };

    const requestBackendStart = async (force = false) => {
      const now = Date.now();
      if (!force && now < backendStartNextAttempt) return null;
      if (backendStartRequest) return backendStartRequest;
      backendStartNextAttempt = now + BACKEND_PROGRESS_POLL_MS;
      backendStartRequest = (async () => {
        const controller = new AbortController();
        const timeout = setTimeout(
          () => controller.abort(),
          BACKEND_PROGRESS_REQUEST_TIMEOUT_MS,
        );
        try {
          const response = await fetch(startEndpointUrl(), {
            method: "POST",
            cache: "no-store",
            credentials: "same-origin",
            headers: { Accept: "application/json" },
            signal: controller.signal,
          });
          if (!response.ok) throw new Error(`startz returned ${response.status}`);
          const contentType = response.headers.get("content-type") || "";
          if (!contentType.toLowerCase().includes("application/json")) {
            throw new Error(`startz returned ${contentType || "non-JSON content"}`);
          }
          await response.json();
          backendStartFailures = 0;
          backendStartNextAttempt = Date.now() + 5000;
          return true;
        } catch (error) {
          backendStartFailures += 1;
          backendProgressLastError = transportErrorText(error, "startz");
          backendStartNextAttempt = Date.now() + Math.min(
            BACKEND_RETRY_MAX_MS,
            BACKEND_PROGRESS_POLL_MS * (2 ** Math.min(backendStartFailures, 3)),
          );
          return false;
        } finally {
          clearTimeout(timeout);
          backendStartRequest = null;
        }
      })();
      return backendStartRequest;
    };

    const setRefreshStageState = (stage, state) => {
      const row = document.getElementById(`refresh-stage-${stage}`);
      if (!row) return;
      row.classList.remove("is-active", "is-complete", "is-skipped", "is-error");
      if (state) row.classList.add(`is-${state}`);
    };

    const configureRefreshStage = (stage, _functionName, progressText = "", _sourceType = "") => {
      const row = document.getElementById(`refresh-stage-${stage}`);
      if (!row) return;
      const duration = row.querySelector(".refresh-stage-duration");
      if (duration) duration.textContent = progressText;
    };

    const setProgressDetail = (id, value) => {
      const node = document.getElementById(id);
      const nextValue = value || "";
      if (node && node.textContent !== nextValue) node.textContent = nextValue;
    };

    const refreshStatusNode = () => (
      document.getElementById("refresh-status")
      || document.getElementById("bootstrap-refresh-status")
    );

    const refreshErrorNode = () => (
      document.getElementById("error-log")
      || document.getElementById("bootstrap-error-log")
    );

    const displayFunctionName = (functionName) => {
      const value = String(functionName || "");
      return /^[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*$/.test(value)
        ? `${value}()`
        : value;
    };

    const renderBackendProgress = (progress) => {
      if (!progress || !refreshProgressState) return;
      const panel = document.getElementById("refresh-progress");
      if (panel) {
        panel.dataset.progressSource = "backend";
        if (!progress.error)
          panel.classList.remove("is-error");
        panel.classList.add("is-running");
      }

      const functionName = progress.function_name || "Backend task";
      const stage = normalizeProgressStage(progress.stage, functionName);
      const hasProduct = progress.product_index !== null
        && progress.product_total !== null
        && progress.product_total > 0;
      const isUnderlyingLoop = ["market", "market_open", "market_status"]
        .includes(stage);
      const unitLabel = isUnderlyingLoop ? "Underlying" : "Product";
      const countText = hasProduct
        ? `${unitLabel} ${Math.max(0, progress.product_index)} of ${progress.product_total}`
        : "";
      const holdText = hasProduct && progress.hold_seconds > 0
        ? `${progress.hold_seconds}s Risk/dRisk hold`
        : "";
      const percent = hasProduct
        ? Math.max(0, Math.min(100, (progress.product_index / progress.product_total) * 100))
        : 0;
      setProgressDetail("refresh-progress-function", displayFunctionName(functionName));
      setProgressDetail(
        "refresh-progress-source",
        [progress.source_type, progress.underlying].filter(Boolean).join(" - "),
      );
      setProgressDetail("refresh-progress-count", countText);
      setProgressDetail("refresh-progress-hold", holdText);
      setProgressDetail(
        "refresh-progress-product",
        hasProduct
          ? isUnderlyingLoop
            ? `${progress.underlying || "Market underlying"} - ${progress.error ? "market call failed" : "loading market data"}`
            : `${progress.product_label || "Risk product"} - ${progress.error ? "Risk & dRisk failed" : "loading Risk & dRisk"}`
          : progress.message || progress.product_label || "Refresh pipeline",
      );

      const meter = document.getElementById("refresh-progress-bar-track");
      const bar = document.getElementById("refresh-progress-bar");
      if (bar && hasProduct) bar.style.width = `${percent}%`;
      if (meter) {
        meter.hidden = !["reload", "bootstrap"].includes(refreshProgressState.mode);
        if (hasProduct) {
          meter.setAttribute("role", "progressbar");
          meter.setAttribute("aria-valuemin", "0");
          meter.setAttribute("aria-valuenow", String(progress.product_index));
          meter.setAttribute("aria-valuemax", String(progress.product_total));
          meter.setAttribute("aria-label", countText);
          meter.setAttribute(
            "aria-valuetext",
            `${progress.underlying || progress.product_label || unitLabel}, ${countText}`,
          );
        } else {
          meter.removeAttribute("role");
          meter.removeAttribute("aria-valuemin");
          meter.removeAttribute("aria-valuenow");
          meter.removeAttribute("aria-valuemax");
          meter.removeAttribute("aria-label");
          meter.removeAttribute("aria-valuetext");
        }
      }

      const title = document.getElementById("refresh-progress-title");
      if (progress.error) {
        refreshProgressState.backendError = progress.error;
        refreshProgressState.backendErrorStage = stage;
        if (title) title.textContent = progress.error;
      }
      if (!stage) return;

      const stages = REFRESH_STAGES;
      const activeIndex = stages.indexOf(stage);
      stages.forEach((candidate, index) => {
        const row = document.getElementById(`refresh-stage-${candidate}`);
        if (!row || row.classList.contains("is-skipped")) return;
        if (index < activeIndex) {
          setRefreshStageState(candidate, "complete");
          if (candidate !== "risk") configureRefreshStage(candidate, "", "Complete");
        }
        else if (index === activeIndex) setRefreshStageState(candidate, progress.error ? "error" : progress.running ? "active" : "complete");
        else setRefreshStageState(candidate, null);
        if (index !== activeIndex) {
          row.style.removeProperty("--stage-progress");
          row.removeAttribute("role");
          row.removeAttribute("aria-valuemin");
          row.removeAttribute("aria-valuenow");
          row.removeAttribute("aria-valuemax");
        }
      });

      const activeRow = document.getElementById(`refresh-stage-${stage}`);
      if (activeRow) {
        activeRow.style.setProperty("--stage-progress", `${percent}%`);
      }
      configureRefreshStage(
        stage,
        functionName,
        hasProduct ? countText : progress.running ? "Running" : "Complete",
        progress.source_type,
      );
    };

    const recoverReadyBootstrap = (progress) => {
      if (
        !refreshProgressState
        || refreshProgressState.mode !== "bootstrap"
        || Number(progress?.revision || 0) < 1
      ) return false;
      if (refreshProgressState.reloadRequested) return true;
      const state = refreshProgressState;
      state.reloadRequested = true;
      const title = document.getElementById("refresh-progress-title");
      if (title) title.textContent = "Opening validated dashboard";
      setProgressDetail(
        "refresh-progress-function",
        "Server refresh completed; synchronising this browser",
      );
      setProgressDetail(
        "refresh-progress-product",
        "Revision is ready",
      );
      const handoffDeadline = Date.now() + 15000;
      const recoverMount = () => {
        if (!refreshProgressState || refreshProgressState !== state) return;
        if (!document.querySelector(".cube-initial-load-shell")) {
          finishRefreshProgress();
          return;
        }
        if (dashIsLoading() && Date.now() < handoffDeadline) {
          setTimeout(recoverMount, 1000);
          return;
        }
        window.location.reload();
      };
      setTimeout(recoverMount, 3000);
      return true;
    };

    const startRefreshProgress = (mode) => {
      const panel = document.getElementById("refresh-progress");
      if (!panel) return;
      setGlobalLoaderVisible(true);
      clearRefreshProgressTimers();
      const reloadAll = mode === "reload";
      const bootstrap = mode === "bootstrap";
      const portfolioOnly = mode === "portfolios";
      const fullRiskLoad = reloadAll || bootstrap;
      const automatic = mode === "automatic";
      const requestedFunction = bootstrap
        ? "RiskRefreshManager.refresh(initial_load)"
        : reloadAll ? "RiskRefreshManager.refresh(force_risk=True)"
        : portfolioOnly ? "RiskRefreshManager.refresh_portfolios()"
        : "RiskRefreshManager.refresh(force_pl=True)";
      const requestedSource = fullRiskLoad
        ? "all connector sources"
        : portfolioOnly ? "portfolio mapping connector only"
        : automatic ? "automatic 15-minute refresh" : "manual P&L refresh";
      const riskProductDelay = Number(panel.dataset.riskProductDelay || 0);
      const title = document.getElementById("refresh-progress-title");
      const elapsed = document.getElementById("refresh-progress-elapsed");
      if (title) title.textContent = bootstrap ? "Loading Cube data" : reloadAll ? "Reloading all risk" : portfolioOnly ? "Refreshing portfolios" : automatic ? "Automatic refresh" : "Refreshing P&L";
      panel.hidden = false;
      panel.classList.remove("is-complete", "is-error");
      panel.classList.add("is-running");
      panel.dataset.progressSource = "pending";
      REFRESH_STAGES.forEach((stage) => {
        setRefreshStageState(stage, null);
        const row = document.getElementById(`refresh-stage-${stage}`);
        row?.style.removeProperty("--stage-progress");
        row?.removeAttribute("role");
        row?.removeAttribute("aria-valuemin");
        row?.removeAttribute("aria-valuenow");
        row?.removeAttribute("aria-valuemax");
      });
      configureRefreshStage("readiness", "get_risk_checker", portfolioOnly ? "Not called" : "queued");
      configureRefreshStage(
        "risk",
        fullRiskLoad ? requestedFunction : "Conditional on changed readiness dates",
        fullRiskLoad && riskProductDelay > 0
          ? `Risk products / ${riskProductDelay}s each`
          : fullRiskLoad ? "queued" : "Conditional",
      );
      configureRefreshStage("market", requestedFunction, "queued", requestedSource);
      configureRefreshStage("pl", requestedFunction, "queued", requestedSource);
      configureRefreshStage("final", "_commit_full_snapshot", "queued");
      setProgressDetail("refresh-progress-function", requestedFunction);
      setProgressDetail("refresh-progress-source", requestedSource);
      setProgressDetail("refresh-progress-count", "");
      setProgressDetail("refresh-progress-hold", "");
      setProgressDetail(
        "refresh-progress-product",
        portfolioOnly
          ? "Reloading portfolio mapping and rebuilding dependent views"
          : fullRiskLoad
            ? "Preparing Risk & dRisk product calls"
            : "Checking readiness before conditional risk and market/P&L refresh",
      );
      const progressBar = document.getElementById("refresh-progress-bar");
      if (progressBar) progressBar.style.width = "0%";
      const progressTrack = document.getElementById("refresh-progress-bar-track");
      if (progressTrack) progressTrack.hidden = !fullRiskLoad;
      if (portfolioOnly) {
        ["readiness", "risk", "market", "pl"].forEach((stage) => {
          setRefreshStageState(stage, "skipped");
          configureRefreshStage(stage, "", "Not called");
        });
        setRefreshStageState("final", "active");
      } else {
        setRefreshStageState("readiness", "active");
      }

      const startedAt = Date.now();
      refreshProgressState = {
        mode,
        startedAt,
        sawRunning: false,
        sawBackendRunning: false,
        sawBackendAttempt: false,
        baselineProgressKey: null,
        baselineRefreshAttemptId: lastBackendProgress?.attempt_id || null,
        baselineRevision: lastBackendProgress
          ? Number(lastBackendProgress.revision || 0)
          : null,
        baselineAttemptId: null,
        serverBootId: null,
        reloadRequested: false,
        transportLostAt: null,
        backendError: "",
        backendErrorStage: null,
        initialErrorText: (refreshErrorNode()?.textContent || "").trim(),
        initialStatusText: (refreshStatusNode()?.textContent || "").trim(),
      };
      const updateElapsed = () => {
        if (elapsed && refreshProgressState) {
          elapsed.textContent = `${Math.floor((Date.now() - startedAt) / 1000)}s elapsed`;
        }
      };
      updateElapsed();
      refreshProgressClock = setInterval(updateElapsed, 1000);
      backendProgressNextPoll = 0;
      if (bootstrap) void requestBackendStart(true);
      void requestBackendProgress(true).then((progress) => {
        if (!progress || !refreshProgressState || refreshProgressState.startedAt !== startedAt) return;
        refreshProgressState.serverBootId = progress.server_boot_id || null;
        const refreshAttemptChanged = Boolean(
          progress.attempt_id
          && refreshProgressState.baselineRefreshAttemptId
          && progress.attempt_id !== refreshProgressState.baselineRefreshAttemptId
        );
        const revisionAdvanced = (
          refreshProgressState.baselineRevision !== null
          && progress.revision > refreshProgressState.baselineRevision
        );
        const belongsToAttempt = (
          (
            bootstrap
            && Boolean(progress.startup_attempt_id)
            && progress.startup_attempt_id !== refreshProgressState.baselineAttemptId
          )
          || refreshAttemptChanged
          || revisionAdvanced
          || progressStartedDuringAttempt(progress, startedAt)
        );
        if (recoverReadyBootstrap(progress)) return;
        if (progress.running) {
          refreshProgressState.sawBackendRunning = true;
          refreshProgressState.sawBackendAttempt = true;
          renderBackendProgress(progress);
        } else if (belongsToAttempt) {
          refreshProgressState.sawBackendAttempt = true;
          renderBackendProgress(progress);
        } else {
          refreshProgressState.baselineProgressKey = progressFingerprint(progress);
          refreshProgressState.baselineRefreshAttemptId = progress.attempt_id || null;
          refreshProgressState.baselineRevision = progress.revision;
          refreshProgressState.baselineAttemptId = progress.startup_attempt_id || null;
        }
      });
  };

  const finishRefreshProgress = () => {
    if (!refreshProgressState) return;
    const panel = document.getElementById("refresh-progress");
    const title = document.getElementById("refresh-progress-title");
    const state = refreshProgressState;
    const errorText = (refreshErrorNode()?.textContent || "").trim();
    const backendError = state.backendError || "";
    const hasNewError = Boolean(backendError || (errorText && errorText !== state.initialErrorText));
    clearRefreshProgressTimers();
    panel?.classList.remove("is-running");
    panel?.classList.add(hasNewError ? "is-error" : "is-complete");
    const errorStage = hasNewError ? state.backendErrorStage : null;
    const stages = REFRESH_STAGES;
    stages.forEach((stage, index) => {
      const row = document.getElementById(`refresh-stage-${stage}`);
      if (row) {
        row.style.removeProperty("--stage-progress");
        row.removeAttribute("role");
        row.removeAttribute("aria-valuemin");
        row.removeAttribute("aria-valuenow");
        row.removeAttribute("aria-valuemax");
        if (stage === errorStage) {
          setRefreshStageState(stage, "error");
          configureRefreshStage(stage, "", "Failed");
        }
        else if (row.classList.contains("is-skipped")) return;
        else if (errorStage) {
          const errorIndex = stages.indexOf(errorStage);
          const completed = index < errorIndex;
          setRefreshStageState(stage, completed ? "complete" : null);
          if (completed && stage !== "risk") configureRefreshStage(stage, "", "Complete");
        } else {
          setRefreshStageState(stage, "complete");
          if (stage !== "risk") configureRefreshStage(stage, "", "Complete");
        }
      }
    });
    if (title) title.textContent = hasNewError
      ? `Refresh failed - ${backendError || "last successful data retained"}`
      : "Refresh complete";
    setProgressDetail(
      "refresh-progress-product",
      hasNewError
        ? (state.mode === "bootstrap" ? "No financial snapshot was published" : "Previous validated snapshot retained")
        : "Validated snapshot is live",
    );
    setProgressDetail("refresh-progress-hold", "");
    const progressBar = document.getElementById("refresh-progress-bar");
    if (progressBar) progressBar.style.width = hasNewError ? "0%" : "100%";
    const progressTrack = document.getElementById("refresh-progress-bar-track");
    if (progressTrack) progressTrack.hidden = hasNewError || !["reload", "bootstrap"].includes(state.mode);
    refreshProgressState = null;
    setGlobalLoaderVisible(false);
    // With no last-good snapshot, the startup incident and its
    // failed stage stay visible beside Retry. Later refresh errors
    // still collapse back to the usable committed dashboard.
    if (!(hasNewError && state.mode === "bootstrap")) {
      setTimeout(() => {
        if (!refreshProgressState && panel) panel.hidden = true;
      }, hasNewError ? 5000 : 1600);
    }
  };

  let refreshProgressTickRunning = false;
  const refreshProgressPoll = setInterval(async () => {
    if (refreshProgressTickRunning) return;
    refreshProgressTickRunning = true;
    try {
      const running = refreshStatusNode()?.classList.contains("is-refreshing") || false;
      // Checking Dash's global loading tree is only useful during a
      // refresh attempt. In particular, an ordinary Risk Explorer
      // tab callback must not activate the cube loader.
      const dashLoading = (running || Boolean(refreshProgressState))
        ? dashIsLoading()
        : false;
      if (running && !refreshProgressState) {
        const initialLoad = document.getElementById("refresh-progress")?.dataset.initialLoad === "true";
        startRefreshProgress(initialLoad ? "bootstrap" : "automatic");
      }
      setGlobalLoaderVisible(running || Boolean(refreshProgressState));
      if (!refreshProgressState) return;

      if (running || dashLoading) refreshProgressState.sawRunning = true;
      const progress = await requestBackendProgress();
      if (!refreshProgressState) return;
      const statusText = (refreshStatusNode()?.textContent || "").trim();
      const statusChanged = Boolean(statusText && statusText !== refreshProgressState.initialStatusText);
      if (progress) {
        refreshProgressState.transportLostAt = null;
        const previousBootId = refreshProgressState.serverBootId;
        const serverWasReplaced = Boolean(
          previousBootId
          && progress.server_boot_id
          && previousBootId !== progress.server_boot_id
        );
        refreshProgressState.serverBootId = progress.server_boot_id || previousBootId;
        if (serverWasReplaced) {
          refreshProgressState.sawBackendRunning = false;
          refreshProgressState.sawBackendAttempt = false;
          refreshProgressState.baselineProgressKey = null;
          refreshProgressState.baselineRefreshAttemptId = null;
          refreshProgressState.baselineRevision = null;
          refreshProgressState.baselineAttemptId = null;
        }
        if (recoverReadyBootstrap(progress)) return;
        if (
          refreshProgressState.mode === "bootstrap"
          && Number(progress.revision || 0) === 0
          && ["", "idle"].includes(progress.startup_phase)
        ) {
          void requestBackendStart();
        }
        const isBaselineSnapshot = Boolean(
          refreshProgressState.baselineProgressKey
          && progressFingerprint(progress) === refreshProgressState.baselineProgressKey
        );
        const startupAttemptMatches = (
          refreshProgressState.mode === "bootstrap"
          && Boolean(progress.startup_attempt_id)
          && progress.startup_attempt_id !== refreshProgressState.baselineAttemptId
        );
        const refreshAttemptMatches = Boolean(
          progress.attempt_id
          && refreshProgressState.baselineRefreshAttemptId
          && progress.attempt_id !== refreshProgressState.baselineRefreshAttemptId
        );
        const revisionAdvanced = (
          refreshProgressState.baselineRevision !== null
          && progress.revision > refreshProgressState.baselineRevision
        );
        const attemptMatches = (
          startupAttemptMatches || refreshAttemptMatches || revisionAdvanced
        );
        const timestampMatchesAttempt = (
          progressStartedDuringAttempt(progress, refreshProgressState.startedAt)
          && !isBaselineSnapshot
        );
        if (progress.running) {
          refreshProgressState.sawBackendRunning = true;
          refreshProgressState.sawBackendAttempt = true;
          renderBackendProgress(progress);
        } else if (
          refreshProgressState.sawBackendAttempt
          || refreshProgressState.sawBackendRunning
          || attemptMatches
          || timestampMatchesAttempt
        ) {
          refreshProgressState.sawBackendAttempt = true;
          renderBackendProgress(progress);
          // Only the refresh callback's running state gates this
          // panel. Revision-driven table callbacks may legitimately
          // retain unrelated Dash loading markers while they render.
          if (!running) finishRefreshProgress();
        } else if (!running && !dashLoading && (refreshProgressState.sawRunning || statusChanged)) {
          if (!isBaselineSnapshot) renderBackendProgress(progress);
          finishRefreshProgress();
        }
      } else {
        const panel = document.getElementById("refresh-progress");
        if (panel) panel.dataset.progressSource = backendProgressAvailable === false ? "fallback" : "pending";
        if (backendProgressAvailable === false) {
          if (!refreshProgressState.transportLostAt) {
            refreshProgressState.transportLostAt = Date.now();
          }
          if (refreshProgressState.mode === "bootstrap") {
            void requestBackendStart();
          }
          const disconnectedFor = Date.now() - refreshProgressState.transportLostAt;
          const sinceSuccess = backendProgressLastSuccessAt
            ? `Last response ${Math.max(1, Math.floor((Date.now() - backendProgressLastSuccessAt) / 1000))}s ago.`
            : "";
          setProgressDetail(
            "refresh-progress-function",
            `Reconnecting to server progress. ${backendProgressLastError || "No JSON response."}${sinceSuccess}`,
          );
          if (disconnectedFor >= 15000) {
            const title = document.getElementById("refresh-progress-title");
            if (title) title.textContent = "Server connection interrupted - retrying";
            setProgressDetail(
              "refresh-progress-product",
              "Refresh state is not confirmed; automatic recovery is active",
            );
            panel?.classList.add("is-error");
          }
          if (
            disconnectedFor >= 45000
            && refreshProgressState.mode === "bootstrap"
          ) {
            const recoveryKey = "cube-progress-recovery-reload-at";
            let lastRecovery = 0;
            try {
              lastRecovery = Number(window.sessionStorage.getItem(recoveryKey) || 0);
            } catch (_error) {
              lastRecovery = 0;
            }
            if (Date.now() - lastRecovery >= 60000) {
              try {
                window.sessionStorage.setItem(recoveryKey, String(Date.now()));
              } catch (_error) {
                // A restricted browser can still perform the reload.
              }
              window.location.reload();
              return;
            }
          }
        }
        if (!running && !dashLoading && (refreshProgressState.sawRunning || statusChanged)) {
          finishRefreshProgress();
        }
      }
    } finally {
      refreshProgressTickRunning = false;
    }
  }, BACKEND_PROGRESS_POLL_MS);

  const hasAggregationModifier = (event) => event.shiftKey || event.ctrlKey || event.metaKey;
  const syncQuickSearchHierarchy = (table) => {
    if (!table?.matches?.(".quick-search-pivot-table")) return;
    const rows = Array.from(
      table.querySelectorAll("tbody tr.quick-search-hierarchy-row"),
    );
    const rowsByPath = new Map(
      rows.map((row) => [row.dataset.quickSearchPath, row]),
    );
    rows.forEach((row) => {
      const depth = Number(row.dataset.quickSearchDepth);
      const parent = rowsByPath.get(row.dataset.quickSearchParentPath);
      const visible = depth === 1 || Boolean(
        parent
        && !parent.hidden
        && parent.dataset.quickSearchOpen === "true"
      );
      row.hidden = !visible;

      const toggle = row.querySelector(".quick-search-hierarchy-toggle");
      if (!toggle) return;
      const expanded = row.dataset.quickSearchOpen === "true";
      const action = expanded ? "Collapse" : "Expand";
      const dimension = row.dataset.quickSearchDimension || "level";
      const label = row.dataset.quickSearchLabel || "group";
      toggle.textContent = expanded ? "\u25be" : "\u203a";
      toggle.setAttribute("aria-expanded", String(expanded));
      toggle.setAttribute("aria-label", `${action} ${dimension}: ${label}`);
      toggle.title = `${action} ${dimension}: ${label}`;
    });
  };

  const toggleQuickSearchHierarchy = (toggle) => {
    if (!toggle || toggle.disabled) return false;
    const row = toggle.closest("tr.quick-search-hierarchy-row");
    const table = toggle.closest("table.quick-search-pivot-table");
    if (!row || !table) return false;
    row.dataset.quickSearchOpen = String(
      row.dataset.quickSearchOpen !== "true",
    );
    syncQuickSearchHierarchy(table);
    // Hidden descendants must never remain in spreadsheet copy or
    // aggregation state after an instantaneous client-side collapse.
    clearSelection();
    return true;
  };

  const riskActionStore = Object.freeze({
    row: "risk-row-action-store",
    cell: "risk-cell-action-store",
    metric: "risk-metric-action-store",
  });
  const publishRiskAction = (node) => {
    if (!node || node.disabled) return false;
    const isTopBookCell = node.classList.contains("top-book-metric-cell-button");
    const isTopBookRow = node.classList.contains("row-toggle")
      && Boolean(node.closest("#top-book-grid"));
    const kind = node.classList.contains("row-toggle")
      ? "row"
      : node.classList.contains("metric-header-button")
        ? "metric"
        : node.classList.contains("metric-cell-button")
          ? "cell"
          : null;
    const storeId = isTopBookCell
      ? "top-book-cell-action-store"
      : isTopBookRow
        ? "top-book-row-action-store"
        : riskActionStore[kind];
    const setProps = window.dash_clientside?.set_props;
    if (!storeId || typeof setProps !== "function") return false;
    const viewRoot = node.closest("[data-risk-view-token]");
    const viewToken = viewRoot?.dataset.riskViewToken;
    if (!viewToken && !isTopBookCell) return false;

    riskActionSequence += 1;
    const action = {
      kind,
      sequence: riskActionSequence,
    };
    if (viewToken) action.view_token = viewToken;
    const nodeRiskKey = node.dataset.riskKey
      ?? node.closest("tr")?.dataset.riskKey;
    if (kind === "row") {
      action.source = node.dataset.riskSource
        || (isTopBookRow
          ? "top-book-row-toggle"
          : node.closest("#alt-risk-grid")
            ? "alt-row-toggle"
            : "main-row-toggle");
      const renderedRows = viewRoot.dataset.riskOpenRows || "[]";
      if (
        !pendingRiskRowState
        || pendingRiskRowState.viewToken !== viewToken
        || pendingRiskRowState.renderedRows !== renderedRows
      ) {
        let parsedRows = [];
        try {
          const value = JSON.parse(renderedRows);
          if (Array.isArray(value) && value.every((item) => typeof item === "string")) {
            parsedRows = value;
          }
        } catch (_error) {
          parsedRows = [];
        }
        pendingRiskRowState = {
          viewToken,
          renderedRows,
          rows: new Set(parsedRows),
        };
      }
      const key = nodeRiskKey;
      if (!key) return false;
      if (pendingRiskRowState.rows.has(key)) pendingRiskRowState.rows.delete(key);
      else pendingRiskRowState.rows.add(key);
      action.open_rows = Array.from(pendingRiskRowState.rows).sort();
    } else if (kind === "cell") {
      action.source = node.dataset.riskSource
        || (isTopBookCell
          ? "top-book-risk-cell"
          : node.classList.contains("credit-measure-cell-button")
            ? "credit-risk-cell"
            : node.closest("#alt-risk-grid")
              ? "alt-risk-cell"
              : "main-risk-cell");
    }
    if (nodeRiskKey !== undefined) action.key = nodeRiskKey;
    if (node.dataset.riskMetric !== undefined) action.metric = node.dataset.riskMetric;
    if (node.dataset.riskMeasure !== undefined) action.measure = node.dataset.riskMeasure;
    setProps(storeId, { data: action });
    return true;
  };
  const metricCellFromTarget = (target) => {
    if (target?.closest?.(".row-toggle, .aggregate-row-toggle")) return null;
    return target?.closest?.(
      ".risk-table tbody td.metric-cell, .risk-table tbody th.index-cell, "
      + ".cell-selection-table tbody td.metric-cell, "
      + ".cell-selection-table tbody th.index-cell, "
      + ".cell-selection-table tbody th.aggregate-index"
    ) || null;
  };

  document.addEventListener("keydown", (event) => {
    const readonlyRadio = event.target?.closest?.(
      ".aggregate-pl-selector input[type='radio'][readonly], .table-dimension-selector input[type='radio'][readonly]"
    );
    if (readonlyRadio && ["Enter", " ", "ArrowRight", "ArrowDown", "ArrowLeft", "ArrowUp"].includes(event.key)) {
      event.preventDefault();
      event.stopPropagation();
      if (event.key === "Enter" || event.key === " ") {
        readonlyRadio.click();
        return;
      }
      const group = readonlyRadio.closest(".aggregate-pl-selector, .table-dimension-selector");
      const radios = Array.from(group?.querySelectorAll("input[type='radio'][readonly]:not(:disabled)") || []);
      const current = radios.indexOf(readonlyRadio);
      if (current < 0 || radios.length < 2) return;
      const direction = event.key === "ArrowRight" || event.key === "ArrowDown" ? 1 : -1;
      const target = radios[(current + direction + radios.length) % radios.length];
      target.focus();
      target.click();
      return;
    }

    const isF9 = event.code === "F9" || event.key === "F9";
    if (isF9 && event.shiftKey && !event.repeat) {
      const refreshButton = document.getElementById("refresh-pl-button");
      if (!refreshButton || refreshButton.disabled) return;
      event.preventDefault();
      event.stopPropagation();
      refreshButton.click();
      return;
    }

    const isF8 = event.code === "F8" || event.key === "F8";
    if (isF8 && event.shiftKey && !event.repeat) {
      const riskButton = document.getElementById("reload-risk-button");
      if (!riskButton || riskButton.disabled) return;
      event.preventDefault();
      event.stopPropagation();
      riskButton.click();
      return;
    }

    if (event.key === "Escape" && selectedCells.size) {
      event.preventDefault();
      clearSelection();
      return;
    }

    const cell = metricCellFromTarget(event.target);
    if (!cell || !hasAggregationModifier(event) || (event.key !== "Enter" && event.key !== " ")) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    setSelected(cell, !selectedCells.has(cell));
    suppressedMetricClick = {
      cells: new Set([cell]),
      expires: Number.POSITIVE_INFINITY,
      keyboardOnly: true,
    };
  }, true);

  document.addEventListener("keyup", (event) => {
    if (
      (event.key !== "Enter" && event.key !== " ")
      || !suppressedMetricClick?.keyboardOnly
    ) return;
    const pendingSuppression = suppressedMetricClick;
    setTimeout(() => {
      if (suppressedMetricClick === pendingSuppression) suppressedMetricClick = null;
    }, 0);
  }, true);

  document.addEventListener("copy", (event) => {
    if (!selectedCells.size || isEditableClipboardTarget(event.target)) return;
    const text = selectedCellsAsTsv();
    if (!text || !event.clipboardData) return;
    event.clipboardData.setData("text/plain", text);
    event.clipboardData.setData("text/tab-separated-values", text);
    event.preventDefault();
    hideSelectionSummaries();
  }, true);

  document.addEventListener("click", (event) => {
    const themeButton = event.target.closest("#theme-toggle");
    if (themeButton) {
      event.preventDefault();
      if (selectedCells.size) clearSelection();
      activeTheme = applyTheme(
        activeTheme === "dark" ? "light" : "dark",
        true,
        true,
      );
      return;
    }

    const quickSearchToggle = event.target.closest(
      ".quick-search-hierarchy-toggle",
    );
    if (quickSearchToggle) {
      event.preventDefault();
      event.stopImmediatePropagation();
      toggleQuickSearchHierarchy(quickSearchToggle);
      return;
    }

    const aggregationCell = metricCellFromTarget(event.target);
    const selectionHeader = event.target.closest(
      ".risk-table thead th.metric-header, .risk-table thead th.index-header, "
      + ".cell-selection-table thead th.metric-header, .cell-selection-table thead th.index-header"
    );
    if (
      selectedCells.size
      && !aggregationCell
      && !event.target.closest(".selection-summary")
      && !(selectionHeader && hasAggregationModifier(event))
    ) {
      clearSelection();
    }
    const matchesSuppressedCell = aggregationCell
      && suppressedMetricClick
      && suppressedMetricClick.expires > Date.now()
      && (!suppressedMetricClick.keyboardOnly || event.detail === 0)
      && suppressedMetricClick.cells.has(aggregationCell);
    if (aggregationCell && (hasAggregationModifier(event) || matchesSuppressedCell)) {
      event.preventDefault();
      event.stopImmediatePropagation();
      suppressedMetricClick = null;
      return;
    }
    if (aggregationCell) suppressedMetricClick = null;
    if (aggregationCell?.closest(".cell-selection-table")) {
      event.preventDefault();
      clearSelection(false);
      setSelected(aggregationCell, true);
      return;
    }

    // Risk Explorer tables can contain hundreds of interactive
    // controls. They deliberately have no Dash pattern IDs: one
    // delegated event publishes a compact action through a stable
    // Store, so replacing a hierarchy does not remount hundreds of
    // callback endpoints. Modifier gestures remain reserved for
    // spreadsheet-style selection and are handled below.
    const riskAction = event.target.closest(
      "#risk-grid .row-toggle, "
      + "#risk-grid .metric-cell-button[data-risk-metric], "
      + "#risk-grid .metric-header-button[data-risk-metric], "
      + "#alt-risk-grid .row-toggle, "
      + "#alt-risk-grid .metric-cell-button[data-risk-metric], "
      + "#top-book-grid .row-toggle, "
      + "#top-book-grid .top-book-metric-cell-button[data-risk-metric]"
    );
    if (riskAction && !(selectionHeader && hasAggregationModifier(event))) {
      event.preventDefault();
      if (publishRiskAction(riskAction)) return;
    }

    const refreshTrigger = event.target.closest(
      "#refresh-portfolios-button, #refresh-pl-button, #reload-risk-button, #initial-load-retry"
    );
    if (refreshTrigger) {
      const mode = refreshTrigger.id === "reload-risk-button"
        ? "reload"
        : refreshTrigger.id === "refresh-portfolios-button" ? "portfolios"
        : refreshTrigger.id === "initial-load-retry" ? "bootstrap" : "pl";
      startRefreshProgress(mode);
    }
    const header = event.target.closest(
      ".risk-table thead th.metric-header, .risk-table thead th.index-header, "
      + ".cell-selection-table thead th.metric-header, .cell-selection-table thead th.index-header"
    );
    if (!header || !(event.ctrlKey || event.metaKey || event.shiftKey)) return;
    const metric = header.dataset.metric;
    if (!metric) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    selectVisibleColumn(
      metric,
      event.ctrlKey || event.metaKey || event.shiftKey,
      header.closest(".risk-table, .cell-selection-table")
    );
  }, true);

  document.addEventListener("mousedown", (event) => {
    const cell = metricCellFromTarget(event.target);
    if (!cell || event.button !== 0) return;
    const append = hasAggregationModifier(event);
    if (!append && selectedCells.size) clearSelection();
    if (append) event.preventDefault();
    rangeGesture = {
      start: cell,
      end: cell,
      append,
      base: append ? new Set(selectedCells) : new Set(),
      moved: false,
      toggleSingle: !event.shiftKey && (event.ctrlKey || event.metaKey),
    };
    if (append) applyRangeSelection(rangeGesture, cell);
  }, true);

  document.addEventListener("mouseover", (event) => {
    if (!rangeGesture) return;
    const cell = metricCellFromTarget(event.target);
    if (!cell || cell.closest(".risk-table, .cell-selection-table") !== rangeGesture.start.closest(".risk-table, .cell-selection-table")) return;
    if (cell !== rangeGesture.end) {
      event.preventDefault();
      rangeGesture.end = cell;
      rangeGesture.moved = rangeGesture.moved || cell !== rangeGesture.start;
      document.body.classList.toggle("is-range-selecting", rangeGesture.moved);
      applyRangeSelection(rangeGesture, cell);
    }
  }, true);

  document.addEventListener("mousemove", (event) => {
    if (!rangeGesture) return;
    const cell = metricCellFromTarget(event.target);
    if (!cell || cell.closest(".risk-table, .cell-selection-table") !== rangeGesture.start.closest(".risk-table, .cell-selection-table")) return;
    if (cell !== rangeGesture.end) {
      event.preventDefault();
      rangeGesture.end = cell;
      rangeGesture.moved = rangeGesture.moved || cell !== rangeGesture.start;
      document.body.classList.toggle("is-range-selecting", rangeGesture.moved);
      applyRangeSelection(rangeGesture, cell);
    }
  }, true);

  document.addEventListener("mouseup", finishRangeGesture, true);
  window.addEventListener("blur", () => {
    finishRangeGesture();
    suppressedMetricClick = null;
    hideSelectionSummaries();
  });
  document.addEventListener("visibilitychange", () => {
    cubeRollStates.forEach((state) => {
      state.lastTimestamp = null;
      state.lastPaintTimestamp = null;
    });
    registerCubeRollers();
  });
  window.addEventListener("pagehide", (event) => {
    if (event.persisted) return;
    cubeRollStopped = true;
    if (cubeRollFrame !== null) cancelAnimationFrame(cubeRollFrame);
    if (cubeRollWakeTimer !== null) clearTimeout(cubeRollWakeTimer);
    if (resizeRefreshFrame !== null) cancelAnimationFrame(resizeRefreshFrame);
    if (riskGridObserverRetry) clearTimeout(riskGridObserverRetry);
    if (plotlyThemeTimer) clearTimeout(plotlyThemeTimer);
    if (uiHookTimer) clearTimeout(uiHookTimer);
    if (cubeHookTimer) clearTimeout(cubeHookTimer);
    clearInterval(refreshProgressPoll);
    riskGridObservers.forEach((observer) => observer.disconnect());
    riskGridObservers.clear();
    uiHookObserver.disconnect();
  });
  window.addEventListener("pageshow", (event) => {
    if (!event.persisted) return;
    cubeRollStates.forEach((state) => {
      state.lastTimestamp = null;
      state.lastPaintTimestamp = null;
    });
    registerCubeRollers();
    connectRiskGridObservers();
    syncUiHooks();
  });

  document.addEventListener("DOMContentLoaded", () => {
    activeTheme = applyTheme(activeTheme);
    registerCubeRollers();
    connectRiskGridObservers();
    syncUiHooks();
  });
  window.addEventListener("load", () => {
    connectRiskGridObservers();
    syncUiHooks();
  });
  window.addEventListener("resize", () => {
    refreshCubeRollSizes();
  });

})();
