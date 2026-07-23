const $ = (id) => document.getElementById(id);
const HORIZONS = [15, 30, 45, 60, 120, 180, 240, 480];
let overview = null;
let currentInputs = null;
let experimentSource = "manual";

function esc(value) {
  return String(value ?? "—")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function number(value, digits = 0) {
  const parsed = Number(value);
  return Number.isFinite(parsed)
    ? parsed.toLocaleString("ar-SA", { minimumFractionDigits: digits, maximumFractionDigits: digits })
    : "—";
}

function pct(value, digits = 1) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? `${(parsed * 100).toFixed(digits)}%` : "—";
}

function signedPct(value, digits = 2) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return "—";
  return `${parsed > 0 ? "+" : ""}${(parsed * 100).toFixed(digits)}%`;
}

function dateTime(value) {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? new Date(parsed).toLocaleString("ar-SA") : "—";
}

function horizonLabel(minutes) {
  const value = Number(minutes);
  if (value < 60) return `${value} دقيقة`;
  if (value < 1440) return `${number(value / 60, value % 60 ? 1 : 0)} ساعة`;
  return `${number(value / 1440, value % 1440 ? 1 : 0)} يوم`;
}

function duration(minutes) {
  return horizonLabel(Number(minutes));
}

function displayValue(value) {
  if (value === null || value === undefined) return "—";
  if (typeof value === "boolean") return value ? "نعم" : "لا";
  if (Array.isArray(value)) return value.length ? value.map((item) => esc(item)).join("، ") : "—";
  if (typeof value === "object") return esc(JSON.stringify(value));
  if (typeof value === "number") return number(value, Math.abs(value) < 1 ? 5 : 2);
  return esc(value);
}

function stateClass(state) {
  return ["RESEARCH_READY", "RESEARCH_ONLY", "READY", "PASS", "CURRENT"].includes(state)
    ? "ready"
    : "";
}

function detailRows(entries) {
  return entries
    .map(([label, value, className = ""]) => `<div class="detail-row"><span>${esc(label)}</span><strong class="${className}">${displayValue(value)}</strong></div>`)
    .join("");
}

function setConnection(ok, text) {
  $("labConnectionDot").className = `dot ${ok ? "live" : "error"}`;
  $("labConnection").textContent = text;
}

function installTabs() {
  document.querySelectorAll(".lab-tabs button").forEach((button) => {
    button.addEventListener("click", () => {
      document.querySelectorAll(".lab-tabs button").forEach((item) => item.classList.remove("active"));
      document.querySelectorAll(".lab-tab").forEach((item) => item.classList.remove("active"));
      button.classList.add("active");
      $(`tab-${button.dataset.tab}`).classList.add("active");
    });
  });
}

function modelCard(model, touch = false) {
  const available = model.available_horizons || [];
  const waiting = model.waiting_horizons || [];
  return `<article class="model-lab-card ${touch ? "touch" : ""}">
    <header><div><span class="model-number">${touch ? "MODEL B" : "MODEL A"}</span><h2>${esc(model.display_name)}</h2></div><span class="state-pill ${stateClass(model.state)}">${esc(model.state)}</span></header>
    <p>${touch ? "احتمالات الوصول الأول إلى +10% أو −10% أو عدم وقوع الحدث." : "توزيعات كمية لأقصى صعود وأدنى هبوط داخل كل أفق."}</p>
    <div class="model-summary-grid">
      <div><span>نسخة النموذج</span><strong>${esc(model.model_version || "لا يوجد Champion")}</strong></div>
      <div><span>آخر تدريب</span><strong>${dateTime(model.trained_at_ms)}</strong></div>
      <div><span>صفوف التدريب</span><strong>${number(model.training_final_rows)}</strong></div>
      <div><span>الآفاق المتاحة</span><strong>${available.length ? available.map(horizonLabel).join("، ") : "لا يوجد"}</strong></div>
      <div><span>الآفاق المنتظرة</span><strong>${waiting.length ? waiting.map(horizonLabel).join("، ") : "لا يوجد"}</strong></div>
      <div><span>عدد الخصائص</span><strong>${number(model.feature_names?.length)}</strong></div>
    </div>
  </article>`;
}

function renderOverview(payload) {
  const platform = payload.platform || {};
  $("overviewPlatformState").textContent = platform.state || "WAIT";
  $("overviewPlatformState").className = stateClass(platform.state);
  $("overviewPlatformReason").textContent = platform.reason || "—";
  $("overviewLifecycle").textContent = platform.lifecycle_stage || "—";
  $("overviewProgress").textContent = pct(platform.lifecycle_progress || 0);
  $("overviewPriceRows").textContent = number(platform.price_store?.total_rows);
  $("overviewDateRange").textContent = `${dateTime(platform.data_start_ms)} ← ${dateTime(platform.data_end_ms)}`;
  $("modelOverviewGrid").innerHTML = [
    modelCard(payload.models?.adaptive_shock || {}, false),
    modelCard(payload.models?.first_touch || {}, true),
  ].join("");
  const policy = payload.laboratory_policy || {};
  $("labPolicy").innerHTML = [
    ["تسجيل التوقع", policy.predictions_are_persisted ? "يُسجل" : "لا يُسجل"],
    ["تغيير Champion", policy.champion_is_modified ? "مسموح" : "غير مسموح"],
    ["التداول الآلي", policy.automatic_trading ? "مفعل" : "غير مفعل"],
    ["المدخلات اليدوية", policy.manual_inputs_are_hypothetical ? "سيناريو افتراضي" : "بيانات حية"],
    ["تعبئة السوق", policy.current_market_inputs_use_last_completed_feature_row ? "آخر صف سببي مكتمل" : "غير متاح"],
  ].map(([label, value]) => `<div><span>${label}</span><strong>${value}</strong></div>`).join("");
}

function algorithmRows(model) {
  const algorithm = model.algorithm || {};
  const validation = algorithm.validation || {};
  const hyper = algorithm.hyperparameters || {};
  return [
    ["العائلة", algorithm.family],
    ["المقدّر", algorithm.estimator],
    ["الأهداف / الفئات", algorithm.targets || algorithm.classes],
    ["خط المعالجة", algorithm.pipeline],
    ["Hyperparameters", hyper],
    ["منهج التحقق", validation.method],
    ["Purging", validation.purging || "مطبق داخل Walk-Forward"],
    ["Embargo", validation.embargo || "أفق التسمية الكامل"],
    ["بوابة الترقية", validation.promotion_gate || validation.required_directional_precision],
    ["ملاحظة أهمية الخصائص", algorithm.importance_note],
  ];
}

function renderAlgorithms(payload) {
  $("algorithmA").innerHTML = detailRows(algorithmRows(payload.models?.adaptive_shock || {}));
  $("algorithmB").innerHTML = detailRows(algorithmRows(payload.models?.first_touch || {}));
}

function modelUsage(name) {
  const requirements = currentInputs?.model_requirements || {};
  const used = [];
  if ((requirements.adaptive_shock || []).includes(name)) used.push("A");
  if ((requirements.first_touch || []).includes(name)) used.push("B");
  return used.length ? used.join(" + ") : "مسجل فقط";
}

function renderFeatureInventory(filter = "") {
  const rows = currentInputs?.features || [];
  const needle = filter.trim().toLowerCase();
  const selected = rows.filter((item) => !needle || String(item.name).toLowerCase().includes(needle));
  $("featureInventoryBody").innerHTML = selected.length
    ? selected.map((item) => `<tr>
      <td><code>${esc(item.name)}</code></td><td>${number(item.latest, 6)}</td><td>${number(item.p05, 6)}</td><td>${number(item.median, 6)}</td><td>${number(item.p95, 6)}</td><td>${pct(item.missing_rate, 2)}</td><td>${modelUsage(item.name)}</td>
    </tr>`).join("")
    : '<tr><td colspan="7" class="empty">لا توجد خصائص مطابقة</td></tr>';
}

function reportEntries(report) {
  return Object.entries(report || {})
    .filter(([key, value]) => Number.isFinite(Number(key)) && value && typeof value === "object")
    .sort((a, b) => Number(a[0]) - Number(b[0]));
}

function renderEvaluationA(model) {
  $("evaluationAState").textContent = model.state || "WAIT";
  $("evaluationAState").className = `state-pill ${stateClass(model.state)}`;
  const entries = reportEntries(model.training_report);
  $("evaluationABody").innerHTML = entries.length ? entries.map(([horizon, row]) => {
    const max = row.metrics?.future_max_return?.test || {};
    const min = row.metrics?.future_min_return?.test || {};
    return `<tr><td>${horizonLabel(horizon)}</td><td class="${stateClass(row.status)}">${esc(row.status)}</td><td>${number(row.rows)}</td><td>${number(row.train_rows)} / ${number(row.validation_rows)} / ${number(row.test_rows)}</td><td>${pct(max.interval_coverage_90)}</td><td>${pct(min.interval_coverage_90)}</td><td>${pct(max.mae_median, 3)}</td><td>${pct(min.mae_median, 3)}</td><td>${esc(row.reason)}</td></tr>`;
  }).join("") : '<tr><td colspan="9" class="empty">لا يوجد تقرير تدريب لـModel A بعد</td></tr>';
}

function renderEvaluationB(model) {
  $("evaluationBState").textContent = model.state || "WAIT";
  $("evaluationBState").className = `state-pill ${stateClass(model.state)}`;
  const entries = reportEntries(model.training_report);
  $("evaluationBBody").innerHTML = entries.length ? entries.map(([horizon, row]) => {
    const metrics = row.metrics || {};
    const counts = row.class_counts || {};
    const support = metrics.walk_forward_support_audit || {};
    const performance = metrics.walk_forward_performance_audit || {};
    return `<tr><td>${horizonLabel(horizon)}</td><td class="${stateClass(row.status)}">${esc(row.status)}</td><td>${number(row.row_count)}</td><td>${number(row.train_rows)} / ${number(row.calibration_rows)} / ${number(row.test_rows)}</td><td>${number(counts.UP_10)} / ${number(counts.DOWN_10)} / ${number(counts.NO_EVENT)}</td><td>${pct(metrics.directional_high_confidence_empirical_precision)}</td><td>${number(metrics.directional_high_confidence_predictions)}</td><td>${number(performance.passing_fold_count ?? support.eligible_fold_count)} / ${number(performance.minimum_passing_folds ?? support.minimum_eligible_folds)}</td><td>${esc(row.reason)}</td></tr>`;
  }).join("") : '<tr><td colspan="9" class="empty">لا يوجد تقرير تدريب لـModel B بعد</td></tr>';
}

function renderTraining(payload) {
  const platform = payload.platform || {};
  $("trainingLifecycle").innerHTML = detailRows([
    ["الحالة", platform.state], ["السبب", platform.reason], ["المرحلة", platform.lifecycle_stage], ["رسالة المرحلة", platform.lifecycle_message], ["التقدم", pct(platform.lifecycle_progress)], ["آخر دورة ناجحة", dateTime(platform.last_successful_cycle_ms)], ["آخر صف نهائي عند التدريب", number(platform.last_training_final_rows)],
  ]);
  $("leakageControls").innerHTML = detailRows([
    ["التقسيم", "زمني فقط — لا خلط عشوائي"], ["Purging", "إزالة الصفوف التي تعبر نتيجتها حدود التقسيم"], ["Embargo", "فاصل يعادل أفق التوقع"], ["الاختبار", "Untouched temporal test"], ["Walk-Forward", "Fresh fit لكل فترة في Model B"], ["تسجيل تجربة المختبر", "غير مسموح"],
  ]);
}

function passageRows(stats) {
  return [
    ["عدد مرات الوصول", number(stats.observed_events)], ["نسبة الوصول", pct(stats.hit_rate, 2)], ["الوسط الحسابي المشروط", duration(stats.conditional_mean_minutes)], ["الوسيط", duration(stats.median_minutes)], ["المنوال", duration(stats.mode_minutes)], ["تكرار المنوال", number(stats.mode_frequency)], ["الانحراف المعياري", duration(stats.standard_deviation_minutes)], ["P75", duration(stats.p75_minutes)], ["P90", duration(stats.p90_minutes)], ["P95", duration(stats.p95_minutes)], ["المتوسط المقيد", duration(stats.restricted_mean_minutes)],
  ];
}

function renderStatistics(payload) {
  const stats = payload.statistical_analysis || {};
  const discovery = stats.first_passage || {};
  const distribution = discovery.return_distribution || {};
  $("statReturnRows").textContent = number(distribution.observations);
  $("statSkewness").textContent = number(distribution.skewness, 4);
  $("statKurtosis").textContent = number(distribution.excess_kurtosis, 3);
  $("statOutliers").textContent = number(distribution.robust_outliers?.absolute_robust_z_ge_6_count);
  $("statUpPassage").innerHTML = detailRows(passageRows(discovery.barrier_time_statistics?.UP_10 || {}));
  $("statDownPassage").innerHTML = detailRows(passageRows(discovery.barrier_time_statistics?.DOWN_10 || {}));
  const horizons = reportEntries(discovery.horizons || {});
  $("statHorizonBody").innerHTML = horizons.length ? horizons.map(([horizon, row]) => {
    const excursion = row.empirical_excursion || {};
    return `<tr><td>${horizonLabel(horizon)}</td><td>${number(row.upper_10_reached_count)} (${pct(row.upper_10_reached_rate, 2)})</td><td>${number(row.lower_10_reached_count)} (${pct(row.lower_10_reached_rate, 2)})</td><td>${pct(row.any_10pct_touch_rate, 2)}</td><td>${number(row.up_first_count)}</td><td>${number(row.down_first_count)}</td><td>${number(row.upper_independent_clusters)} / ${number(row.lower_independent_clusters)}</td><td>${signedPct(excursion.max_return_q50)} / ${signedPct(excursion.min_return_q50)}</td><td>${number(row.sample_rows)}</td></tr>`;
  }).join("") : '<tr><td colspan="9" class="empty">تقرير الاستكشاف غير متاح</td></tr>';
  const integrity = stats.data_integrity || {};
  $("integrityAnalysis").innerHTML = detailRows([
    ["الحالة", integrity.status], ["السبب", integrity.reason], ["التغطية", pct(integrity.coverage_ratio, 3)], ["الدقائق المفقودة", number(integrity.missing_minutes)], ["الصفوف", number(integrity.total_rows)], ["البصمة", integrity.dataset_fingerprint_sha256],
  ]);
  const selection = discovery.window_selection || {};
  $("windowCandidates").innerHTML = detailRows([
    ["الحالة", selection.status], ["مرشحات الكميات", (selection.quantile_aligned_horizons_minutes || []).map(horizonLabel)], ["مرشحات الأدلة", (selection.evidence_supported_horizons_minutes || []).map(horizonLabel)], ["المدد الطويلة المدعومة", (selection.longer_supported_horizons_minutes || []).map(horizonLabel)], ["تعديل الاستراتيجية مطلوب", selection.strategy_revision_required], ["تغيير تلقائي", selection.automatic_model_reconfiguration], ["ملاحظة", selection.note],
  ]);
}

function renderFeatureEditor(filter = "") {
  const features = currentInputs?.features || [];
  const needle = filter.trim().toLowerCase();
  const rows = features.filter((item) => !needle || String(item.name).toLowerCase().includes(needle));
  $("featureEditor").innerHTML = rows.length ? rows.map((item) => `<div class="feature-input-row" data-feature="${esc(item.name)}"><label><code>${esc(item.name)}</code><small>P05 ${number(item.p05, 5)} · Median ${number(item.median, 5)} · P95 ${number(item.p95, 5)}</small></label><input type="number" step="any" value="${item.latest ?? ""}" aria-label="${esc(item.name)}" /></div>`).join("") : '<p class="empty">لا توجد خصائص مطابقة</p>';
  $("featureEditor").querySelectorAll("input").forEach((input) => input.addEventListener("input", () => {
    experimentSource = "manual";
    updateSourceLabel();
    markOutsideRanges();
  }));
  markOutsideRanges();
}

function updateSourceLabel() {
  $("inputSourceLabel").textContent = experimentSource === "current_market" ? "السوق الحالي — آخر صف مكتمل" : "يدوي / معدل";
  $("inputTimestamp").textContent = experimentSource === "current_market" ? dateTime(currentInputs?.timestamp_ms) : "سيناريو افتراضي";
}

function markOutsideRanges() {
  const byName = Object.fromEntries((currentInputs?.features || []).map((item) => [item.name, item]));
  $("featureEditor").querySelectorAll(".feature-input-row").forEach((row) => {
    const name = row.dataset.feature;
    const stats = byName[name] || {};
    const value = Number(row.querySelector("input").value);
    const outside = Number.isFinite(value) && Number.isFinite(Number(stats.p05)) && Number.isFinite(Number(stats.p95)) && (value < Number(stats.p05) || value > Number(stats.p95));
    row.classList.toggle("outside", outside);
  });
}

function collectFeatureValues() {
  const values = {};
  $("featureEditor").querySelectorAll(".feature-input-row").forEach((row) => {
    const input = row.querySelector("input");
    const parsed = Number(input.value);
    values[row.dataset.feature] = input.value === "" || !Number.isFinite(parsed) ? null : parsed;
  });
  return values;
}

function fillCurrent() {
  if (currentInputs?.status !== "READY") return;
  $("experimentPrice").value = currentInputs.anchor_price;
  experimentSource = "current_market";
  renderFeatureEditor($("experimentFeatureSearch").value);
  updateSourceLabel();
}

function updateExperimentModelState() {
  const key = $("experimentModel").value;
  const model = overview?.models?.[key] || {};
  $("experimentModelState").textContent = model.state || "WAIT";
  $("experimentModelState").className = `state-pill ${stateClass(model.state)}`;
  const available = new Set(model.available_horizons || []);
  $("experimentHorizon").innerHTML = HORIZONS.map((horizon) => `<option value="${horizon}">${horizonLabel(horizon)}${available.has(horizon) ? " — READY" : " — WAIT"}</option>`).join("");
}

function renderPrediction(payload) {
  $("responseJson").textContent = JSON.stringify(payload, null, 2);
  const notice = $("experimentNotice");
  notice.textContent = `${payload.status || "WAIT"} — ${payload.reason || payload.decision_reason || "نتيجة بحثية"}`;
  const output = payload.output || {};
  if (payload.model_key === "adaptive_shock" && payload.status === "RESEARCH_RESULT") {
    $("predictionCards").innerHTML = [
      ["صعود Q50", signedPct(output.max_return_q50), "positive"], ["سعر الصعود Q50", number(output.max_price_q50, 4), "positive"], ["هبوط Q50", signedPct(output.min_return_q50), "negative"], ["سعر الهبوط Q50", number(output.min_price_q50, 4), "negative"], ["نطاق الصعود Q05–Q95", `${signedPct(output.max_return_q05)} → ${signedPct(output.max_return_q95)}`, ""], ["نطاق الهبوط Q05–Q95", `${signedPct(output.min_return_q05)} → ${signedPct(output.min_return_q95)}`, ""],
    ].map(([label, value, cls]) => `<div class="prediction-card"><span>${label}</span><strong class="${cls}">${value}</strong></div>`).join("");
  } else if (payload.model_key === "first_touch" && payload.status === "RESEARCH_RESULT") {
    $("predictionCards").innerHTML = [
      ["احتمال +10% أولًا", pct(output.p_up_10, 2), "positive"], ["احتمال −10% أولًا", pct(output.p_down_10, 2), "negative"], ["احتمال لا حدث", pct(output.p_no_event, 2), ""], ["الفئة الأعلى", output.highest_probability_class, ""], ["أعلى احتمال", pct(output.highest_probability, 2), ""], ["الحكم", payload.decision, "wait"],
    ].map(([label, value, cls]) => `<div class="prediction-card"><span>${label}</span><strong class="${cls}">${esc(value)}</strong></div>`).join("");
  } else {
    $("predictionCards").innerHTML = `<div class="prediction-card"><span>الحالة</span><strong class="wait">${esc(payload.status || "WAIT")}</strong></div><div class="prediction-card"><span>السبب</span><strong>${esc(payload.reason || "لا توجد نتيجة")}</strong></div>`;
  }
  const diagnostics = payload.input_diagnostics || {};
  $("inputDiagnostics").innerHTML = detailRows([
    ["خصائص مطلوبة", payload.required_feature_count], ["خصائص مفقودة", diagnostics.missing_feature_count], ["خارج P05–P95", diagnostics.outside_historical_p05_p95_count], ["القيم الخارجة", diagnostics.outside_historical_p05_p95], ["تم حفظ التجربة", payload.persisted], ["مفعلة للتداول", payload.promoted_for_trading],
  ]);
}

async function runExperiment() {
  const request = {
    model_key: $("experimentModel").value,
    horizon_minutes: Number($("experimentHorizon").value),
    input_source: experimentSource,
    anchor_price: Number($("experimentPrice").value) || null,
    feature_values: collectFeatureValues(),
  };
  $("requestJson").textContent = JSON.stringify(request, null, 2);
  $("experimentNotice").textContent = "جاري تشغيل التجربة داخل النموذج الفعلي…";
  const response = await fetch("/api/lab/predict", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(request) });
  const payload = await response.json();
  renderPrediction(payload);
}

function installExperimentEvents() {
  $("experimentModel").addEventListener("change", updateExperimentModelState);
  $("fillCurrentButton").addEventListener("click", fillCurrent);
  $("resetInputsButton").addEventListener("click", fillCurrent);
  $("experimentPrice").addEventListener("input", () => { experimentSource = "manual"; updateSourceLabel(); });
  $("experimentFeatureSearch").addEventListener("input", (event) => renderFeatureEditor(event.target.value));
  $("featureSearch").addEventListener("input", (event) => renderFeatureInventory(event.target.value));
  $("runPredictionButton").addEventListener("click", () => runExperiment().catch((error) => {
    $("experimentNotice").textContent = `تعذر تشغيل التجربة: ${error.message}`;
  }));
  $("refreshLabButton").addEventListener("click", refreshLab);
}

function renderAll(payload, inputs) {
  overview = payload;
  currentInputs = inputs;
  renderOverview(payload);
  renderAlgorithms(payload);
  renderFeatureInventory();
  renderEvaluationA(payload.models?.adaptive_shock || {});
  renderEvaluationB(payload.models?.first_touch || {});
  renderTraining(payload);
  renderStatistics(payload);
  updateExperimentModelState();
  fillCurrent();
  $("rawOverviewJson").textContent = JSON.stringify(payload, null, 2);
  $("rawInputsJson").textContent = JSON.stringify(inputs, null, 2);
  $("labUpdated").textContent = `آخر قراءة: ${dateTime(payload.generated_at_ms)}`;
}

async function refreshLab() {
  setConnection(false, "جاري تحديث أدلة المختبر…");
  const [overviewResponse, inputsResponse] = await Promise.all([
    fetch("/api/lab/overview", { cache: "no-store" }),
    fetch("/api/lab/current-inputs", { cache: "no-store" }),
  ]);
  if (!overviewResponse.ok || !inputsResponse.ok) throw new Error("تعذر قراءة API المختبر");
  renderAll(await overviewResponse.json(), await inputsResponse.json());
  setConnection(true, "متصل بالمنصة الفعلية");
}

installTabs();
installExperimentEvents();
refreshLab().catch((error) => {
  setConnection(false, "المختبر غير متاح");
  $("labUpdated").textContent = error.message;
});
