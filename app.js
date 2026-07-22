const $ = (id) => document.getElementById(id);
const HORIZONS = [15, 30, 45, 60];

function pct(value) {
  return Number.isFinite(value) ? `${(value * 100).toFixed(1)}%` : "—";
}

function price(value) {
  return Number.isFinite(value) ? Number(value).toFixed(3) : "—";
}

function time(value) {
  return value ? new Date(Number(value)).toLocaleString("ar-SA") : "—";
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
  const connected = Boolean(status.data_end_ms);
  setConnection(connected ? "live" : "pending", connected ? "متصل بخادم البيانات الحقيقية" : "بانتظار أول مزامنة حقيقية");
  $("lastTick").textContent = connected ? `آخر دقيقة: ${time(status.data_end_ms)}` : "لا توجد بيانات محفوظة بعد";
  $("priceRows").textContent = Number(status.price_rows || 0).toLocaleString("ar-SA");
  $("dataStart").textContent = time(status.data_start_ms);
  $("platformState").textContent = status.state || "WAIT";
  $("platformState").className = status.state === "WAIT" ? "wait" : "";
  $("platformReason").textContent = status.reason || "—";
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
    waitShockCards();
    waitTouchCards();
  }
}

waitShockCards();
waitTouchCards();
refresh();
setInterval(refresh, 5_000);
