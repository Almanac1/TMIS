(() => {
  "use strict";

  const analyticsNode = document.getElementById("earnings-analytics-data");
  const positionNode = document.getElementById("earnings-position-data");
  if (!analyticsNode || !positionNode || !window.Chart) return;

  const data = JSON.parse(analyticsNode.textContent);
  const position = JSON.parse(positionNode.textContent);
  const rootStyles = getComputedStyle(document.documentElement);
  const cssColor = (property, fallback) =>
    rootStyles.getPropertyValue(property).trim() || fallback;

  const colors = {
    plum: cssColor("--color-brand-primary", "#54285F"),
    green: cssColor("--color-success", "#249653"),
    amber: cssColor("--color-warning", "#D97706"),
    blue: "#536D8D",
    neutral: "#ECE9EF",
    grid: "rgba(148, 163, 184, 0.18)",
    text: "#697083",
    tooltip: "rgba(20, 24, 36, 0.94)",
  };

  const money = (value) =>
    `₦${Number(value || 0).toLocaleString(undefined, {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    })}`;

  const darkenHex = (hex, amount = 0.12) => {
    const value = hex.replace("#", "");
    if (!/^[0-9a-f]{6}$/i.test(value)) return hex;
    const channel = (offset) =>
      Math.max(0, Math.round(parseInt(value.slice(offset, offset + 2), 16) * (1 - amount)))
        .toString(16)
        .padStart(2, "0");
    return `#${channel(0)}${channel(2)}${channel(4)}`;
  };

  const legend = {
    position: "bottom",
    labels: {
      color: colors.text,
      boxWidth: 10,
      boxHeight: 10,
      padding: 18,
      usePointStyle: true,
      pointStyle: "circle",
      font: { size: 12, weight: "600" },
    },
  };

  const tooltip = {
    displayColors: true,
    boxWidth: 9,
    boxHeight: 9,
    boxPadding: 5,
    backgroundColor: colors.tooltip,
    titleColor: "#FFFFFF",
    bodyColor: "#FFFFFF",
    padding: 10,
    cornerRadius: 6,
    caretSize: 5,
    titleMarginBottom: 4,
    bodySpacing: 2,
    titleFont: { size: 13, weight: "600" },
    bodyFont: { size: 12, weight: "600" },
    callbacks: {
      label: (context) => `${context.dataset.label}: ${money(context.raw)}`,
    },
  };

  const valueScale = {
    beginAtZero: true,
    border: { display: false },
    grid: { color: colors.grid, drawBorder: false, tickLength: 0 },
    ticks: {
      color: colors.text,
      padding: 8,
      maxTicksLimit: 6,
      font: { size: 12, weight: "500" },
      callback: (value) => Number(value).toLocaleString(),
    },
  };
  const categoryScale = {
    border: { display: false },
    grid: { display: false },
    ticks: { color: colors.text, padding: 8, font: { size: 12, weight: "600" } },
  };

  const sharedOptions = {
    responsive: true,
    maintainAspectRatio: false,
    animation: { duration: 250 },
    interaction: { mode: "index", intersect: false },
    layout: { padding: { top: 2, right: 2, bottom: 0, left: 0 } },
    plugins: { legend, tooltip },
    scales: { x: categoryScale, y: valueScale },
  };

  const total =
    Number(position.disbursed || 0) +
    Number(position.funded_undisbursed || 0) +
    Number(position.unfunded || 0);

  document.querySelectorAll(".position-bar [data-value]").forEach((segment) => {
    const value = Number(segment.dataset.value) || 0;
    segment.style.width = `${total > 0 ? (value / total) * 100 : 0}%`;
  });

  const fundingCanvas = document.getElementById("fundingCoverageChart");
  if (fundingCanvas) {
    const funded = Math.min(Math.max(Number(position.funded) || 0, 0), total);
    const hasEntitlement = total > 0;
    const hasFunding = hasEntitlement && funded > 0;
    const fundingValues = hasEntitlement ? [funded, Math.max(total - funded, 0)] : [0, 1];

    new Chart(fundingCanvas, {
      type: "doughnut",
      data: {
        labels: ["Funded", "Remaining"],
        datasets: [
          {
            label: "Funding coverage",
            data: fundingValues,
            backgroundColor: [hasFunding ? colors.green : colors.neutral, colors.neutral],
            hoverBackgroundColor: [hasFunding ? darkenHex(colors.green) : colors.neutral, colors.neutral],
            borderWidth: 0,
            hoverOffset: 0,
            spacing: 0,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        cutout: "77%",
        rotation: -90,
        circumference: 360,
        animation: { duration: 250 },
        layout: { padding: 4 },
        plugins: {
          legend: { display: false },
          tooltip: {
            ...tooltip,
            enabled: hasEntitlement,
            filter: (context) => Number(context.raw) > 0,
            callbacks: {
              title: (items) => items[0]?.label || "",
              label: (context) => money(context.raw),
            },
          },
        },
      },
    });
  }

  const trendCanvas = document.getElementById("compensationTrendChart");
  if (trendCanvas) {
    new Chart(trendCanvas, {
      type: "line",
      data: {
        labels: data.monthly.labels,
        datasets: [
          { label: "Accrued", data: data.monthly.accrued, borderColor: colors.plum, backgroundColor: "rgba(84, 40, 95, 0.10)", fill: true, tension: 0.34, borderWidth: 3, pointRadius: 4, pointHoverRadius: 6, pointBackgroundColor: "#FFFFFF", pointBorderColor: colors.plum, pointBorderWidth: 2, pointHoverBackgroundColor: darkenHex(colors.plum), pointHoverBorderColor: darkenHex(colors.plum) },
          { label: "Funded", data: data.monthly.funded, borderColor: colors.green, backgroundColor: colors.green, tension: 0.34, borderWidth: 2.5, pointRadius: 3.5, pointHoverRadius: 5, pointBackgroundColor: "#FFFFFF", pointBorderColor: colors.green, pointBorderWidth: 2, pointHoverBackgroundColor: darkenHex(colors.green), pointHoverBorderColor: darkenHex(colors.green) },
          { label: "Disbursed", data: data.monthly.disbursed, borderColor: colors.blue, backgroundColor: colors.blue, tension: 0.34, borderWidth: 2.5, pointRadius: 3.5, pointHoverRadius: 5, pointBackgroundColor: "#FFFFFF", pointBorderColor: colors.blue, pointBorderWidth: 2, pointHoverBackgroundColor: darkenHex(colors.blue), pointHoverBorderColor: darkenHex(colors.blue) },
        ],
      },
      options: sharedOptions,
    });
  }

  const barOptions = (horizontal = false) => ({
    ...sharedOptions,
    indexAxis: horizontal ? "y" : "x",
    datasets: {
      bar: {
        borderRadius: 1,
        borderSkipped: false,
        maxBarThickness: 22,
        barPercentage: 0.86,
        categoryPercentage: 0.72,
      },
    },
    scales: horizontal
      ? {
          x: valueScale,
          y: {
            ...categoryScale,
            ticks: { ...categoryScale.ticks, autoSkip: false },
          },
        }
      : { x: categoryScale, y: valueScale },
  });

  const governorCanvas = document.getElementById("governorCompensationChart");
  if (governorCanvas) {
    new Chart(governorCanvas, {
      type: "bar",
      data: {
        labels: data.governors.labels,
        datasets: [
          { label: "Due", data: data.governors.due, backgroundColor: colors.plum, hoverBackgroundColor: darkenHex(colors.plum) },
          { label: "Funded", data: data.governors.funded, backgroundColor: colors.green, hoverBackgroundColor: darkenHex(colors.green) },
          { label: "Disbursed", data: data.governors.disbursed, backgroundColor: colors.blue, hoverBackgroundColor: darkenHex(colors.blue) },
        ],
      },
      options: barOptions(),
    });
  }

  const agingCanvas = document.getElementById("payoutAgingChart");
  if (agingCanvas) {
    new Chart(agingCanvas, {
      type: "bar",
      data: { labels: data.aging.labels, datasets: [{ label: "Funded, awaiting payout", data: data.aging.values, backgroundColor: colors.green, hoverBackgroundColor: darkenHex(colors.green) }] },
      options: { ...barOptions(), plugins: { ...sharedOptions.plugins, legend: { display: false } } },
    });
  }

  const courseCanvas = document.getElementById("courseCompensationChart");
  if (courseCanvas) {
    new Chart(courseCanvas, {
      type: "bar",
      data: {
        labels: data.courses.labels,
        datasets: [
          { label: "Due", data: data.courses.due, backgroundColor: colors.plum, hoverBackgroundColor: darkenHex(colors.plum) },
          { label: "Funded", data: data.courses.funded, backgroundColor: colors.green, hoverBackgroundColor: darkenHex(colors.green) },
        ],
      },
      options: barOptions(true),
    });
  }
})();
