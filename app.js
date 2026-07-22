const $ = (id) => document.getElementById(id);

const CONFIG = Object.freeze({
  symbol: "XRPUSDT",
  upperBarrier: 0.10,
  lowerBarrier: -0.10,
  horizonsMinutes: [15, 30, 45, 60],
  forecastCadenceMs: 60_000,
  historyWindowMs: 90 * 60_000,
  minimumWarmupMinutes: 15,
  minimumEvaluatedEventsForAnySignal: 500,
  websocketUrl:
    "wss://stream.binance.com:9443/stream?streams=xrpusdt@bookTicker/xrpusdt@aggTrade/btcusdt@bookTicker/btcusdt@aggTrade",
});

const state = {
  socket: null,
  reconnectTimer: null,
  prices: { xrp: null, btc: null },
  books: { xrp: null, btc: null },
  ticks: { xrp: [], btc: [] },
  trades: [],
  lastMessageAt: null,
  startedAt: Date.now(),
  lastForecastAt: 0,
  ledger: loadLedger(),
};

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function mean(values) {
  return values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : 0;
}

function standardDeviation(values) {
  if (values.length < 2) return 0;
  const avg = mean(values);
  return Math.sqrt(mean(values.map((value) => (value - avg) ** 2)));
}

function formatPrice(value, digits = 5) {
  return Number.isFinite(value) ? value.toFixed(digits) : "—";
}

function formatProbability(value) {
  return Number.isFinite(value) ? `${(value * 100).toFixed(1)}%` : "—";
}

function formatTime(timestamp) {
  return new Date(timestamp).toLocaleTimeString("ar-SA", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function saveLedger() {
  localStorage.setItem("xasp_ledger_v1", JSON.stringify(state.ledger.slice(0, 250)));
}

function loadLedger() {
  try {
    const parsed = JSON.parse(localStorage.getItem("xasp_ledger_v1") || "[]");
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function connect() {
  clearTimeout(state.reconnectTimer);
  setConnection("pending", "جاري الاتصال…");
  const socket = new WebSocket(CONFIG.websocketUrl);
  state.socket = socket;

  socket.onopen = () => setConnection("live", "متصل ببيانات Binance العامة");
  socket.onerror = () => setConnection("error", "خطأ في الاتصال");
  socket.onclose = () => {
    setConnection("pending", "إعادة اتصال…");
    state.reconnectTimer = setTimeout(connect, 2500);
  };
  socket.onmessage = (event) => {
    const message = JSON.parse(event.data);
    const data = message.data || message;
    ingestMessage(data);
  };
}

function setConnection(mode, text) {
  $("connectionDot").className = `dot ${mode}`;
  $("connectionStatus").textContent = text;
}

function ingestMessage(data) {
  const symbol = String(data.s || "").toLowerCase();
  const key = symbol.startsWith("xrp") ? "xrp" : "btc";
  const timestamp = Number(data.E || data.T || Date.now());
  state.lastMessageAt = timestamp;

  if (data.e === "aggTrade") {
    const price = Number(data.p);
    const quantity = Number(data.q);
    if (!Number.isFinite(price) || !Number.isFinite(quantity)) return;
    state.prices[key] = price;
    if (key === "xrp") {
      state.trades.push({
        timestamp: Number(data.T || timestamp),
        price,
        quantity,
        aggressor: data.m ? -1 : 1,
      });
    }
    appendTick(key, timestamp, price);
  } else if (data.b && data.a) {
    const bid = Number(data.b);
    const ask = Number(data.a);
    const bidQuantity = Number(data.B);
    const askQuantity = Number(data.A);
    if (![bid, ask, bidQuantity, askQuantity].every(Number.isFinite)) return;
    state.books[key] = { timestamp, bid, ask, bidQuantity, askQuantity };
    state.prices[key] = (bid + ask) / 2;
    appendTick(key, timestamp, state.prices[key]);
  }

  pruneState();
}

function appendTick(key, timestamp, price) {
  const list = state.ticks[key];
  const previous = list[list.length - 1];
  if (!previous || previous.timestamp !== timestamp || previous.price !== price) {
    list.push({ timestamp, price });
  }
}

function pruneState() {
  const cutoff = Date.now() - CONFIG.historyWindowMs;
  state.ticks.xrp = state.ticks.xrp.filter((row) => row.timestamp >= cutoff);
  state.ticks.btc = state.ticks.btc.filter((row) => row.timestamp >= cutoff);
  state.trades = state.trades.filter((row) => row.timestamp >= cutoff);
}

function priceAtOrBefore(key, cutoff) {
  const eligible = state.ticks[key].filter((row) => row.timestamp <= cutoff);
  return eligible.length ? eligible[eligible.length - 1].price : null;
}

function returnOver(key, minutes) {
  const current = state.prices[key];
  const start = priceAtOrBefore(key, Date.now() - minutes * 60_000);
  return Number.isFinite(current) && Number.isFinite(start) && start > 0 ? current / start - 1 : 0;
}

function realizedVolatility(minutes = 5) {
  const cutoff = Date.now() - minutes * 60_000;
  const ticks = state.ticks.xrp.filter((row) => row.timestamp >= cutoff);
  if (ticks.length < 5) return 0;
  const returns = [];
  for (let index = 1; index < ticks.length; index += 1) {
    if (ticks[index - 1].price > 0) returns.push(Math.log(ticks[index].price / ticks[index - 1].price));
  }
  return standardDeviation(returns) * Math.sqrt(Math.max(returns.length, 1));
}

function orderBookImbalance() {
  const book = state.books.xrp;
  if (!book) return 0;
  return (book.bidQuantity - book.askQuantity) / (book.bidQuantity + book.askQuantity + Number.EPSILON);
}

function tradeFlowImbalance(seconds = 30) {
  const cutoff = Date.now() - seconds * 1000;
  const recent = state.trades.filter((row) => row.timestamp >= cutoff);
  let buy = 0;
  let sell = 0;
  for (const trade of recent) {
    const notional = trade.price * trade.quantity;
    if (trade.aggressor > 0) buy += notional;
    else sell += notional;
  }
  return (buy - sell) / (buy + sell + Number.EPSILON);
}

function featureSnapshot() {
  const book = state.books.xrp;
  const spreadBps = book && state.prices.xrp ? ((book.ask - book.bid) / state.prices.xrp) * 10_000 : null;
  return {
    xrpReturn1m: returnOver("xrp", 1),
    xrpReturn5m: returnOver("xrp", 5),
    xrpReturn15m: returnOver("xrp", 15),
    btcReturn5m: returnOver("btc", 5),
    btcReturn15m: returnOver("btc", 15),
    volatility5m: realizedVolatility(5),
    orderBookImbalance: orderBookImbalance(),
    tradeFlowImbalance: tradeFlowImbalance(30),
    spreadBps,
  };
}

function classifyRegime(features) {
  const momentum = features.xrpReturn5m;
  const pressure = 0.55 * features.tradeFlowImbalance + 0.45 * features.orderBookImbalance;
  const volatility = features.volatility5m;
  if (volatility > 0.02 && pressure > 0.25) return "تقلب انفجاري مع ضغط شراء";
  if (volatility > 0.02 && pressure < -0.25) return "تقلب انفجاري مع ضغط بيع";
  if (momentum > 0.01 && pressure > 0.1) return "اتجاه صاعد قصير";
  if (momentum < -0.01 && pressure < -0.1) return "اتجاه هابط قصير";
  if (Math.abs(momentum) < 0.003 && Math.abs(pressure) < 0.12) return "توازن منخفض الإشارة";
  return "نظام مختلط";
}

/*
 * This is intentionally a transparent research baseline, not a validated model.
 * It produces provisional research probabilities so the event registry, UI,
 * delayed labels, and governance gates can be exercised before a trained model exists.
 */
function provisionalProbabilities(features) {
  const momentumScore = clamp(features.xrpReturn5m * 18 + features.xrpReturn15m * 8, -2.5, 2.5);
  const marketScore = clamp(features.btcReturn5m * 10 + features.btcReturn15m * 5, -1.2, 1.2);
  const flowScore = clamp(features.tradeFlowImbalance * 1.1 + features.orderBookImbalance * 0.7, -1.8, 1.8);
  const volatilityGate = clamp(features.volatility5m / 0.025, 0, 1.5);
  const liquidityPenalty = Number.isFinite(features.spreadBps) ? clamp((features.spreadBps - 2) / 20, 0, 0.7) : 0.3;
  const directional = clamp(momentumScore + marketScore + flowScore, -4, 4);
  const eventMass = clamp(0.02 + 0.28 * volatilityGate + 0.07 * Math.abs(directional) - liquidityPenalty * 0.08, 0.01, 0.62);
  const upShare = 1 / (1 + Math.exp(-directional));
  const up = eventMass * upShare;
  const down = eventMass * (1 - upShare);
  const none = 1 - eventMass;
  return { up, down, none };
}

function horizonHazards(probabilities) {
  return CONFIG.horizonsMinutes.map((minutes) => {
    const timeFraction = minutes / 60;
    const cumulativeEvent = (probabilities.up + probabilities.down) * Math.pow(timeFraction, 0.82);
    return { minutes, probability: clamp(cumulativeEvent, 0, 1) };
  });
}

function modelReadiness() {
  const warmupMinutes = (Date.now() - state.startedAt) / 60_000;
  const evaluated = state.ledger.filter((row) => row.status === "evaluated").length;
  const featureReady = Boolean(state.books.xrp && state.books.btc && state.ticks.xrp.length > 20 && state.ticks.btc.length > 20);
  return {
    warmupMinutes,
    evaluated,
    featureReady,
    canSignal: featureReady && evaluated >= CONFIG.minimumEvaluatedEventsForAnySignal,
  };
}

function decision(probabilities, readiness) {
  if (!readiness.canSignal) return { label: "WAIT", className: "wait", reason: "بوابة التحقق لم تُجتز" };
  if (probabilities.up >= 0.7 && probabilities.up - probabilities.down >= 0.35) {
    return { label: "LONG", className: "long", reason: "احتمال صعود متفوق بعد التحقق" };
  }
  if (probabilities.down >= 0.7 && probabilities.down - probabilities.up >= 0.35) {
    return { label: "SHORT", className: "short", reason: "احتمال هبوط متفوق بعد التحقق" };
  }
  return { label: "WAIT", className: "wait", reason: "لا توجد أفضلية قابلة للتداول" };
}

function createForecast(features, probabilities, tradeDecision) {
  if (!state.prices.xrp || Date.now() - state.lastForecastAt < CONFIG.forecastCadenceMs) return;
  state.lastForecastAt = Date.now();
  state.ledger.unshift({
    id: crypto.randomUUID(),
    issuedAt: Date.now(),
    expiresAt: Date.now() + 60 * 60_000,
    referencePrice: state.prices.xrp,
    upperPrice: state.prices.xrp * 1.1,
    lowerPrice: state.prices.xrp * 0.9,
    upProbability: probabilities.up,
    downProbability: probabilities.down,
    noneProbability: probabilities.none,
    decision: tradeDecision.label,
    status: "pending",
    outcome: null,
    hitAt: null,
    features,
  });
  saveLedger();
}

function evaluateLedger() {
  const currentPrice = state.prices.xrp;
  if (!Number.isFinite(currentPrice)) return;
  let changed = false;
  for (const row of state.ledger) {
    if (row.status !== "pending") continue;
    if (currentPrice >= row.upperPrice) {
      row.status = "evaluated";
      row.outcome = "UP_10";
      row.hitAt = Date.now();
      changed = true;
    } else if (currentPrice <= row.lowerPrice) {
      row.status = "evaluated";
      row.outcome = "DOWN_10";
      row.hitAt = Date.now();
      changed = true;
    } else if (Date.now() >= row.expiresAt) {
      row.status = "evaluated";
      row.outcome = "NO_EVENT";
      row.hitAt = row.expiresAt;
      changed = true;
    }
  }
  if (changed) saveLedger();
}

function render() {
  const features = featureSnapshot();
  const probabilities = provisionalProbabilities(features);
  const readiness = modelReadiness();
  const tradeDecision = decision(probabilities, readiness);
  const hazards = horizonHazards(probabilities);

  $("xrpPrice").textContent = formatPrice(state.prices.xrp, 5);
  $("btcPrice").textContent = formatPrice(state.prices.btc, 2);
  $("lastTick").textContent = state.lastMessageAt ? `آخر تحديث ${formatTime(state.lastMessageAt)}` : "لا توجد بيانات بعد";
  $("xrpSpread").textContent = Number.isFinite(features.spreadBps) ? `السبريد ${features.spreadBps.toFixed(2)} bps` : "السبريد —";
  $("btcMomentum").textContent = `زخم 5 دقائق ${(features.btcReturn5m * 100).toFixed(3)}%`;

  $("modelState").textContent = readiness.canSignal ? "مؤهل بحثيًا" : readiness.featureReady ? "رصد تجريبي" : "إحماء";
  $("sampleState").textContent = `${readiness.evaluated} عينة قابلة للتقييم`;
  $("tradeDecision").textContent = tradeDecision.label;
  $("tradeDecision").className = tradeDecision.className;

  $("upProbability").textContent = formatProbability(probabilities.up);
  $("downProbability").textContent = formatProbability(probabilities.down);
  $("noneProbability").textContent = formatProbability(probabilities.none);
  $("upBar").style.width = `${probabilities.up * 100}%`;
  $("downBar").style.width = `${probabilities.down * 100}%`;
  $("noneBar").style.width = `${probabilities.none * 100}%`;
  $("upEta").textContent = `الوقت المرجح: ${estimateEta(probabilities.up, hazards)}`;
  $("downEta").textContent = `الوقت المرجح: ${estimateEta(probabilities.down, hazards)}`;

  const maxDirectional = Math.max(probabilities.up, probabilities.down);
  $("confidenceBadge").textContent = !readiness.featureReady ? "ثقة غير متاحة" : maxDirectional >= 0.6 ? "ثقة بحثية مرتفعة" : maxDirectional >= 0.35 ? "ثقة بحثية متوسطة" : "ثقة بحثية منخفضة";
  $("regimeTag").textContent = classifyRegime(features);

  renderFactors(features);
  renderHorizons(hazards);
  renderQuality(features, readiness);
  renderLedger();

  createForecast(features, probabilities, tradeDecision);
}

function estimateEta(directionProbability, hazards) {
  if (directionProbability < 0.08) return "غير مرجح";
  const threshold = Math.min(directionProbability * 0.65, 0.35);
  const match = hazards.find((row) => row.probability >= threshold);
  return match ? `${Math.max(15, match.minutes - 10)}–${match.minutes} دقيقة` : "45–60 دقيقة";
}

function renderFactors(features) {
  const factors = [
    {
      title: "تدفق الصفقات المنفذة",
      detail: "صافي ضغط المشترين مقابل البائعين خلال 30 ثانية",
      value: features.tradeFlowImbalance,
    },
    {
      title: "اختلال أفضل عرض وطلب",
      detail: "كمية الشراء المعروضة مقابل كمية البيع في أفضل مستوى",
      value: features.orderBookImbalance,
    },
    {
      title: "زخم XRP خلال 5 دقائق",
      detail: "اتجاه الحركة الحديثة وليس إشارة مستقلة",
      value: clamp(features.xrpReturn5m * 20, -1, 1),
    },
    {
      title: "دعم BTC خلال 5 دقائق",
      detail: "السوق المرجعي قد يدعم الحركة أو يعارضها",
      value: clamp(features.btcReturn5m * 20, -1, 1),
    },
    {
      title: "بوابة التقلب",
      detail: "مدى توافر بيئة تسمح أصلًا بحركة كبيرة",
      value: clamp(features.volatility5m / 0.02, 0, 1),
    },
  ];

  $("factorList").innerHTML = factors
    .map((factor) => {
      const className = factor.value > 0.08 ? "positive" : factor.value < -0.08 ? "negative" : "neutral";
      const prefix = factor.value > 0 ? "+" : "";
      return `<div class="factor"><div><strong>${factor.title}</strong><small>${factor.detail}</small></div><b class="factor-score ${className}">${prefix}${factor.value.toFixed(3)}</b></div>`;
    })
    .join("");
}

function renderHorizons(hazards) {
  $("horizonRows").innerHTML = hazards
    .map(
      (row) => `<div class="horizon-row"><span>${row.minutes} د</span><div class="horizon-meter"><i style="width:${row.probability * 100}%"></i></div><strong>${formatProbability(row.probability)}</strong></div>`,
    )
    .join("");
}

function renderQuality(features, readiness) {
  const freshnessSeconds = state.lastMessageAt ? (Date.now() - state.lastMessageAt) / 1000 : Infinity;
  $("dataFreshness").textContent = freshnessSeconds < 3 ? "حيّة" : freshnessSeconds < 10 ? "متأخرة قليلًا" : "غير حديثة";
  const completed = [state.books.xrp, state.books.btc, state.ticks.xrp.length > 20, state.ticks.btc.length > 20, state.trades.length > 20].filter(Boolean).length;
  $("featureCoverage").textContent = `${completed}/5 مصادر أساسية`;
  $("driftState").textContent = "سيُفعّل بعد خط أساس تاريخي";
  $("calibrationState").textContent = readiness.evaluated >= 100 ? "قيد القياس" : "عينات غير كافية";
}

function renderLedger() {
  const body = $("ledgerBody");
  if (!state.ledger.length) {
    body.innerHTML = '<tr><td colspan="7" class="empty">لم تُسجل توقعات بعد</td></tr>';
    return;
  }
  body.innerHTML = state.ledger
    .slice(0, 100)
    .map((row) => {
      const resultClass = row.outcome === "UP_10" ? "positive" : row.outcome === "DOWN_10" ? "negative" : row.outcome === "NO_EVENT" ? "neutral" : "";
      return `<tr>
        <td>${formatTime(row.issuedAt)}</td>
        <td dir="ltr">${formatPrice(row.referencePrice)}</td>
        <td>${formatProbability(row.upProbability)}</td>
        <td>${formatProbability(row.downProbability)}</td>
        <td>${row.decision}</td>
        <td>${row.status === "pending" ? "قيد المراقبة" : "مقيّم"}</td>
        <td class="${resultClass}">${row.outcome || "—"}</td>
      </tr>`;
    })
    .join("");
}

$("clearLedger").addEventListener("click", () => {
  state.ledger = [];
  saveLedger();
  renderLedger();
});

connect();
setInterval(() => {
  evaluateLedger();
  render();
}, 1000);
render();
