const $ = (id) => document.getElementById(id);
const HORIZONS = [15, 30, 45, 60, 120, 180, 240, 480];

const STAGE_LABELS = {
  IDLE: "بانتظار التشغيل",
  MIGRATE_PRICE_STORAGE: "ترحيل البيانات إلى تخزين شهري",
  MIGRATE_HORIZONS: "توسيع السجل التاريخي إلى 8 ساعات",
  BOOTSTRAP_HISTORY: "جمع التاريخ السوقي",
  SYNC_MISSING_TAIL: "استكمال البيانات المفقودة",
  BUILD_ANCHORS: "بناء نقاط التوقع",
  DATA_CHECKPOINTED: "حُفظت البيانات",
  BUILD_FEATURES: "هندسة الخصائص",
  DATA_READY: "البيانات والخصائص جاهزة",
  BUILD_TARGETS_A: "بناء أهداف نموذج A",
  TARGETS_A_READY: "أهداف نموذج A جاهزة",
  TRAIN_MODEL_B: "تدريب نموذج B",
  MODEL_B_WAIT: "بعض آفاق نموذج B في الانتظار",
  MODEL_B_RESEARCH_READY: "اكتمل تقييم آفاق نموذج B",
  TRAIN_MODEL_A: "تدريب نموذج A",
  MODEL_A_WAIT: "بعض آفاق نموذج A في الانتظار",
  MODEL_A_RESEARCH_READY: "اكتمل تقييم آفاق نموذج A",
  PREDICT: "إصدار توقعات نموذج B",
  PREDICTIONS_STORED: "حُفظت توقعات نموذج B",
  PREDICT_MODEL_A: "إصدار توقعات نموذج A",
  MODEL_A_PREDICTIONS_STORED: "حُفظت توقعات نموذج A",
  MATURE_OUTCOMES: "تقييم التوقعات الناضجة",
  OUTCOMES_MATURED: "اكتمل تقييم النتائج الناضجة",
  REPORT: "إعداد تقرير الإنتاج",
  REPORT_READY: "حُفظ تقرير الإنتاج",
  LIVE_IDLE: "مراقبة السوق وانتظار الدقيقة التالية",
  ERROR: "توقف آمن بسبب خطأ",
};

const MESSAGE_LABELS = {
  not_started: "بانتظار بدء دورة التشغيل.",
  migrate_price_storage: "نسخ ملف الأسعار القديم إلى أقسام شهرية دون حذف الأصل.",
  rebuilding_historical_anchors_for_extended_horizons: "إعادة بناء الأهداف التاريخية للآفاق حتى 8 ساعات؛ تُحفظ نسخة احتياطية من الملف السابق.",
  extended_historical_horizons_ready: "اكتمل بناء الآفاق التاريخية الجديدة.",
  collecting_completed_market_candles: "جمع شموع السوق المكتملة وحفظها على دفعات.",
  collect_history: "تجميع البيانات التاريخية مع نقاط حفظ قابلة للاستئناف.",
  build_anchors: "بناء نقاط التوقع والنتائج المؤجلة من الشموع الحقيقية.",
  data_checkpointed: "تم حفظ آخر دفعة وتحديث علامة الاستئناف.",
  building_causal_feature_matrix: "حساب خصائص سببية لا تستخدم المستقبل.",
  real_data_and_features_ready_for_model_gates: "اكتمل تجهيز البيانات والخصائص لبوابات التدريب.",
  building_observed_future_excursion_targets_through_8h: "استخراج أعلى وأدنى حركة مرصودة داخل كل أفق حتى 8 ساعات.",
  model_a_extended_horizon_targets_ready: "اكتملت أهداف Model A لجميع الآفاق الجديدة.",
  training_first_touch_independent_horizon_challengers: "اختبار كل أفق في Model B بصورة مستقلة عبر فترات زمنية محمية.",
  training_future_excursion_independent_horizon_challengers: "اختبار كل أفق في Model A بصورة مستقلة.",
  model_b_all_horizons_wait: "لم يجتز أي أفق في Model B البوابة الحالية.",
  model_b_independent_horizon_gates_evaluated: "اجتازت بعض الآفاق أو كلها بوابات Model B المستقلة.",
  model_a_all_horizons_wait: "لم يجتز أي أفق في Model A البوابة الحالية.",
  model_a_challengers_rejected_existing_horizons_retained: "رُفضت التحديات الجديدة واحتُفظ بالآفاق المعتمدة سابقًا.",
  model_a_independent_horizon_gates_evaluated: "اجتازت بعض الآفاق أو كلها بوابات Model A المستقلة.",
  creating_model_b_predictions_for_available_horizons: "إنشاء احتمالات Model B للآفاق المعتمدة فقط.",
  model_b_available_horizon_predictions_stored: "حُفظت احتمالات Model B للآفاق المعتمدة.",
  creating_future_excursion_predictions_for_available_horizons: "إنشاء نطاقات Model A للآفاق المعتمدة فقط.",
  model_a_available_horizon_predictions_stored: "حُفظت نطاقات Model A للآفاق المعتمدة.",
  maturing_eligible_model_b_predictions: "مطابقة التوقعات التي انتهى أفقها مع المسار الحقيقي.",
  eligible_model_b_outcomes_resolved: "اكتمل تقييم التوقعات المؤهلة.",
  building_extended_horizon_production_report: "حساب الأداء الحي لكل أفق حتى 8 ساعات.",
  extended_horizon_production_report_saved: "تم حفظ تقرير الأداء الممتد.",
  cycle_complete_waiting_for_next_completed_minute: "اكتملت الدورة، والمنصة تنتظر شمعة دقيقة مكتملة جديدة.",
};

const REASON_LABELS = {
  both_model_independent_horizon_gates_pending: "بوابات الآفاق المستقلة للنموذجين ما زالت قيد التحقق",
  dual_models_all_horizons_research_monitoring_only: "كل آفاق النموذجين جاهزة للمراقبة البحثية فقط",
  dual_models_some_independent_horizons_ready_others_wait: "بعض آفاق النموذجين جاهزة، والبقية في الانتظار",
  model_a_some_horizons_ready_model_b_wait: "بعض آفاق Model A جاهزة، ولا يوجد أفق معتمد في Model B",
  model_b_some_horizons_ready_model_a_wait: "بعض آفاق Model B جاهزة، ولا يوجد أفق معتمد في Model A",
  model_b_independent_walk_forward_event_support_wait: "لا توجد صدمات مستقلة كافية في عدة فترات اختبار",
  model_b_walk_forward_split_wait: "تعذر تكوين فترات Walk‑Forward آمنة لهذا الأفق",
  model_b_directional_precision_gate_wait: "الدقة الاتجاهية التجريبية لهذا الأفق أقل من 85%",
  insufficient_independent_directional_events_across_untouched_periods: "لا توجد عناقيد صعود وهبوط مستقلة كافية في فترتين اختبار على الأقل",
  walk_forward_split_unavailable: "البيانات لا تكفي لتكوين تقسيم Walk‑Forward آمن",
  directional_event_evidence_gate_failed: "فشلت بوابة الأدلة الاتجاهية",
  legacy_first_touch_gate_or_horizon_set_invalidated: "أُبطلت النسخة القديمة لأنها لا تشمل مجموعة الآفاق والبوابة الحالية",
  no_directionally_valid_first_touch_horizon: "لا يوجد أفق في Model B اجتاز البوابة",
  no_valid_adaptive_shock_horizon: "لا يوجد أفق في Model A اجتاز بوابة التغطية",
  report_matches_current_independent_horizon_gate: "التقرير مطابق لبوابات الآفاق الحالية",
  report_was_generated_by_an_older_gate_or_training_is_still_running: "التقرير قديم أو أن التدريب الجديد لم يكتمل بعد",
  insufficient_directional_event_test_support: "الاختبار الأخير لا يحتوي حالات اتجاهية كافية",
  insufficient_high_confidence_directional_predictions: "لا توجد توقعات اتجاهية عالية الثقة بعدد كافٍ",
  insufficient_high_confidence_predictions_per_direction: "أحد الاتجاهين لا يملك توقعات عالية الثقة كافية",
  directional_empirical_precision_below_required_85pct: "دقة التوقعات الاتجاهية أقل من 85%",
  no_predictions: "لا توجد توقعات من نسخة معتمدة",
  no_matured_predictions: "التوقعات لم تنضج بعد",
  no_matured_predictions_for_horizon: "لا توجد توقعات ناضجة لهذا الأفق",
  insufficient_matured_predictions_for_horizon: "العينات الحية الناضجة لهذا الأفق غير كافية",
  horizon_interval_coverage_gate_passed: "اجتاز الأفق بوابة التغطية الحية",
  horizon_interval_coverage_below_required_85pct: "تغطية الأفق الحية أقل من 85%",
  some_horizons_ready_others_monitoring_or_drift: "بعض الآفاق جاهزة والبقية تحت المراقبة",
  insufficient_matured_predictions_across_horizons: "العينات الحية لم تنضج بعد عبر الآفاق",
};

function reasonLabel(value) {
  return REASON_LABELS[value] || value || "—";
}

function horizonLabel(value) {
  const labels = {
    15: "15 دقيقة",
    30: "30 دقيقة",
    45: "45 دقيقة",
    60: "60 دقيقة (ساعة)",
    120: "120 دقيقة (ساعتان)",
    180: "180 دقيقة (3 ساعات)",
    240: "240 دقيقة (4 ساعات)",
    480: "480 دقيقة (8 ساعات)",
  };
  return labels[value] || `${value} دقيقة`;
}

function pct(value) {
  return Number.isFinite(value) ? `${(value * 100).toFixed(1)}%` : "—";
}

function price(value) {
  return Number.isFinite(value) ? Number(value).toFixed(4) : "—";
}

function time(value) {
  return value ? new Date(Number(value)).toLocaleString("ar-SA") : "—";
}

function count(value) {
  return Number(value || 0).toLocaleString("ar-SA");
}

function setConnection(mode, text) {
  $("connectionDot").className = `dot ${mode}`;
  $("connectionStatus").textContent = text;
}

function shockWaitCard(horizon, reason = "لم يجتز الأفق بوابة التغطية بعد") {
  return `
    <article class="horizon-model-card wait-card">
      <header><span>${horizonLabel(horizon)}</span><strong>WAIT — GATE</strong></header>
      <dl>
        <div><dt>أعلى حركة متوقعة</dt><dd>—</dd></div>
        <div><dt>أدنى حركة متوقعة</dt><dd>—</dd></div>
        <div><dt>الحالة</dt><dd>${reason}</dd></div>
      </dl>
    </article>`;
}

function waitShockCards() {
  $("shockHorizonGrid").innerHTML = HORIZONS.map((h) => shockWaitCard(h)).join("");
}

function horizonReport(report, horizon) {
  const value = report && report[String(horizon)];
  return value && typeof value === "object" ? value : {};
}

function directionalSupport(report, horizon) {
  const current = horizonReport(report, horizon);
  const metrics = current.metrics || {};
  const explicit = metrics.directional_test_support || {};
  const perClass = metrics.per_class || {};
  const walkForward = metrics.walk_forward_support_audit || {};
  const aggregate = walkForward.aggregate_event_support || {};
  const clusters = walkForward.aggregate_independent_event_clusters || {};
  return {
    up: Number(explicit.UP_10 ?? aggregate.UP_10 ?? perClass.UP_10?.support ?? 0),
    down: Number(explicit.DOWN_10 ?? aggregate.DOWN_10 ?? perClass.DOWN_10?.support ?? 0),
    upClusters: Number(clusters.UP_10 || 0),
    downClusters: Number(clusters.DOWN_10 || 0),
    eligibleFolds: Number(walkForward.eligible_fold_count || 0),
    foldCount: Number(walkForward.fold_count || 0),
    reason: current.reason || report?._meta?.reason || "no_first_touch_training_report",
    status: current.status || report?._meta?.status || "WAIT",
  };
}

function touchWaitCard(horizon, report = {}, platformReason = "") {
  const evidence = directionalSupport(report, horizon);
  const reason = evidence.reason || platformReason;
  return `
    <article class="horizon-model-card wait-card">
      <header><span>${horizonLabel(horizon)}</span><strong>WAIT — INDEPENDENT GATE</strong></header>
      <dl>
        <div><dt>صفوف +10% / −10%</dt><dd>${count(evidence.up)} / ${count(evidence.down)}</dd></div>
        <div><dt>صدمات مستقلة +10% / −10%</dt><dd>${count(evidence.upClusters)} / ${count(evidence.downClusters)}</dd></div>
        <div><dt>الفترات المؤهلة</dt><dd>${count(evidence.eligibleFolds)} من ${count(evidence.foldCount)}</dd></div>
        <div><dt>سبب الانتظار</dt><dd>${reasonLabel(reason)}</dd></div>
      </dl>
    </article>`;
}

function waitTouchCards(report = {}, platformReason = "") {
  $("touchHorizonGrid").innerHTML = HORIZONS.map(
    (h) => touchWaitCard(h, report, platformReason),
  ).join("");
}

function renderStatus(status) {
  const watermark = status.data_end_ms || status.current_watermark_ms;
  const connected = Boolean(watermark);
  const errored = status.lifecycle_stage === "ERROR";
  setConnection(
    errored ? "error" : connected ? "live" : "pending",
    errored ? "توقف آمن — راجع سبب الخطأ" : connected ? "متصل بخادم البيانات الحقيقية" : "بانتظار أول دفعة حقيقية",
  );
  $("lastTick").textContent = connected ? `آخر Watermark: ${time(watermark)}` : "لا توجد بيانات محفوظة بعد";
  $("priceRows").textContent = count(status.price_rows);
  $("dataStart").textContent = time(status.data_start_ms);
  $("platformState").textContent = status.state || "WAIT";
  $("platformState").className = status.state === "WAIT" ? "wait" : "";
  $("platformReason").textContent = reasonLabel(status.reason);

  const progress = Math.min(1, Math.max(0, Number(status.lifecycle_progress || 0)));
  const stage = status.lifecycle_stage || "IDLE";
  const message = status.lifecycle_message || "not_started";
  $("lifecycleTitle").textContent = STAGE_LABELS[stage] || stage;
  $("lifecycleMessage").textContent = MESSAGE_LABELS[message] || message;
  $("lifecyclePercent").textContent = `${Math.round(progress * 100)}%`;
  $("lifecycleProgress").value = progress * 100;
  $("lifecycleProgress").textContent = `${Math.round(progress * 100)}%`;
  $("processedRows").textContent = count(status.processed_rows);
  $("expectedRows").textContent = count(status.expected_rows);
  $("checkpointWrites").textContent = count(status.checkpoint_writes);
  $("currentWatermark").textContent = time(status.current_watermark_ms);
}

function summarizeTouchEvidence(report) {
  return HORIZONS.map((h) => {
    const evidence = directionalSupport(report, h);
    return `${h}د: ${count(evidence.upClusters)}/${count(evidence.downClusters)} صدمات | ${count(evidence.eligibleFolds)}/${count(evidence.foldCount)} فترات`;
  }).join(" | ");
}

function renderCatalog(catalog, touchReport = {}, production = {}) {
  const shock = catalog.adaptive_shock;
  const touch = catalog.first_touch_10;
  const envelopeLive = production.future_envelope || {};
  const reportMeta = touchReport._meta || {};

  $("shockState").textContent = shock.available ? "PARTIAL/READY — RESEARCH" : "WAIT";
  $("shockVersion").textContent = shock.available
    ? `${shock.model_version} | الآفاق: ${(shock.available_horizons || []).join(", ")}`
    : "لا يوجد أفق مدرّب";
  $("touchState").textContent = touch.available ? "PARTIAL/READY — RESEARCH" : "WAIT — INDEPENDENT GATES";
  $("touchVersion").textContent = touch.available
    ? `${touch.model_version} | الآفاق: ${(touch.available_horizons || []).join(", ")}`
    : reasonLabel(touch.availability_reason);

  $("shockMethod").innerHTML = `
    <div class="factor"><span>النوع</span><strong>${shock.technical_name}</strong></div>
    <div class="factor"><span>الغرض</span><strong>${shock.purpose}</strong></div>
    <div class="factor"><span>الآفاق المعتمدة</span><strong>${(shock.available_horizons || []).join(", ") || "لا يوجد"}</strong></div>`;
  $("shockGate").innerHTML = `
    <div class="factor"><span>الاختبار التاريخي</span><strong>${shock.gate}</strong></div>
    <div class="factor"><span>المراقبة الحية</span><strong>${envelopeLive.status || "WAIT"} — ${reasonLabel(envelopeLive.reason)}</strong></div>
    <div class="factor"><span>آفاق تحت الانتظار</span><strong>${(shock.waiting_horizons || []).join(", ") || "لا يوجد"}</strong></div>
    <div class="factor"><span>الترقية للتداول</span><strong>غير مفعلة</strong></div>`;
  $("touchMethod").innerHTML = `
    <div class="factor"><span>النوع</span><strong>${touch.technical_name}</strong></div>
    <div class="factor"><span>الغرض</span><strong>${touch.purpose}</strong></div>
    <div class="factor"><span>الآفاق المعتمدة</span><strong>${(touch.available_horizons || []).join(", ") || "لا يوجد"}</strong></div>`;
  $("touchGate").innerHTML = `
    <div class="factor"><span>الاختبار</span><strong>${touch.gate}</strong></div>
    <div class="factor"><span>حالة التقرير</span><strong>${reportMeta.status || touch.training_report_status || "WAIT"} — ${reasonLabel(reportMeta.reason)}</strong></div>
    <div class="factor"><span>الصدمات المستقلة عبر الفترات</span><strong>${summarizeTouchEvidence(touchReport)}</strong></div>
    <div class="factor"><span>الترقية للتداول</span><strong>غير مفعلة</strong></div>`;
}

function shockCard(row) {
  return `
    <article class="horizon-model-card shock-card">
      <header><span>${horizonLabel(Number(row.horizon_minutes))}</span><strong>${row.empirical_gate || "RESEARCH"}</strong></header>
      <dl>
        <div><dt>أعلى حركة وسطية</dt><dd class="positive">${pct(Number(row.max_return_q50))}</dd></div>
        <div><dt>أدنى حركة وسطية</dt><dd class="negative">${pct(Number(row.min_return_q50))}</dd></div>
        <div><dt>أعلى سعر وسطي</dt><dd>${price(Number(row.max_price_q50))}</dd></div>
        <div><dt>أدنى سعر وسطي</dt><dd>${price(Number(row.min_price_q50))}</dd></div>
        <div><dt>نطاق الصعود 5–95%</dt><dd>${pct(Number(row.max_return_q05))} → ${pct(Number(row.max_return_q95))}</dd></div>
        <div><dt>نطاق الهبوط 5–95%</dt><dd>${pct(Number(row.min_return_q05))} → ${pct(Number(row.min_return_q95))}</dd></div>
      </dl>
    </article>`;
}

function renderShock(rows) {
  const byHorizon = new Map(rows.map((row) => [Number(row.horizon_minutes), row]));
  const latest = rows.length ? [...rows].sort((a, b) => a.horizon_minutes - b.horizon_minutes).at(-1) : null;
  if (latest) {
    $("xrpPrice").textContent = price(Number(latest.anchor_price));
    $("xrpReference").textContent = `مرجع: ${time(latest.anchor_timestamp_ms)}`;
  }
  $("shockHorizonGrid").innerHTML = HORIZONS.map((h) =>
    byHorizon.has(h) ? shockCard(byHorizon.get(h)) : shockWaitCard(h),
  ).join("");
}

function touchCard(row) {
  const values = [Number(row.p_up_10), Number(row.p_down_10), Number(row.p_no_event)];
  const labels = ["UP_10", "DOWN_10", "NO_EVENT"];
  const winner = labels[values.indexOf(Math.max(...values))];
  return `
    <article class="horizon-model-card touch-card">
      <header><span>${horizonLabel(Number(row.horizon_minutes))}</span><strong>${winner}</strong></header>
      <dl>
        <div><dt>+10% أولًا</dt><dd class="positive">${pct(values[0])}</dd></div>
        <div><dt>−10% أولًا</dt><dd class="negative">${pct(values[1])}</dd></div>
        <div><dt>لا حدث</dt><dd>${pct(values[2])}</dd></div>
        <div><dt>قرار المنصة</dt><dd>${row.decision || "WAIT"}</dd></div>
      </dl>
    </article>`;
}

function renderTouch(rows, report = {}, platformReason = "") {
  const byHorizon = new Map(rows.map((row) => [Number(row.horizon_minutes), row]));
  const latest = rows.length ? [...rows].sort((a, b) => a.horizon_minutes - b.horizon_minutes).at(-1) : null;
  if (latest && $("xrpPrice").textContent === "—") {
    $("xrpPrice").textContent = price(Number(latest.anchor_price));
    $("xrpReference").textContent = `مرجع: ${time(latest.anchor_timestamp_ms)}`;
  }
  $("touchHorizonGrid").innerHTML = HORIZONS.map((h) =>
    byHorizon.has(h) ? touchCard(byHorizon.get(h)) : touchWaitCard(h, report, platformReason),
  ).join("");
}

function renderLedger(rows, touchAvailable) {
  const body = $("ledgerBody");
  if (!rows.length) {
    body.innerHTML = `<tr><td colspan="8" class="empty">${touchAvailable
      ? "لا توجد نتائج ناضجة بعد للآفاق المعتمدة"
      : "لا يوجد أفق في Model B اجتاز البوابة؛ أُخفيت سجلات النسخ الملغاة"}</td></tr>`;
    return;
  }
  body.innerHTML = rows.slice(0, 100).map((row) => `
    <tr><td>${time(row.created_at_ms)}</td><td>${horizonLabel(Number(row.horizon_minutes))}</td><td>${price(Number(row.anchor_price))}</td><td>${pct(Number(row.p_up_10))}</td><td>${pct(Number(row.p_down_10))}</td><td>${pct(Number(row.p_no_event))}</td><td>${row.status}</td><td>${row.actual_label || "معلّق"}</td></tr>`).join("");
}

async function refresh() {
  try {
    const responses = await Promise.all([
      fetch("/api/status", { cache: "no-store" }),
      fetch("/api/models", { cache: "no-store" }),
      fetch("/api/models/adaptive-shock/latest", { cache: "no-store" }),
      fetch("/api/models/first-touch/latest", { cache: "no-store" }),
      fetch("/api/ledger?limit=100", { cache: "no-store" }),
      fetch("/api/reports/training/first-touch", { cache: "no-store" }),
      fetch("/api/reports/production", { cache: "no-store" }),
    ]);
    if (!responses.every((response) => response.ok)) throw new Error("API unavailable");
    const [status, catalog, shock, touch, ledger, touchReport, production] = await Promise.all(
      responses.map((response) => response.json()),
    );
    renderStatus(status);
    renderCatalog(catalog, touchReport, production);
    renderShock(shock);
    renderTouch(touch, touchReport, status.reason);
    renderLedger(ledger, Boolean(catalog.first_touch_10.available));
  } catch (error) {
    setConnection("error", "الخادم غير مشغّل أو الدورة الأولى فشلت");
    $("lastTick").textContent = "راجع نافذة التشغيل لمعرفة سبب WAIT";
    $("lifecycleTitle").textContent = "تعذر قراءة حالة التشغيل";
    $("lifecycleMessage").textContent = "لم تستجب واجهة API.";
    waitShockCards();
    waitTouchCards();
  }
}

waitShockCards();
waitTouchCards();
refresh();
setInterval(refresh, 2_000);
