(function () {
  "use strict";

  document.querySelectorAll("[data-checkin-expand]").forEach((button) => {
    const initialLabel = button.innerHTML;
    button.addEventListener("click", () => {
      const expanded = button.getAttribute("aria-expanded") === "true";
      document.querySelectorAll(".checkin-extra").forEach((row) => {
        row.classList.toggle("d-none", expanded);
      });
      button.setAttribute("aria-expanded", String(!expanded));
      button.innerHTML = expanded
        ? initialLabel
        : 'Show fewer check-ins <i class="bi bi-arrow-up"></i>';
    });
  });

  const payloadNode = document.getElementById("home-dashboard-charts");
  if (!payloadNode || !window.Chart) return;

  const payload = JSON.parse(payloadNode.textContent);
  const numberSeries = (values) =>
    Array.isArray(values) ? values.map((value) => Number(value) || 0) : [];

  const rootStyles = getComputedStyle(document.documentElement);
  const cssColor = (property, fallback) =>
    rootStyles.getPropertyValue(property).trim() || fallback;
  const brandPrimary = cssColor("--color-brand-primary", "#54285F");
  const brandPrimaryHover = cssColor("--brand-primary-hover", "#43204C");
  const brandPrimaryRgb = cssColor("--color-brand-primary-rgb", "84, 40, 95");
  const success = cssColor("--color-success", "#249653");
  const successRgb = cssColor("--color-success-rgb", "36, 150, 83");
  const warning = cssColor("--color-warning", "#D97706");
  const danger = cssColor("--color-danger", "#C2413B");
  const neutral = cssColor("--color-neutral", "#94A3B8");
  const neutralRgb = cssColor("--color-neutral-rgb", "148, 163, 184");

  const colors = {
    primary: brandPrimary,
    primaryHover: brandPrimaryHover,
    primaryFill: `rgba(${brandPrimaryRgb}, 0.13)`,
    success,
    successFill: `rgba(${successRgb}, 0.08)`,
    warning,
    danger,
    neutral,
    grid: `rgba(${neutralRgb}, 0.22)`,
    text: "#38425b",
  };

  const axisLabelFont = {
    family: "system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
    size: 12,
    weight: "500",
  };

  // Enrollment categories use stable, muted colours keyed by course name. This
  // is intentionally separate from the semantic palette used by finance charts.
  const enrollmentPalette = [
    "#54285F",
    "#76507E",
    "#536D8D",
    "#4F7C78",
    "#4F805D",
    "#B27A32",
    "#9A5F70",
    "#68718F",
    "#6B5B7B",
    "#547381",
    "#667957",
    "#8B6847",
    "#815F78",
    "#596987",
  ];
  const courseColors = {
    "tm - adult": "#54285F",
    "tm - couple": "#76507E",
    "tm - student": "#536D8D",
    "advanced technique": "#4F7C78",
    "tm-sidhi course": "#4F805D",
    "tm - family": "#B27A32",
    "tm - word of wisdom": "#9A5F70",
    "tm introductory program": "#68718F",
    "advanced technique - couple": "#6B5B7B",
    "advanced technique 1": "#547381",
    "advanced technique 2": "#667957",
    "advanced technique 3": "#8B6847",
    "knowledge courses": "#815F78",
    sidhi: "#596987",
    unassigned: colors.neutral,
  };
  const normalizeCourseName = (name) =>
    String(name || "Unassigned").trim().replace(/\s+/g, " ").toLowerCase();
  const stableStringHash = (value) => {
    let hash = 0;
    for (let index = 0; index < value.length; index += 1) {
      hash = (hash * 31 + value.charCodeAt(index)) >>> 0;
    }
    return hash;
  };
  const enrollmentColorFor = (courseName) => {
    const normalizedName = normalizeCourseName(courseName);
    return (
      courseColors[normalizedName] ||
      enrollmentPalette[stableStringHash(normalizedName) % enrollmentPalette.length]
    );
  };
  const darkenHex = (hex, amount = 0.12) => {
    const value = hex.replace("#", "");
    if (!/^[0-9a-f]{6}$/i.test(value)) return hex;
    const channel = (offset) =>
      Math.max(0, Math.round(parseInt(value.slice(offset, offset + 2), 16) * (1 - amount)))
        .toString(16)
        .padStart(2, "0");
    return `#${channel(0)}${channel(2)}${channel(4)}`;
  };

  const enrollmentValueLabels = {
    id: "enrollmentValueLabels",
    afterDatasetsDraw(chart) {
      const context = chart.ctx;
      context.save();
      context.fillStyle = colors.text;
      context.font = "700 13px system-ui, -apple-system, sans-serif";
      context.textBaseline = "middle";
      chart.getDatasetMeta(0).data.forEach((bar, index) => {
        const value = chart.data.datasets[0].data[index];
        context.fillText(Number(value).toLocaleString(), bar.x + 10, bar.y);
      });
      context.restore();
    },
  };

  const sharedLegend = {
    position: "bottom",
    labels: {
      boxWidth: 10,
      boxHeight: 10,
      padding: 18,
      usePointStyle: true,
      color: colors.text,
      font: { size: 12, weight: "600" },
    },
  };

  const enrollmentCanvas = document.getElementById("enrollmentCourseChart");
  if (enrollmentCanvas) {
    const enrollmentLabels = payload.enrollments_by_course?.labels || [];
    const enrollmentColors = enrollmentLabels.map(enrollmentColorFor);
    new Chart(enrollmentCanvas, {
      type: "bar",
      plugins: [enrollmentValueLabels],
      data: {
        labels: enrollmentLabels,
        datasets: [
          {
            label: "Enrollments",
            data: numberSeries(payload.enrollments_by_course?.values),
            backgroundColor: enrollmentColors,
            hoverBackgroundColor: enrollmentColors.map((color) => darkenHex(color)),
            borderRadius: 1,
            borderSkipped: false,
            barThickness: 20,
            maxBarThickness: 22,
            barPercentage: 0.86,
            categoryPercentage: 0.72,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        indexAxis: "y",
        layout: { padding: { top: 2, right: 58, bottom: 0, left: 0 } },
        animation: { duration: 250 },
        plugins: {
          legend: { display: false },
          tooltip: {
            displayColors: false,
            backgroundColor: "rgba(20, 24, 36, 0.94)",
            titleColor: "#ffffff",
            bodyColor: "#ffffff",
            padding: 10,
            cornerRadius: 6,
            caretSize: 5,
            titleMarginBottom: 4,
            bodySpacing: 2,
            titleFont: { size: 13, weight: "600" },
            bodyFont: { size: 12, weight: "600" },
            callbacks: {
              title(items) {
                return items[0]?.label || "";
              },
              label(context) {
                const value = Number(context.raw) || 0;
                return `${value.toLocaleString()} ${value === 1 ? "enrollment" : "enrollments"}`;
              },
            },
          },
        },
        scales: {
          x: {
            beginAtZero: true,
            grace: "8%",
            border: { display: false },
            grid: {
              color: `rgba(${neutralRgb}, 0.14)`,
              drawBorder: false,
              tickLength: 0,
            },
            ticks: {
              color: colors.text,
              font: axisLabelFont,
              maxTicksLimit: 6,
              precision: 0,
              padding: 8,
            },
          },
          y: {
            grid: { display: false },
            border: { display: false },
            afterFit(axis) {
              axis.width = Math.min(230, axis.chart.width * 0.38);
            },
            ticks: {
              align: "start",
              crossAlign: "far",
              color: colors.text,
              font: { ...axisLabelFont, size: 15, weight: "600", lineHeight: 1.25 },
              padding: 12,
            },
          },
        },
      },
    });
  }

  const revenueCanvas = document.getElementById("revenueTrendChart");
  if (revenueCanvas) {
    new Chart(revenueCanvas, {
      type: "line",
      data: {
        labels: payload.revenue_trend?.labels || [],
        datasets: [
          {
            label: "Invoiced",
            data: numberSeries(payload.revenue_trend?.invoiced),
            borderColor: colors.primary,
            backgroundColor: colors.primaryFill,
            borderWidth: 3,
            pointRadius: 4,
            pointHoverRadius: 6,
            pointBackgroundColor: "#fff",
            pointBorderColor: colors.primary,
            pointBorderWidth: 2,
            pointHoverBackgroundColor: colors.primaryHover,
            pointHoverBorderColor: colors.primaryHover,
            fill: true,
            tension: 0.34,
          },
          {
            label: "Received",
            data: numberSeries(payload.revenue_trend?.received),
            borderColor: colors.success,
            backgroundColor: colors.successFill,
            borderWidth: 2.5,
            pointRadius: 3.5,
            pointHoverRadius: 5,
            pointBackgroundColor: "#fff",
            pointBorderColor: colors.success,
            pointBorderWidth: 2,
            fill: false,
            tension: 0.34,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: { duration: 250 },
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: sharedLegend,
          tooltip: {
            callbacks: {
              label(context) {
                return `${context.dataset.label}: ₦${Number(context.parsed.y).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
              },
            },
          },
        },
        scales: {
          x: {
            grid: { display: false },
            ticks: { color: colors.text, font: axisLabelFont, padding: 8 },
          },
          y: {
            beginAtZero: true,
            grid: { color: colors.grid, drawBorder: false },
            ticks: {
              color: colors.text,
              font: axisLabelFont,
              padding: 8,
              callback(value) {
                return Number(value).toLocaleString();
              },
            },
          },
        },
      },
    });
  }
})();
