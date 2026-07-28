const $ = (id) => document.getElementById(id);
const HORIZONS = [60, 120, 180, 240, 300, 360, 420, 480];

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
  training_joint_hourly_competing_risk_model: "تدريب نموذج واحد مشترك لاتجاه اللمس وساعة الوصول خلال 8 ساعات.",
  training_future_excursion_independent_horizon_challengers: "اختبار كل أفق في Model A بصورة مستقلة.",
  model_b_all_horizons_wait: "لم يجتز أي أفق في Model B البوابة الحالية.",
  joint_hourly_competing_risk_model_ready: "أصبح توزيع اتجاه اللمس وساعة الوصول المتسق جاهزًا.",
  model_a_all_horizons_wait: "لم يجتز أي أفق في Model A البوابة الحالية.",
  model_a_challengers_rejected_existing_horizons_retained: "رُفضت التحديات الجديدة واحتُفظ بالآفاق المعتمدة سابقًا.",
  model_a_independent_horizon_gates_evaluated: "اجتازت بعض الآفاق أو كلها بوابات Model A المستقلة.",
  creating_coherent_joint_first_touch_timeline: "إنشاء تايم لاين احتمالات متسق من توزيع واحد.",
  coherent_joint_first_touch_timeline_stored: "حُفظ تايم لاين أول وصول المتسق.",
  creating_future_excursion_predictions_for_available_horizons: "إنشاء نطاقات Model A للآفاق المعتمدة فقط.",
  model_a_available_horizon_predictions_stored: "حُفظت نطاقات Model A للآفاق المعتمدة.",
  maturing_eligible_model_b_predictions: "مطابقة التوقعات التي انتهى أفقها مع المسار الحقيقي.",
  eligible_model_b_outcomes_resolved: "اكتمل تقييم التوقعات المؤهلة.",
  building_extended_horizon_production_report: "حساب الأداء الحي لكل أفق حتى 8 ساعات.",
  extended_horizon_production_report_saved: "تم حفظ تقرير الأداء الممتد.",
  cycle_complete_waiting_for_next_completed_minute: "اكتملت الدورة، والمنصة تنتظر شمعة دقيقة مكتملة جديدة.",
};

const REASON_LABELS = {
  both_model_independent_horizon_gates_pending: "بوابتا النموذج المشترك والمسار السعري ما زالتا قيد التحقق",
  dual_models_all_horizons_research_monitoring_only: "كل آفاق النموذجين جاهزة للمراقبة البحثية فقط",
  dual_models_some_independent_horizons_ready_others_wait: "جزء من التوقع السعري أو الاتجاهي جاهز، والبقية في الانتظار",
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
  report_matches_current_independent_horizon_gate: "التقرير مطابق لبوابات الإصدار السابق",
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
  no_valid_hourly_first_touch_prediction: "لم يجتز أي نموذج ساعي بوابة التحقق بعد",
  directional_probabilities_tied: "احتمالا الصعود والهبوط متساويان",
  "2pct_touch_probability_too_low_within_available_hours": "احتمال بلوغ ±2% خلال الساعات المتاحة منخفض",
  directional_edge_too_small: "الفارق بين احتمال الصعود والهبوط غير كافٍ",
  directional_2pct_first_touch_edge: "الاتجاه المرجح وفق أول وصول إلى ±2%",
  joint_direction_and_price_path_models_pending: "نموذجا المسار السعري والاتجاه المشترك لم يكملا التدريب بعد",
  joint_first_touch_forecast_available_advisory_wait: "التوقع الاحتمالي متاح، لكن بوابة القرار العملي لم تجتز بعد",
  validated_joint_competing_risk_signal: "إشارة النموذج المشترك اجتازت سياسة القرار المختبرة زمنيًا",
  validated_policy_thresholds_not_met_now: "النموذج صالح لكن قراءة السوق الحالية دون عتبات القرار",
  no_joint_competing_risk_forecast_available: "لم يكتمل تدريب نموذج اتجاه وساعة الوصول المشترك بعد",
  report_matches_current_joint_competing_risk_gate: "التقرير مطابق لنموذج المخاطر المتنافسة الحالي",
};

function reasonLabel(value) {
  return REASON_LABELS[value] || value || "—";
}

function horizonLabel(value) {
  const labels = {
    60: "60 دقيقة (ساعة)",
    120: "120 دقيقة (ساعتان)",
    180: "180 دقيقة (3 ساعات)",
    240: "240 دقيقة (4 ساعات)",
    300: "300 دقيقة (5 ساعات)",
    360: "360 دقيقة (6 ساعات)",
    420: "420 دقيقة (7 ساعات)",
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

function renderPricePathChart(forecast = {}) {
  const svg = $("pricePathChart");
  if (!svg) return;
  const anchor = Number(forecast.anchor_price);
  const rows = Array.isArray(forecast.timeline) ? forecast.timeline : [];
  const projected = rows
    .filter((row) =>
      Number.isFinite(Number(row.predicted_close_price_q05)) &&
      Number.isFinite(Number(row.predicted_close_price_q50)) &&
      Number.isFinite(Number(row.predicted_close_price_q95)))
    .map((row) => ({
      hour: Number(row.hour),
      low: Number(row.predicted_close_price_q05),
      mid: Number(row.predicted_close_price_q50),
      high: Number(row.predicted_close_price_q95),
    }));
  if (!Number.isFinite(anchor) || projected.length === 0) {
    svg.innerHTML = '<text x="480" y="165" text-anchor="middle" class="chart-empty">لا يوجد مسار سعري صالح بعد</text>';
    return;
  }

  const points = [{ hour: 0, low: anchor, mid: anchor, high: anchor }, ...projected];
  const targets = [Number(forecast.up_target_price), Number(forecast.down_target_price)]
    .filter(Number.isFinite);
  const values = points.flatMap((point) => [point.low, point.mid, point.high]).concat(targets);
  let minimum = Math.min(...values);
  let maximum = Math.max(...values);
  const rawSpan = maximum - minimum;
  const padding = rawSpan > 0 ? rawSpan * 0.12 : anchor * 0.01;
  minimum -= padding;
  maximum += padding;
  const width = 960;
  const height = 320;
  const left = 72;
  const right = 24;
  const top = 22;
  const bottom = 42;
  const plotWidth = width - left - right;
  const plotHeight = height - top - bottom;
  const x = (hour) => left + (hour / 8) * plotWidth;
  const y = (value) => top + ((maximum - value) / (maximum - minimum)) * plotHeight;
  const path = (items, key) => items
    .map((point, index) => `${index ? "L" : "M"} ${x(point.hour).toFixed(1)} ${y(point[key]).toFixed(1)}`)
    .join(" ");
  const band = [
    ...points.map((point) => `${x(point.hour).toFixed(1)},${y(point.high).toFixed(1)}`),
    ...[...points].reverse().map((point) => `${x(point.hour).toFixed(1)},${y(point.low).toFixed(1)}`),
  ].join(" ");
  const horizontalGrid = Array.from({ length: 5 }, (_, index) => {
    const value = maximum - ((maximum - minimum) * index / 4);
    const py = y(value);
    return `<line x1="${left}" y1="${py}" x2="${width - right}" y2="${py}" class="chart-grid"/>
      <text x="${left - 10}" y="${py + 4}" text-anchor="end" class="chart-axis-label">${price(value)}</text>`;
  }).join("");
  const hourLabels = Array.from({ length: 9 }, (_, hour) =>
    `<text x="${x(hour)}" y="${height - 14}" text-anchor="middle" class="chart-axis-label">${hour === 0 ? "الآن" : `${hour}س`}</text>`
  ).join("");
  const targetLine = (value, className) => Number.isFinite(value)
    ? `<line x1="${left}" y1="${y(value)}" x2="${width - right}" y2="${y(value)}" class="${className}"/>`
    : "";
  const circles = points.map((point) =>
    `<circle cx="${x(point.hour)}" cy="${y(point.mid)}" r="5" class="chart-point ${point.hour === 0 ? "chart-anchor" : ""}">
      <title>${point.hour === 0 ? "الآن" : `بعد ${point.hour} ساعة`}: ${price(point.mid)}</title>
    </circle>`
  ).join("");
  svg.innerHTML = `${horizontalGrid}${hourLabels}
    ${targetLine(Number(forecast.up_target_price), "chart-target-up")}
    ${targetLine(Number(forecast.down_target_price), "chart-target-down")}
    <polygon points="${band}" class="chart-band"/>
    <path d="${path(points, "mid")}" class="chart-path"/>
    ${circles}`;
}

function setConnection(mode, text) {
  $("connectionDot").className = `dot ${mode}`;
  $("connectionStatus").textContent = text;
}

function renderDirectionalForecast(forecast = {}) {
  const decision = forecast.decision || "WAIT";
  const bias = forecast.directional_bias;
  const decisionElement = $("directionalDecision");
  decisionElement.textContent = decision;
  decisionElement.className = decision === "LONG"
    ? "decision-long"
    : decision === "SHORT"
      ? "decision-short"
      : "decision-wait";

  $("decisionAnchorPrice").textContent = price(forecast.anchor_price);
  $("decisionUpTarget").textContent = price(forecast.up_target_price);
  $("decisionDownTarget").textContent = price(forecast.down_target_price);
  $("decisionPredictedPrice").textContent = price(forecast.predicted_target_price);
  $("decisionClose8h").textContent = price(forecast.predicted_close_price_8h_q50);
  $("decisionHigh8h").textContent = price(forecast.predicted_high_price_8h_q50);
  $("decisionLow8h").textContent = price(forecast.predicted_low_price_8h_q50);
  $("decisionConfidence").textContent = pct(forecast.directional_probability);
  $("decisionEventProbability").textContent = pct(forecast.event_probability);
  $("decisionTouchWindow").textContent = forecast.expected_touch_horizon_minutes
    ? horizonLabel(Number(forecast.expected_touch_horizon_minutes))
    : "—";
  $("decisionTimestamp").textContent = time(forecast.anchor_timestamp_ms);

  if (decision === "LONG") {
    $("directionalExplanation").textContent = `السيناريو المرجح هو وصول +2% أولًا نحو ${price(forecast.predicted_target_price)}.`;
  } else if (decision === "SHORT") {
    $("directionalExplanation").textContent = `السيناريو المرجح هو وصول −2% أولًا نحو ${price(forecast.predicted_target_price)}.`;
  } else if (bias === "LONG" || bias === "SHORT") {
    $("directionalExplanation").textContent = `الميل الحالي ${bias}، لكن الثقة أو احتمال حدوث حركة ±2% لا يكفيان لإصدار قرار. السبب: ${reasonLabel(forecast.decision_reason)}.`;
  } else {
    $("directionalExplanation").textContent = `WAIT — ${reasonLabel(forecast.decision_reason)}`;
  }

  const timeline = Array.isArray(forecast.timeline) ? forecast.timeline : [];
  renderPricePathChart(forecast);
  $("directionalTimelineBody").innerHTML = timeline.length
    ? timeline.map((row) => `
      <tr>
        <td>${horizonLabel(Number(row.horizon_minutes))}</td>
        <td>${price(row.predicted_close_price_q05)}</td>
        <td><strong>${price(row.predicted_close_price_q50)}</strong></td>
        <td>${price(row.predicted_close_price_q95)}</td>
        <td class="positive">${pct(row.p_up_first_by_horizon)}</td>
        <td class="negative">${pct(row.p_down_first_by_horizon)}</td>
        <td>${row.directional_bias || "—"}</td>
        <td>${price(row.predicted_high_price_q50)} / ${price(row.predicted_low_price_q50)}</td>
      </tr>`).join("")
    : '<tr><td colspan="8" class="empty">لا يوجد توقع ساعي صالح بعد</td></tr>';

  const basis = forecast.training_basis || {};
  if (basis.requested_history_days) {
    $("directionalTrainingBasis").textContent =
      `النافذة المطلوبة ${count(basis.requested_history_days)} يومًا من شموع Binance الدقيقة الحقيقية. ` +
      "الخصائص: العوائد والزخم والتقلب وRSI وATR وBollinger والحجم وتدفق التداول. " +
      "القيمة السوقية ودفتر الأوامر التاريخي غير المدون لا يدخلان التدريب.";
  }
}

function shockWaitCard(horizon, reason = "لم يجتز الأفق بوابة التغطية بعد") {
  return `
    <article class="horizon-model-card wait-card">
      <header><span>${horizonLabel(horizon)}</span><strong>WAIT — GATE</strong></header>
      <dl>
        <div><dt>هدف الصعود</dt><dd class="positive">+2% فأكثر</dd></div>
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
  const joint = report?._joint || {};
  const jointCounts = joint.class_counts || {};
  const jointUp = Object.entries(jointCounts)
    .filter(([label]) => label.startsWith("UP_H"))
    .reduce((sum, [, value]) => sum + Number(value || 0), 0);
  const jointDown = Object.entries(jointCounts)
    .filter(([label]) => label.startsWith("DOWN_H"))
    .reduce((sum, [, value]) => sum + Number(value || 0), 0);
  const jointGate = joint.metrics?.advisory_gate || {};
  const metrics = current.metrics || {};
  const explicit = metrics.directional_test_support || {};
  const perClass = metrics.per_class || {};
  const walkForward = metrics.walk_forward_support_audit || {};
  const aggregate = walkForward.aggregate_event_support || {};
  const clusters = walkForward.aggregate_independent_event_clusters || {};
  return {
    up: Number(explicit.UP_02 ?? aggregate.UP_02 ?? perClass.UP_02?.support ?? jointUp),
    down: Number(explicit.DOWN_02 ?? aggregate.DOWN_02 ?? perClass.DOWN_02?.support ?? jointDown),
    upClusters: Number(clusters.UP_02 || 0),
    downClusters: Number(clusters.DOWN_02 || 0),
    eligibleFolds: Number(walkForward.eligible_fold_count || 0),
    foldCount: Number(walkForward.fold_count || 0),
    reason: current.reason || jointGate.reason || report?._meta?.reason || "no_first_touch_training_report",
    status: current.status || joint.status || report?._meta?.status || "WAIT",
  };
}

function touchWaitCard(horizon, report = {}, platformReason = "") {
  const evidence = directionalSupport(report, horizon);
  const reason = evidence.reason || platformReason;
  return `
    <article class="horizon-model-card wait-card">
      <header><span>${horizonLabel(horizon)}</span><strong>WAIT — JOINT GATE</strong></header>
      <dl>
        <div><dt>صفوف +2% / −2%</dt><dd>${count(evidence.up)} / ${count(evidence.down)}</dd></div>
        <div><dt>صدمات مستقلة +2% / −2%</dt><dd>${count(evidence.upClusters)} / ${count(evidence.downClusters)}</dd></div>
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
  const touch = catalog.first_touch_02;
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
    <div class="factor"><span>هدف الصعود الحاكم</span><strong>+2% فأكثر</strong></div>
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
        <div><dt>هدف +2% وفق الوسيط</dt><dd class="${row.target_up_reached_by_median ? "positive" : ""}">${row.target_up_reached_by_median ? "مرجّح بلوغه" : "غير مرجّح بالوسيط"}</dd></div>
        <div><dt>سعر هدف +2%</dt><dd>${price(Number(row.target_up_price))}</dd></div>
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
  const values = [Number(row.p_up_02), Number(row.p_down_02), Number(row.p_no_event)];
  const labels = ["UP_02", "DOWN_02", "NO_EVENT"];
  const winner = labels[values.indexOf(Math.max(...values))];
  const directionalMass = values[0] + values[1];
  const directionalConfidence = directionalMass > 0
    ? Math.max(values[0], values[1]) / directionalMass
    : 0.5;
  const targetPrice = winner === "UP_02"
    ? Number(row.anchor_price) * 1.02
    : winner === "DOWN_02"
      ? Number(row.anchor_price) * 0.98
      : NaN;
  return `
    <article class="horizon-model-card touch-card">
      <header><span>${horizonLabel(Number(row.horizon_minutes))}</span><strong>${winner}</strong></header>
      <dl>
        <div><dt>+2% أولًا</dt><dd class="positive">${pct(values[0])}</dd></div>
        <div><dt>−2% أولًا</dt><dd class="negative">${pct(values[1])}</dd></div>
        <div><dt>لا حدث</dt><dd>${pct(values[2])}</dd></div>
        <div><dt>ثقة الاتجاه عند اللمس</dt><dd>${pct(directionalConfidence)}</dd></div>
        <div><dt>سعر الهدف المرجح</dt><dd>${price(targetPrice)}</dd></div>
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
    <tr><td>${time(row.created_at_ms)}</td><td>${horizonLabel(Number(row.horizon_minutes))}</td><td>${price(Number(row.anchor_price))}</td><td>${pct(Number(row.p_up_02))}</td><td>${pct(Number(row.p_down_02))}</td><td>${pct(Number(row.p_no_event))}</td><td>${row.status}</td><td>${row.actual_label || "معلّق"}</td></tr>`).join("");
}

async function refresh() {
  try {
    const responses = await Promise.all([
      fetch("/api/forecast/directional", { cache: "no-store" }),
      fetch("/api/status", { cache: "no-store" }),
      fetch("/api/models", { cache: "no-store" }),
      fetch("/api/models/adaptive-shock/latest", { cache: "no-store" }),
      fetch("/api/models/first-touch/latest", { cache: "no-store" }),
      fetch("/api/ledger?limit=100", { cache: "no-store" }),
      fetch("/api/reports/training/first-touch", { cache: "no-store" }),
      fetch("/api/reports/production", { cache: "no-store" }),
    ]);
    if (!responses.every((response) => response.ok)) throw new Error("API unavailable");
    const [forecast, status, catalog, shock, touch, ledger, touchReport, production] = await Promise.all(
      responses.map((response) => response.json()),
    );
    renderDirectionalForecast(forecast);
    renderStatus(status);
    renderCatalog(catalog, touchReport, production);
    renderShock(shock);
    renderTouch(touch, touchReport, status.reason);
    renderLedger(ledger, Boolean(catalog.first_touch_02.available));
  } catch (error) {
    setConnection("error", "الخادم غير مشغّل أو الدورة الأولى فشلت");
    $("lastTick").textContent = "راجع نافذة التشغيل لمعرفة سبب WAIT";
    $("lifecycleTitle").textContent = "تعذر قراءة حالة التشغيل";
    $("lifecycleMessage").textContent = "لم تستجب واجهة API.";
    renderDirectionalForecast({
      decision: "WAIT",
      decision_reason: "no_valid_hourly_first_touch_prediction",
    });
    waitShockCards();
    waitTouchCards();
  }
}

waitShockCards();
waitTouchCards();
refresh();
setInterval(refresh, 2_000);
