const $ = (id) => document.getElementById(id);
const HORIZONS = [15, 30, 45, 60];

const STAGE_LABELS = {
  IDLE: "بانتظار التشغيل",
  BOOTSTRAP_HISTORY: "جمع السنة التاريخية",
  SYNC_MISSING_TAIL: "استكمال البيانات المفقودة",
  BUILD_ANCHORS: "بناء نقاط التوقع",
  DATA_CHECKPOINTED: "حُفظت البيانات",
  BUILD_FEATURES: "هندسة الخصائص",
  DATA_READY: "البيانات والخصائص جاهزة",
  BUILD_TARGETS_A: "بناء أهداف نموذج A",
  TARGETS_A_READY: "أهداف نموذج A جاهزة",
  TRAIN_MODEL_B: "تدريب نموذج B",
  MODEL_B_WAIT: "نموذج B لم يجتز البوابة",
  MODEL_B_RESEARCH_READY: "نموذج B جاهز بحثيًا",
  TRAIN_MODEL_A: "تدريب نموذج A",
  MODEL_A_WAIT: "نموذج A لم يجتز البوابة",
  MODEL_A_RESEARCH_READY: "نموذج A جاهز بحثيًا",
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
  collecting_completed_market_candles: "جمع شموع السوق المكتملة وحفظها على دفعات.",
  collect_history: "تجميع البيانات التاريخية مع نقاط حفظ قابلة للاستئناف.",
  build_anchors: "بناء نقاط التوقع والنتائج المؤجلة من الشموع الحقيقية.",
  data_checkpointed: "تم حفظ آخر دفعة وتحديث علامة الاستئناف.",
  building_causal_feature_matrix: "حساب خصائص سببية لا تستخدم المستقبل.",
  real_data_and_features_ready_for_model_gates: "اكتمل تجهيز البيانات والخصائص لبوابات التدريب.",
  building_observed_future_excursion_targets: "استخراج أعلى وأدنى حركة مستقبلية مرصودة لنموذج A.",
  model_a_targets_ready_for_training_gate: "اكتمل بناء أهداف نموذج A.",
  training_first_touch_challenger: "تدريب وتقييم نموذج الوصول الأول لكل أفق زمني.",
  training_future_excursion_challenger: "تدريب وتقييم نموذج نطاق الصدمة لكل أفق زمني.",
  model_b_evidence_gate_failed_or_insufficient: "الأدلة أو العينات غير كافية لنشر نموذج B.",
  model_a_evidence_gate_failed_or_insufficient: "الأدلة أو تغطية النطاق غير كافية لنشر نموذج A.",
  model_b_empirical_gate_passed: "اجتاز نموذج B بوابته البحثية، دون ترقية للتداول.",
  model_a_empirical_gate_passed: "اجتاز نموذج A بوابته البحثية، دون ترقية للتداول.",
  creating_model_b_predictions: "إنشاء توقعات Model B قبل معرفة النتائج.",
  model_b_predictions_written_before_outcomes: "تم تثبيت توقعات Model B في السجل قبل نضج النتائج.",
  creating_future_excursion_predictions: "إنشاء نطاقات Model A قبل معرفة النتائج.",
  model_a_predictions_written_before_outcomes: "تم تثبيت توقعات Model A قبل نضج النتائج.",
  maturing_eligible_model_b_predictions: "مطابقة التوقعات التي انتهى أفقها مع المسار الحقيقي.",
  eligible_model_b_outcomes_resolved: "اكتمل تقييم التوقعات المؤهلة.",
  building_dual_model_production_report: "حساب تقارير الأداء المنفصلة للنموذجين.",
  production_report_saved: "تم حفظ تقرير الأداء الحالي.",
  cycle_complete_waiting_for_next_completed_minute: "اكتملت الدورة، والمنصة تنتظر شمعة دقيقة مكتملة جديدة.",
};

function pct(value) {
  return Number.isFinite(value) ? `${(value * 100).toFixed(1)}%` : "—";
}

function price(value) {
  return Number.isFinite(value) ? Number(value).toFixed(3) : "—";
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

function waitShockCards() {
  $("shockHorizonGrid").innerHTML = HORIZONS.map((h) => `
    <article class="horizon-model-card wait-card">
      <header><span>${h} دقيقة</span><strong>WAIT</strong></header>
      <dl><div><dt>أعلى حركة متوقعة</dt><dd>—</dd></div><div><dt>أدنى حركة متوقعة</dt><dd>—</dd></div><div><dt>النطاق الاحتمالي</dt><dd>لم يتدرب النموذج</dd></div></dl>
    </article>`).join("");
}

function waitTouchCards() {
  $("touchHorizonGrid").innerHTML = HORIZONS.map((h) => `
    <article class="horizon-model-card wait-card">
      <header><span>${h} دقيقة</span><strong>WAIT</strong></header>
      <dl><div><dt>+10% أولًا</dt><dd>—</dd></div><div><dt>−10% أولًا</dt><dd>—</dd></div><div><dt>لا حدث</dt><dd>—</dd></div></dl>
    </article>`).join("");
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
  $("platformReason").textContent = status.reason || "—";

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

function renderCatalog(catalog) {
  const shock = catalog.adaptive_shock;
  const touch = catalog.first_touch_10;
  $("shockState").textContent = shock.available ? "READY — RESEARCH" : "WAIT";
  $("shockVersion").textContent = shock.available ? shock.model_version : "لا يوجد نموذج مدرّب";
  $("touchState").textContent = touch.available ? "READY — RESEARCH" : "WAIT";
  $("touchVersion").textContent = touch.available ? touch.model_version : "لا يوجد نموذج مدرّب";

  $("shockMethod").innerHTML = `
    <div class="factor"><span>النوع</span><strong>${shock.technical_name}</strong></div>
    <div class="factor"><span>الغرض</span><strong>${shock.purpose}</strong></div>
    <div class="factor"><span>صفوف التدريب</span><strong>${shock.training_rows ?? "—"}</strong></div>`;
  $("shockGate").innerHTML = `
    <div class="factor"><span>الاختبار</span><strong>${shock.gate}</strong></div>
    <div class="factor"><span>الترقية للتداول</span><strong>غير مفعلة</strong></div>`;
  $("touchMethod").innerHTML = `
    <div class="factor"><span>النوع</span><strong>${touch.technical_name}</strong></div>
    <div class="factor"><span>الغرض</span><strong>${touch.purpose}</strong></div>
    <div class="factor"><span>صفوف التدريب</span><strong>${touch.training_rows ?? "—"}</strong></div>`;
  $("touchGate").innerHTML = `
    <div class="factor"><span>الاختبار</span><strong>${touch.gate}</strong></div>
    <div class="factor"><span>الترقية للتداول</span><strong>غير مفعلة</strong></div>`;
}

function renderShock(rows) {
  if (!rows.length) {
    waitShockCards();
    return;
  }
  const sorted = [...rows].sort((a, b) => a.horizon_minutes - b.horizon_minutes);
  const latest = sorted.at(-1);
  $("xrpPrice").textContent = price(Number(latest.anchor_price));
  $("xrpReference").textContent = `مرجع: ${time(latest.anchor_timestamp_ms)}`;
  $("shockHorizonGrid").innerHTML = sorted.map((row) => `
    <article class="horizon-model-card shock-card">
      <header><span>${row.horizon_minutes} دقيقة</span><strong>${row.empirical_gate || "RESEARCH"}</strong></header>
      <dl>
        <div><dt>أعلى حركة وسطية</dt><dd class="positive">${pct(Number(row.max_return_q50))}</dd></div>
        <div><dt>أدنى حركة وسطية</dt><dd class="negative">${pct(Number(row.min_return_q50))}</dd></div>
        <div><dt>أعلى سعر وسطي</dt><dd>${price(Number(row.max_price_q50))}</dd></div>
        <div><dt>أدنى سعر وسطي</dt><dd>${price(Number(row.min_price_q50))}</dd></div>
        <div><dt>نطاق الصعود 5–95%</dt><dd>${pct(Number(row.max_return_q05))} → ${pct(Number(row.max_return_q95))}</dd></div>
        <div><dt>نطاق الهبوط 5–95%</dt><dd>${pct(Number(row.min_return_q05))} → ${pct(Number(row.min_return_q95))}</dd></div>
      </dl>
    </article>`).join("");
}

function renderTouch(rows) {
  if (!rows.length) {
    waitTouchCards();
    return;
  }
  const sorted = [...rows].sort((a, b) => a.horizon_minutes - b.horizon_minutes);
  const latest = sorted.at(-1);
  if ($("xrpPrice").textContent === "—") {
    $("xrpPrice").textContent = price(Number(latest.anchor_price));
    $("xrpReference").textContent = `مرجع: ${time(latest.anchor_timestamp_ms)}`;
  }
  $("touchHorizonGrid").innerHTML = sorted.map((row) => {
    const values = [Number(row.p_up_10), Number(row.p_down_10), Number(row.p_no_event)];
    const labels = ["UP_10", "DOWN_10", "NO_EVENT"];
    const winner = labels[values.indexOf(Math.max(...values))];
    return `
      <article class="horizon-model-card touch-card">
        <header><span>${row.horizon_minutes} دقيقة</span><strong>${winner}</strong></header>
        <dl>
          <div><dt>+10% أولًا</dt><dd class="positive">${pct(values[0])}</dd></div>
          <div><dt>−10% أولًا</dt><dd class="negative">${pct(values[1])}</dd></div>
          <div><dt>لا حدث</dt><dd>${pct(values[2])}</dd></div>
          <div><dt>قرار المنصة</dt><dd>${row.decision || "WAIT"}</dd></div>
        </dl>
      </article>`;
  }).join("");
}

function renderLedger(rows) {
  const body = $("ledgerBody");
  if (!rows.length) {
    body.innerHTML = '<tr><td colspan="8" class="empty">لم تُسجل توقعات حقيقية بعد</td></tr>';
    return;
  }
  body.innerHTML = rows.slice(0, 100).map((row) => `
    <tr><td>${time(row.created_at_ms)}</td><td>${row.horizon_minutes}</td><td>${price(Number(row.anchor_price))}</td><td>${pct(Number(row.p_up_10))}</td><td>${pct(Number(row.p_down_10))}</td><td>${pct(Number(row.p_no_event))}</td><td>${row.status}</td><td>${row.actual_label || "معلّق"}</td></tr>`).join("");
}

async function refresh() {
  try {
    const responses = await Promise.all([
      fetch("/api/status", { cache: "no-store" }),
      fetch("/api/models", { cache: "no-store" }),
      fetch("/api/models/adaptive-shock/latest", { cache: "no-store" }),
      fetch("/api/models/first-touch/latest", { cache: "no-store" }),
      fetch("/api/ledger?limit=100", { cache: "no-store" }),
    ]);
    if (!responses.every((response) => response.ok)) throw new Error("API unavailable");
    const [status, catalog, shock, touch, ledger] = await Promise.all(responses.map((r) => r.json()));
    renderStatus(status);
    renderCatalog(catalog);
    renderShock(shock);
    renderTouch(touch);
    renderLedger(ledger);
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