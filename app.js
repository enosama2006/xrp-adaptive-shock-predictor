const $ = (id) => document.getElementById(id);

const HORIZONS = [15, 30, 45, 60];

function pct(value) {
  return Number.isFinite(value) ? `${(value * 100).toFixed(1)}%` : "—";
}

function price(value) {
  return Number.isFinite(value) ? Number(value).toFixed(5) : "—";
}

function time(value) {
  return value ? new Date(Number(value)).toLocaleString("ar-SA") : "—";
}

function setConnection(mode, text) {
  $("connectionDot").className = `dot ${mode}`;
  $("connectionStatus").textContent = text;
}

function renderStatus(status) {
  const ready = status.model_available && status.state !== "WAIT";
  setConnection(status.data_end_ms ? "live" : "pending", status.data_end_ms ? "متصل بخادم البيانات الحقيقية" : "بانتظار أول مزامنة حقيقية");
  $("lastTick").textContent = status.data_end_ms ? `آخر دقيقة حقيقية: ${time(status.data_end_ms)}` : "لا توجد بيانات محفوظة بعد";
  $("modelState").textContent = status.state;
  $("sampleState").textContent = `${status.final_rows || 0} نتيجة نهائية`;
  $("tradeDecision").textContent = "WAIT";
  $("tradeDecision").className = "wait";
  $("confidenceBadge").textContent = ready ? "نموذج حقيقي — بحثي" : "لا توجد ثقة قابلة للنشر";
  $("dataFreshness").textContent = status.data_end_ms ? time(status.data_end_ms) : "غير متاحة";
  $("featureCoverage").textContent = status.price_rows ? `${status.price_rows} دقيقة سعرية` : "0";
  $("driftState").textContent = "يُقاس بعد تراكم التوقعات";
  $("calibrationState").textContent = status.model_available ? "متوفرة في تقرير التدريب" : "غير متاحة";
  $("regimeTag").textContent = status.reason || "WAIT";
}

function renderPredictions(rows) {
  if (!rows.length) {
    $("xrpPrice").textContent = "—";
    $("upProbability").textContent = "—";
    $("downProbability").textContent = "—";
    $("noneProbability").textContent = "—";
    $("upBar").style.width = "0%";
    $("downBar").style.width = "0%";
    $("noneBar").style.width = "0%";
    $("horizonRows").innerHTML = HORIZONS.map((h) => `<div class="horizon-row"><span>${h} دقيقة</span><strong>WAIT</strong></div>`).join("");
    $("factorList").innerHTML = '<div class="factor"><span>لا توجد احتمالات معروضة حتى يتدرب نموذج على بيانات حقيقية كافية.</span></div>';
    return;
  }

  const sorted = [...rows].sort((a, b) => a.horizon_minutes - b.horizon_minutes);
  const sixty = sorted.find((row) => row.horizon_minutes === 60) || sorted.at(-1);
  $("xrpPrice").textContent = price(Number(sixty.anchor_price));
  $("xrpSpread").textContent = `مرجع التوقع: ${time(sixty.anchor_timestamp_ms)}`;
  $("upProbability").textContent = pct(Number(sixty.p_up_10));
  $("downProbability").textContent = pct(Number(sixty.p_down_10));
  $("noneProbability").textContent = pct(Number(sixty.p_no_event));
  $("upBar").style.width = `${Number(sixty.p_up_10) * 100}%`;
  $("downBar").style.width = `${Number(sixty.p_down_10) * 100}%`;
  $("noneBar").style.width = `${Number(sixty.p_no_event) * 100}%`;
  $("upEta").textContent = "أفق 60 دقيقة";
  $("downEta").textContent = "أفق 60 دقيقة";

  $("horizonRows").innerHTML = sorted.map((row) => `
    <div class="horizon-row">
      <span>${row.horizon_minutes} دقيقة</span>
      <strong>↑ ${pct(Number(row.p_up_10))} · ↓ ${pct(Number(row.p_down_10))} · لا حدث ${pct(Number(row.p_no_event))}</strong>
    </div>`).join("");

  $("factorList").innerHTML = `
    <div class="factor"><span>المصدر</span><strong>بيانات Binance عامة حقيقية فقط</strong></div>
    <div class="factor"><span>إصدار النموذج</span><strong>${sixty.model_version}</strong></div>
    <div class="factor"><span>حالة التوصية</span><strong>WAIT — لم تتم ترقية النموذج للتداول</strong></div>`;
}

function renderLedger(rows) {
  const body = $("ledgerBody");
  if (!rows.length) {
    body.innerHTML = '<tr><td colspan="7" class="empty">لم تُسجل توقعات حقيقية بعد</td></tr>';
    return;
  }
  body.innerHTML = rows.slice(0, 100).map((row) => `
    <tr>
      <td>${time(row.created_at_ms)}</td>
      <td>${price(Number(row.anchor_price))}</td>
      <td>${pct(Number(row.p_up_10))}</td>
      <td>${pct(Number(row.p_down_10))}</td>
      <td>${row.decision}</td>
      <td>${row.status}</td>
      <td>${row.actual_label || "معلّق"}</td>
    </tr>`).join("");
}

async function refresh() {
  try {
    const [statusResponse, predictionResponse, ledgerResponse] = await Promise.all([
      fetch("/api/status", { cache: "no-store" }),
      fetch("/api/predictions/latest", { cache: "no-store" }),
      fetch("/api/ledger?limit=100", { cache: "no-store" }),
    ]);
    if (![statusResponse, predictionResponse, ledgerResponse].every((response) => response.ok)) {
      throw new Error("API unavailable");
    }
    const [status, predictions, ledger] = await Promise.all([
      statusResponse.json(), predictionResponse.json(), ledgerResponse.json(),
    ]);
    renderStatus(status);
    renderPredictions(predictions);
    renderLedger(ledger);
  } catch (error) {
    setConnection("error", "الخادم غير مشغّل");
    $("lastTick").textContent = "شغّل xasp-platform لبدء البيانات والتدريب الحقيقي";
    $("tradeDecision").textContent = "WAIT";
    renderPredictions([]);
  }
}

$("clearLedger").disabled = true;
$("clearLedger").title = "السجل الحقيقي غير قابل للمسح من الواجهة";
$("btcPrice").textContent = "—";
$("btcMomentum").textContent = "سيُربط ضمن مصفوفة السوق";
refresh();
setInterval(refresh, 5_000);
