const byId = (id) => document.getElementById(id);
const MODEL_HORIZONS = [15, 30, 45, 60, 120, 180, 240, 480];
let latestDiscovery = null;

const CONTEXT_LABELS = {
  total_crypto_market_cap: "إجمالي القيمة السوقية للكريبتو",
  total_crypto_market_return: "تغير سوق الكريبتو",
  xrp_market_cap: "القيمة السوقية لـXRP",
  xrp_market_cap_share: "حصة XRP من السوق",
  xrp_global_volume_share: "حصة تداول XRP من السوق",
  xrp_turnover_volume_to_market_cap: "حجم XRP ÷ قيمته السوقية",
};

function number(value, digits = 0) {
  const parsed = Number(value);
  return Number.isFinite(parsed)
    ? parsed.toLocaleString("ar-SA", {
        minimumFractionDigits: digits,
        maximumFractionDigits: digits,
      })
    : "—";
}

function percent(value, digits = 2) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? `${(parsed * 100).toFixed(digits)}%` : "—";
}

function signedPercent(value, digits = 2) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return "—";
  const sign = parsed > 0 ? "+" : "";
  return `${sign}${(parsed * 100).toFixed(digits)}%`;
}

function price(value) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed.toFixed(4) : "—";
}

function dateTime(value) {
  return value ? new Date(Number(value)).toLocaleString("ar-SA") : "—";
}

function duration(minutes) {
  const parsed = Number(minutes);
  if (!Number.isFinite(parsed)) return "—";
  if (parsed < 60) return `${number(parsed, 0)} دقيقة`;
  if (parsed < 1_440) return `${number(parsed / 60, parsed % 60 ? 1 : 0)} ساعة`;
  return `${number(parsed / 1_440, parsed % 1_440 ? 1 : 0)} يوم`;
}

function horizonLabel(minutes) {
  const parsed = Number(minutes);
  if (parsed < 60) return `${parsed} دقيقة`;
  if (parsed < 1_440) return `${number(parsed / 60, parsed % 60 ? 1 : 0)} ساعة`;
  return `${number(parsed / 1_440, parsed % 1_440 ? 1 : 0)} يوم`;
}

function factor(label, value, className = "") {
  return `<div class="factor"><span>${label}</span><strong class="${className}">${value}</strong></div>`;
}

function passageFactors(statistics, direction) {
  const signClass = direction === "UP" ? "positive" : "negative";
  return [
    factor("عدد مرات الوصول", number(statistics.observed_events), signClass),
    factor("نسبة الوصول خلال 14 يومًا", percent(statistics.hit_rate), signClass),
    factor("الوسط الحسابي المشروط", duration(statistics.conditional_mean_minutes)),
    factor("الوسيط", duration(statistics.median_minutes)),
    factor("المنوال", duration(statistics.mode_minutes)),
    factor("تكرار المنوال", number(statistics.mode_frequency)),
    factor("الانحراف المعياري", duration(statistics.standard_deviation_minutes)),
    factor("الربع الثالث P75", duration(statistics.p75_minutes)),
    factor("P90", duration(statistics.p90_minutes)),
    factor("P95", duration(statistics.p95_minutes)),
    factor("المتوسط المقيد مع الحالات غير الواصلة", duration(statistics.restricted_mean_minutes)),
  ].join("");
}

function renderReturnDistribution(distribution) {
  if (!distribution || distribution.status !== "READY") {
    byId("returnDistributionStats").innerHTML = factor("الحالة", "WAIT");
    return;
  }
  const threeSigma = distribution.absolute_sigma_tail_rates?.["3"] || {};
  byId("returnDistributionStats").innerHTML = [
    factor("عدد عوائد الدقيقة", number(distribution.observations)),
    factor("متوسط العائد اللوغاريتمي", signedPercent(distribution.mean_log_return, 5)),
    factor("الانحراف المعياري", percent(distribution.standard_deviation, 4)),
    factor("الالتواء", number(distribution.skewness, 3)),
    factor("التفرطح الزائد", number(distribution.excess_kurtosis, 2)),
    factor("معدل القيم خارج ±3σ", percent(threeSigma.observed_rate, 4)),
    factor("مرجع التوزيع الطبيعي ±3σ", percent(threeSigma.normal_reference_rate, 4)),
    factor("تضاعف الذيل عن الطبيعي", `${number(threeSigma.observed_to_normal_ratio, 1)}×`),
    factor(
      "القيم الشاذة Robust Z≥6",
      number(distribution.robust_outliers?.absolute_robust_z_ge_6_count),
    ),
  ].join("");
}

function renderExternalContext(status) {
  if (!status || typeof status !== "object") {
    byId("externalContextStatus").innerHTML = factor("الحالة", "غير متاح");
    return;
  }
  const rows = Object.entries(CONTEXT_LABELS).map(([key, label]) =>
    factor(label, status[key] === "NOT_COLLECTED" ? "لم يُجمع بعد" : String(status[key] || "—")),
  );
  rows.push(factor("السياسة", "لا تدخل هذه العوامل التدريب قبل توفر تاريخ Point-in-Time حقيقي"));
  byId("externalContextStatus").innerHTML = rows.join("");
}

function renderHorizonTable(horizons) {
  const entries = Object.entries(horizons || {})
    .map(([key, value]) => [Number(key), value])
    .filter(([key]) => Number.isFinite(key))
    .sort((a, b) => a[0] - b[0]);
  if (!entries.length) {
    byId("discoveryHorizonBody").innerHTML =
      '<tr><td colspan="8" class="empty">لا توجد نوافذ محللة</td></tr>';
    return;
  }
  byId("discoveryHorizonBody").innerHTML = entries
    .map(([minutes, row]) => {
      const excursion = row.empirical_excursion || {};
      return `<tr>
        <td>${horizonLabel(minutes)}</td>
        <td class="positive">${number(row.upper_10_reached_count)} (${percent(row.upper_10_reached_rate)})</td>
        <td class="negative">${number(row.lower_10_reached_count)} (${percent(row.lower_10_reached_rate)})</td>
        <td>${percent(row.any_10pct_touch_rate)}</td>
        <td>${number(row.upper_independent_clusters)} / ${number(row.lower_independent_clusters)}</td>
        <td class="positive">${signedPercent(excursion.max_return_q50)}</td>
        <td class="negative">${signedPercent(excursion.min_return_q50)}</td>
        <td>${number(row.sample_rows)}</td>
      </tr>`;
    })
    .join("");
}

function enrichShockWaitCards(payload) {
  const cards = [...byId("shockHorizonGrid").querySelectorAll(".horizon-model-card")];
  cards.forEach((card, index) => {
    const horizon = MODEL_HORIZONS[index];
    const row = payload.horizons?.[String(horizon)];
    const gate = card.querySelector("header strong")?.textContent || "";
    if (!row || !gate.includes("WAIT")) return;
    const excursion = row.empirical_excursion || {};
    const list = card.querySelector("dl");
    if (!list) return;
    list.innerHTML = `
      <div><dt>الارتفاع التاريخي الوسيط</dt><dd class="positive">${signedPercent(excursion.max_return_q50)}</dd></div>
      <div><dt>الهبوط التاريخي الوسيط</dt><dd class="negative">${signedPercent(excursion.min_return_q50)}</dd></div>
      <div><dt>نطاق الصعود 5–95%</dt><dd>${signedPercent(excursion.max_return_q05)} → ${signedPercent(excursion.max_return_q95)}</dd></div>
      <div><dt>نطاق الهبوط 5–95%</dt><dd>${signedPercent(excursion.min_return_q05)} → ${signedPercent(excursion.min_return_q95)}</dd></div>
      <div><dt>العينة التاريخية</dt><dd>${number(excursion.sample_rows)}</dd></div>
      <div><dt>الحكم</dt><dd>وصفي فقط — ليس توقعًا حيًا</dd></div>`;
    card.dataset.discoveryEnriched = "true";
  });
}

function enrichTouchWaitCards(payload) {
  const cards = [...byId("touchHorizonGrid").querySelectorAll(".horizon-model-card")];
  cards.forEach((card, index) => {
    const horizon = MODEL_HORIZONS[index];
    const row = payload.horizons?.[String(horizon)];
    const gate = card.querySelector("header strong")?.textContent || "";
    if (!row || !gate.includes("WAIT")) return;
    const list = card.querySelector("dl");
    if (!list) return;
    list.innerHTML = `
      <div><dt>وصل +10% / −10%</dt><dd>${number(row.upper_10_reached_count)} / ${number(row.lower_10_reached_count)}</dd></div>
      <div><dt>أول وصول صعود / هبوط</dt><dd>${number(row.up_first_count)} / ${number(row.down_first_count)}</dd></div>
      <div><dt>صدمات مستقلة + / −</dt><dd>${number(row.upper_independent_clusters)} / ${number(row.lower_independent_clusters)}</dd></div>
      <div><dt>نسبة أي لمس</dt><dd>${percent(row.any_10pct_touch_rate)}</dd></div>
      <div><dt>العينة التاريخية</dt><dd>${number(row.sample_rows)}</dd></div>
      <div><dt>الحكم</dt><dd>WAIT حتى تنجح بوابة الأداء خارج العينة</dd></div>`;
    card.dataset.discoveryEnriched = "true";
  });
}

function enrichModelCards(payload) {
  if (!payload || payload.status !== "READY") return;
  enrichShockWaitCards(payload);
  enrichTouchWaitCards(payload);
}

function installGridObservers() {
  for (const id of ["shockHorizonGrid", "touchHorizonGrid"]) {
    const grid = byId(id);
    const observer = new MutationObserver(() => enrichModelCards(latestDiscovery));
    observer.observe(grid, { childList: true });
  }
}

function renderDiscovery(payload) {
  const ready = payload.status === "READY";
  byId("discoveryState").textContent = ready ? "READY — EMPIRICAL" : payload.status || "WAIT";
  byId("discoveryState").className = ready ? "" : "wait";
  byId("discoveryUpdated").textContent = ready
    ? `آخر تحليل: ${dateTime(payload.generated_at_ms)}`
    : payload.reason || "لم يُنشأ التقرير بعد";
  if (!ready) return;

  latestDiscovery = payload;
  byId("discoveryAnchors").textContent = number(payload.valid_anchor_count);
  byId("discoveryMaxHorizon").textContent = horizonLabel(payload.max_horizon_minutes);
  byId("discoveryStride").textContent = `${number(payload.anchor_stride_minutes)} دقيقة`;
  byId("discoveryLatestPrice").textContent = price(payload.data?.latest_price);

  const barrier = payload.barrier_time_statistics || {};
  byId("upPassageStats").innerHTML = passageFactors(barrier.UP_10 || {}, "UP");
  byId("downPassageStats").innerHTML = passageFactors(barrier.DOWN_10 || {}, "DOWN");
  renderReturnDistribution(payload.return_distribution || {});
  renderExternalContext(payload.external_context_feature_status || {});
  renderHorizonTable(payload.horizons || {});
  enrichModelCards(payload);
}

async function refreshLatestMarket() {
  try {
    const response = await fetch("/api/market/latest", { cache: "no-store" });
    if (!response.ok) return;
    const payload = await response.json();
    if (payload.status !== "READY") return;
    byId("xrpPrice").textContent = price(payload.price);
    byId("xrpReference").textContent = `شمعة مكتملة: ${dateTime(payload.timestamp_ms)}`;
  } catch (error) {
    // Main runtime status remains authoritative; a transient market read is non-fatal.
  }
}

async function refreshDiscovery() {
  try {
    const response = await fetch("/api/research/first-passage", { cache: "no-store" });
    if (!response.ok) throw new Error("discovery API unavailable");
    renderDiscovery(await response.json());
  } catch (error) {
    byId("discoveryState").textContent = "UNAVAILABLE";
    byId("discoveryState").className = "wait";
  }
}

installGridObservers();
refreshLatestMarket();
refreshDiscovery();
setInterval(refreshLatestMarket, 2_000);
setInterval(refreshDiscovery, 15_000);
