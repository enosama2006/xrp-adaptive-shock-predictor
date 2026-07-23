const byId = (id) => document.getElementById(id);

const REASONS = {
  no_data_integrity_report: "لم يُنشأ تقرير سلامة البيانات بعد",
  price_integrity_and_coverage_passed: "اجتازت البيانات اختبارات البنية والتغطية",
  minute_coverage_below_threshold: "البنية سليمة لكن تغطية الدقائق أقل من الحد",
  structural_price_integrity_failed: "فشل بنيوي في بيانات الأسعار",
  no_history_expansion_requested: "لم تُطلب توسعة تاريخية مستقلة",
  history_expansion_checkpointed_incomplete: "توسعة محفوظة جزئيًا وقابلة للاستئناف",
  history_expansion_completed: "اكتملت التوسعة التاريخية المطلوبة",
};

function number(value) {
  return Number(value || 0).toLocaleString("ar-SA");
}

function percent(value) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? `${(parsed * 100).toFixed(2)}%` : "—";
}

function dateTime(value) {
  if (!value) return "—";
  if (typeof value === "number") return new Date(value).toLocaleString("ar-SA");
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? new Date(parsed).toLocaleString("ar-SA") : String(value);
}

function shortHash(value) {
  if (!value) return "—";
  const text = String(value);
  return text.length > 24 ? `${text.slice(0, 12)}…${text.slice(-8)}` : text;
}

function renderGovernance(payload) {
  const integrity = payload.data_integrity || {};
  const expansion = payload.history_expansion || {};
  const integrityStatus = integrity.status || "WAIT";
  const expansionStatus = expansion.status || "IDLE";

  byId("integrityStatus").textContent = integrityStatus;
  byId("integrityStatus").title = REASONS[integrity.reason] || integrity.reason || "";
  byId("integrityCoverage").textContent = percent(integrity.coverage_ratio);
  byId("integrityMissing").textContent = number(integrity.missing_minutes);
  byId("integrityFingerprint").textContent = shortHash(integrity.dataset_fingerprint_sha256);
  byId("integrityFingerprint").title = integrity.dataset_fingerprint_sha256 || "";

  byId("historyExpansionStatus").textContent = expansionStatus;
  byId("historyExpansionStatus").title = REASONS[expansion.reason] || expansion.reason || "";
  byId("historyExpansionProgress").textContent = percent(expansion.progress_fraction);
  byId("historyExpansionRows").textContent = number(expansion.accepted_rows);
  byId("historyExpansionUpdated").textContent = dateTime(
    expansion.updated_at || expansion.state_updated_at_ms,
  );

  const failed = integrityStatus === "FAIL";
  const warning = integrityStatus === "WARN" || expansionStatus === "WAIT";
  byId("governanceState").textContent = failed ? "FAIL" : warning ? "MONITORING" : "READY";
  byId("governanceState").className = failed || warning ? "wait" : "";
}

async function refreshGovernance() {
  try {
    const response = await fetch("/api/governance", { cache: "no-store" });
    if (!response.ok) throw new Error("governance API unavailable");
    renderGovernance(await response.json());
  } catch (error) {
    byId("governanceState").textContent = "UNAVAILABLE";
    byId("governanceState").className = "wait";
  }
}

refreshGovernance();
setInterval(refreshGovernance, 5_000);
