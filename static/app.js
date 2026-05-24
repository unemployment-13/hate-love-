const state = {
  conversationId: null,
  senders: [],
  messages: [],
  errors: [],
  warnings: [],
  metrics: null,
  report: null,
  selectedSender: null,
};

const els = {
  tabs: document.querySelectorAll(".tab"),
  screenshotForm: document.querySelector("#screenshot-form"),
  screenshotFiles: document.querySelector("#screenshot-files"),
  screenshotLabel: document.querySelector("#screenshot-label"),
  screenshotButton: document.querySelector("#screenshot-button"),
  fileForm: document.querySelector("#file-form"),
  fileInput: document.querySelector("#chat-file"),
  fileLabel: document.querySelector("#file-label"),
  fileButton: document.querySelector("#file-button"),
  timeField: document.querySelector("#time-field"),
  senderField: document.querySelector("#sender-field"),
  textField: document.querySelector("#text-field"),
  status: document.querySelector("#status"),
  identityPanel: document.querySelector("#identity-panel"),
  senderCount: document.querySelector("#sender-count"),
  senderOptions: document.querySelector("#sender-options"),
  identityWarning: document.querySelector("#identity-warning"),
  exportPanel: document.querySelector("#export-panel"),
  exportTxt: document.querySelector("#export-txt"),
  exportCsv: document.querySelector("#export-csv"),
  exportHtml: document.querySelector("#export-html"),
  summaryPanel: document.querySelector("#summary-panel"),
  reportPanel: document.querySelector("#report-panel"),
  reportConfidence: document.querySelector("#report-confidence"),
  reportOverview: document.querySelector("#report-overview"),
  reportClaims: document.querySelector("#report-claims"),
  reviewPanel: document.querySelector("#review-panel"),
  messagesBody: document.querySelector("#messages-body"),
  messageCount: document.querySelector("#message-count"),
  saveMessages: document.querySelector("#save-messages"),
  deleteSelected: document.querySelector("#delete-selected"),
  mergeSelected: document.querySelector("#merge-selected"),
  chartsPanel: document.querySelector("#charts-panel"),
  issuesPanel: document.querySelector("#issues-panel"),
  issueCount: document.querySelector("#issue-count"),
  issuesList: document.querySelector("#issues-list"),
  dailyChart: document.querySelector("#daily-chart"),
  roleChart: document.querySelector("#role-chart"),
  replyChart: document.querySelector("#reply-chart"),
};

els.tabs.forEach((tab) => {
  tab.addEventListener("click", () => {
    els.tabs.forEach((item) => item.classList.remove("active"));
    tab.classList.add("active");
    const active = tab.dataset.tab;
    els.screenshotForm.classList.toggle("hidden", active !== "screenshots");
    els.fileForm.classList.toggle("hidden", active !== "files");
  });
});

els.screenshotFiles.addEventListener("change", () => {
  const count = els.screenshotFiles.files.length;
  els.screenshotLabel.textContent = count ? `已选择 ${count} 张截图` : "选择一张或多张聊天截图";
});

els.fileInput.addEventListener("change", () => {
  const file = els.fileInput.files[0];
  els.fileLabel.textContent = file ? file.name : "选择第三方导出文件";
});

els.screenshotForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const files = Array.from(els.screenshotFiles.files || []);
  if (!files.length) {
    setStatus("请先选择聊天截图。", true);
    return;
  }
  const formData = new FormData();
  files.forEach((file) => formData.append("screenshots", file));
  await importConversation("/api/import/screenshots", formData, els.screenshotButton, "正在本地 OCR 识别截图...");
});

els.fileForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const file = els.fileInput.files[0];
  if (!file) {
    setStatus("请先选择第三方导出文件。", true);
    return;
  }
  const formData = new FormData();
  formData.append("file", file);
  if (els.timeField.value.trim()) formData.append("time_field", els.timeField.value.trim());
  if (els.senderField.value.trim()) formData.append("sender_field", els.senderField.value.trim());
  if (els.textField.value.trim()) formData.append("text_field", els.textField.value.trim());
  await importConversation("/api/import/files", formData, els.fileButton, "正在导入第三方文件...");
});

els.saveMessages.addEventListener("click", saveReviewEdits);
els.deleteSelected.addEventListener("click", deleteSelectedMessages);
els.mergeSelected.addEventListener("click", mergeSelectedMessages);

async function importConversation(url, formData, button, loadingText) {
  setStatus(loadingText);
  button.disabled = true;
  try {
    const response = await fetch(url, { method: "POST", body: formData });
    const data = await readJson(response);
    loadConversationPayload(data);
    setStatus(`导入完成：${data.message_count} 条消息，${data.needs_review_count || 0} 条建议校对。`);
  } catch (error) {
    setStatus(error.message, true);
  } finally {
    button.disabled = false;
  }
}

function loadConversationPayload(data) {
  state.conversationId = data.conversation_id;
  state.senders = data.senders || [];
  state.messages = data.messages || [];
  state.errors = data.errors || [];
  state.warnings = data.warnings || [];
  state.metrics = data.metrics || null;
  state.report = data.report || null;
  state.selectedSender = data.self_sender || null;
  renderAll();
}

async function readJson(response) {
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const message = data.error?.message || "请求失败，请检查文件或稍后重试。";
    throw new Error(message);
  }
  return data;
}

function setStatus(message, isError = false) {
  els.status.textContent = message;
  els.status.classList.toggle("error", isError);
}

function renderAll() {
  renderSenderOptions();
  renderMessages();
  renderIssues();
  renderExportLinks();
  if (state.metrics) renderSummary();
  else els.summaryPanel.classList.add("hidden");
  if (state.report) renderReport();
  else els.reportPanel.classList.add("hidden");
  if (state.metrics) renderCharts();
  else els.chartsPanel.classList.add("hidden");
}

function renderSenderOptions() {
  els.identityPanel.classList.remove("hidden");
  els.senderCount.textContent = `${state.senders.length} 个发送者`;
  els.senderOptions.innerHTML = "";

  state.senders.forEach((sender) => {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = sender;
    button.classList.toggle("active", sender === state.selectedSender);
    button.addEventListener("click", () => setSelfSender(sender));
    els.senderOptions.appendChild(button);
  });

  if (state.senders.length < 2) {
    els.identityWarning.textContent = "只识别到一个发送者，仍可校对和导出，但双方分析会有限。";
    els.identityWarning.classList.remove("hidden");
  } else {
    els.identityWarning.classList.add("hidden");
  }
}

async function setSelfSender(sender) {
  if (!state.conversationId) return;
  setStatus("正在设置身份并生成报告...");
  try {
    const response = await fetch(`/api/conversations/${state.conversationId}/role`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ self_sender: sender }),
    });
    const data = await readJson(response);
    loadConversationPayload(data);
    setStatus("身份已设置，报告已更新。");
  } catch (error) {
    setStatus(error.message, true);
  }
}

function renderExportLinks() {
  if (!state.conversationId) return;
  els.exportPanel.classList.remove("hidden");
  els.exportTxt.href = `/api/conversations/${state.conversationId}/export.txt`;
  els.exportCsv.href = `/api/conversations/${state.conversationId}/export.csv`;
  els.exportHtml.href = `/api/conversations/${state.conversationId}/export.html`;
}

function renderMessages() {
  els.reviewPanel.classList.remove("hidden");
  const maxRows = 800;
  const rows = state.messages.slice(0, maxRows);
  els.messageCount.textContent =
    state.messages.length > maxRows
      ? `${state.messages.length} 条，显示前 ${maxRows} 条`
      : `${state.messages.length} 条`;
  els.messagesBody.innerHTML = "";

  rows.forEach((message) => {
    const tr = document.createElement("tr");
    tr.dataset.id = message.id;
    tr.classList.toggle("needs-review", Boolean(message.needs_review));
    tr.innerHTML = `
      <td class="select-col"><input type="checkbox" class="row-select" /></td>
      <td><input class="cell-input timestamp" value="${escapeAttr(formatTime(message.timestamp))}" placeholder="无时间" /></td>
      <td><input class="cell-input sender" value="${escapeAttr(message.sender)}" /></td>
      <td>
        <select class="cell-input sender-role">
          <option value="self" ${message.sender_role === "self" ? "selected" : ""}>我</option>
          <option value="other" ${message.sender_role === "other" ? "selected" : ""}>对方</option>
          <option value="unknown" ${message.sender_role === "unknown" ? "selected" : ""}>未知</option>
        </select>
      </td>
      <td>
        <select class="cell-input message-type">
          ${["text", "voice", "image", "system", "empty"].map((type) => `<option value="${type}" ${message.message_type === type ? "selected" : ""}>${typeName(type)}</option>`).join("")}
        </select>
      </td>
      <td><textarea class="cell-text text">${escapeHtml(message.text)}</textarea></td>
      <td>${Math.round((message.confidence || 0) * 100)}%${message.needs_review ? "<br><span class='review-tag'>需校对</span>" : ""}</td>
    `;
    els.messagesBody.appendChild(tr);
  });
}

async function saveReviewEdits() {
  if (!state.conversationId) return;
  const updates = Array.from(els.messagesBody.querySelectorAll("tr")).map((row) => ({
    id: row.dataset.id,
    timestamp: row.querySelector(".timestamp").value,
    sender: row.querySelector(".sender").value,
    sender_role: row.querySelector(".sender-role").value,
    message_type: row.querySelector(".message-type").value,
    text: row.querySelector(".text").value,
    needs_review: false,
  }));
  setStatus("正在保存校对结果...");
  try {
    const response = await fetch(`/api/conversations/${state.conversationId}/messages/bulk-update`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ messages: updates }),
    });
    const data = await readJson(response);
    loadConversationPayload(data);
    setStatus("校对结果已保存。");
  } catch (error) {
    setStatus(error.message, true);
  }
}

async function deleteSelectedMessages() {
  const ids = selectedIds();
  if (!ids.length) {
    setStatus("请先选择要删除的消息。", true);
    return;
  }
  await postMessageAction("delete", { ids }, "已删除选中消息。");
}

async function mergeSelectedMessages() {
  const ids = selectedIds();
  if (ids.length < 2) {
    setStatus("至少选择两条消息才能合并。", true);
    return;
  }
  await postMessageAction("merge", { ids }, "已合并选中消息。");
}

async function postMessageAction(action, payload, successText) {
  if (!state.conversationId) return;
  setStatus("正在更新消息...");
  try {
    const response = await fetch(`/api/conversations/${state.conversationId}/messages/${action}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await readJson(response);
    loadConversationPayload(data);
    setStatus(successText);
  } catch (error) {
    setStatus(error.message, true);
  }
}

function selectedIds() {
  return Array.from(els.messagesBody.querySelectorAll("tr"))
    .filter((row) => row.querySelector(".row-select").checked)
    .map((row) => row.dataset.id);
}

function renderIssues() {
  const issues = [...state.errors, ...state.warnings];
  if (!issues.length) {
    els.issuesPanel.classList.add("hidden");
    return;
  }
  els.issuesPanel.classList.remove("hidden");
  els.issueCount.textContent = `${issues.length} 条`;
  els.issuesList.innerHTML = "";
  issues.slice(0, 80).forEach((item) => {
    const div = document.createElement("div");
    div.className = "issue-item";
    const raw = typeof item.raw === "string" ? item.raw : JSON.stringify(item.raw, null, 0);
    div.textContent = `第 ${item.line} 项：${item.reason}${raw ? `。${raw}` : ""}`;
    els.issuesList.appendChild(div);
  });
}

function renderSummary() {
  const metrics = state.metrics;
  els.summaryPanel.classList.remove("hidden");
  const otherRatio =
    metrics.other_start_ratio === null ? "暂无" : `${Math.round(metrics.other_start_ratio * 100)}%`;
  const cards = [
    ["总消息数", metrics.total_messages, `需校对 ${metrics.needs_review_count} 条`],
    ["我的消息", metrics.role_counts.self, `${metrics.role_char_counts.self} 字`],
    ["对方消息", metrics.role_counts.other, `${metrics.role_char_counts.other} 字`],
    ["互动开启", metrics.conversation_starts, `对方开启 ${otherRatio}`],
    ["平均回复", formatDuration(metrics.avg_reply_interval_seconds), `中位数 ${formatDuration(metrics.median_reply_interval_seconds)}`],
    ["长间隔", metrics.long_reply_interval_count, "超过 7 天不计入平均值"],
    ["我的均长", metrics.role_avg_lengths.self, "字 / 条"],
    ["对方均长", metrics.role_avg_lengths.other, "字 / 条"],
  ];
  els.summaryPanel.innerHTML = cards
    .map(
      ([label, value, detail]) => `
        <article class="stat-card">
          <div class="label">${escapeHtml(label)}</div>
          <div class="value">${escapeHtml(String(value))}</div>
          <div class="detail">${escapeHtml(detail)}</div>
        </article>
      `
    )
    .join("");
}

function renderReport() {
  const report = state.report;
  els.reportPanel.classList.remove("hidden");
  els.reportConfidence.textContent = `置信度：${confidenceName(report.confidence)}`;
  const scores = report.scores || {};
  els.reportOverview.innerHTML = `
    <div class="trend-card">
      <span>关系趋势</span>
      <strong>${trendName(report.trend)}</strong>
      <small>${escapeHtml(report.disclaimer || "")}</small>
    </div>
    ${scoreCard("互动热度", scores.interaction_heat)}
    ${scoreCard("回应投入", scores.response_investment)}
    ${scoreCard("互动对等", scores.reciprocity)}
    ${scoreCard("关系推进", scores.relationship_progress)}
    ${scoreCard("风险信号", scores.risk)}
  `;

  const byId = new Map(state.messages.map((message) => [message.id, message]));
  els.reportClaims.innerHTML = (report.claims || [])
    .map((claim) => `
      <article class="claim-card">
        <h3>${escapeHtml(claim.title)}</h3>
        <p>${escapeHtml(claim.summary)}</p>
        <h4>支持证据</h4>
        <ul>${evidenceList(claim.evidence, byId)}</ul>
        <h4>反向证据</h4>
        <ul>${evidenceList(claim.counter_evidence, byId)}</ul>
      </article>
    `)
    .join("");
}

function scoreCard(label, value) {
  return `
    <div class="score-card">
      <span>${escapeHtml(label)}</span>
      <strong>${value ?? "暂无"}</strong>
    </div>
  `;
}

function evidenceList(ids, byId) {
  const items = (ids || [])
    .map((id) => byId.get(id))
    .filter(Boolean)
    .map((message) => `<li><b>${escapeHtml(message.sender)}</b>：${escapeHtml(message.text)}</li>`);
  return items.length ? items.join("") : "<li>暂无</li>";
}

function renderCharts() {
  els.chartsPanel.classList.remove("hidden");
  drawDailyChart(els.dailyChart, state.metrics.daily_message_counts);
  drawRoleChart(els.roleChart, state.metrics.role_counts);
  drawReplyChart(els.replyChart, state.metrics.reply_intervals);
}

function setupCanvas(canvas) {
  const rect = canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.max(320, Math.floor(rect.width * dpr));
  canvas.height = Math.floor(Number(canvas.getAttribute("height")) * dpr);
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return { ctx, width: canvas.width / dpr, height: canvas.height / dpr };
}

function clearChart(ctx, width, height) {
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, width, height);
}

function drawEmpty(ctx, width, height, text) {
  clearChart(ctx, width, height);
  ctx.fillStyle = "#64706d";
  ctx.font = "14px sans-serif";
  ctx.textAlign = "center";
  ctx.fillText(text, width / 2, height / 2);
}

function drawAxes(ctx, width, height, padding) {
  ctx.strokeStyle = "#dce2de";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(padding.left, padding.top);
  ctx.lineTo(padding.left, height - padding.bottom);
  ctx.lineTo(width - padding.right, height - padding.bottom);
  ctx.stroke();
}

function drawDailyChart(canvas, daily) {
  const { ctx, width, height } = setupCanvas(canvas);
  if (!daily.length) return drawEmpty(ctx, width, height, "没有可用于趋势图的时间数据");
  clearChart(ctx, width, height);
  const padding = { top: 18, right: 18, bottom: 38, left: 44 };
  drawAxes(ctx, width, height, padding);
  const max = Math.max(1, ...daily.map((day) => day.total));
  const plotW = width - padding.left - padding.right;
  const plotH = height - padding.top - padding.bottom;
  const xStep = daily.length > 1 ? plotW / (daily.length - 1) : 0;
  ctx.strokeStyle = "#0f766e";
  ctx.lineWidth = 2;
  ctx.beginPath();
  daily.forEach((day, index) => {
    const x = daily.length > 1 ? padding.left + xStep * index : padding.left + plotW / 2;
    const y = padding.top + plotH - (day.total / max) * plotH;
    if (index === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.stroke();
  daily.forEach((day, index) => {
    const x = daily.length > 1 ? padding.left + xStep * index : padding.left + plotW / 2;
    const y = padding.top + plotH - (day.total / max) * plotH;
    ctx.fillStyle = "#0f766e";
    ctx.beginPath();
    ctx.arc(x, y, 4, 0, Math.PI * 2);
    ctx.fill();
  });
  ctx.fillStyle = "#64706d";
  ctx.font = "12px sans-serif";
  ctx.textAlign = "left";
  ctx.fillText(daily[0].date.slice(5), padding.left, height - 12);
  ctx.textAlign = "right";
  ctx.fillText(daily[daily.length - 1].date.slice(5), width - padding.right, height - 12);
}

function drawRoleChart(canvas, counts) {
  const { ctx, width, height } = setupCanvas(canvas);
  clearChart(ctx, width, height);
  const data = [
    ["我", counts.self || 0, "#2563eb"],
    ["对方", counts.other || 0, "#0f766e"],
    ["未知", counts.unknown || 0, "#9ca3af"],
  ];
  const max = Math.max(1, ...data.map((item) => item[1]));
  const padding = { top: 18, right: 18, bottom: 38, left: 42 };
  drawAxes(ctx, width, height, padding);
  const plotW = width - padding.left - padding.right;
  const plotH = height - padding.top - padding.bottom;
  const barW = Math.min(72, plotW / data.length - 18);
  data.forEach(([label, value, color], index) => {
    const x = padding.left + (plotW / data.length) * index + (plotW / data.length - barW) / 2;
    const barH = (value / max) * plotH;
    const y = padding.top + plotH - barH;
    ctx.fillStyle = color;
    ctx.fillRect(x, y, barW, barH);
    ctx.fillStyle = "#18201f";
    ctx.font = "13px sans-serif";
    ctx.textAlign = "center";
    ctx.fillText(String(value), x + barW / 2, y - 6);
    ctx.fillStyle = "#64706d";
    ctx.fillText(label, x + barW / 2, height - 12);
  });
}

function drawReplyChart(canvas, intervals) {
  const { ctx, width, height } = setupCanvas(canvas);
  const included = intervals.filter((item) => !item.excluded_from_average);
  if (!included.length) return drawEmpty(ctx, width, height, "没有足够的发送者切换记录");
  clearChart(ctx, width, height);
  const padding = { top: 18, right: 18, bottom: 38, left: 50 };
  drawAxes(ctx, width, height, padding);
  const max = Math.max(1, ...included.map((item) => item.hours));
  const plotW = width - padding.left - padding.right;
  const plotH = height - padding.top - padding.bottom;
  const xStep = included.length > 1 ? plotW / (included.length - 1) : 0;
  ctx.strokeStyle = "#b45309";
  ctx.lineWidth = 2;
  ctx.beginPath();
  included.forEach((item, index) => {
    const x = included.length > 1 ? padding.left + xStep * index : padding.left + plotW / 2;
    const y = padding.top + plotH - (item.hours / max) * plotH;
    if (index === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.stroke();
}

function trendName(value) {
  return {
    warming: "升温",
    stable: "稳定",
    cooling: "降温",
    one_sided: "单方投入",
    insufficient_data: "数据不足",
  }[value] || value;
}

function confidenceName(value) {
  return { high: "高", medium: "中", low: "低" }[value] || value;
}

function typeName(value) {
  return { text: "文本", voice: "语音", image: "图片/表情", system: "系统", empty: "空" }[value] || value;
}

function formatTime(value) {
  if (!value) return "";
  return value.replace("T", " ");
}

function formatDuration(seconds) {
  if (seconds === null || seconds === undefined) return "暂无";
  if (seconds < 60) return `${Math.round(seconds)} 秒`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} 分钟`;
  if (seconds < 86400) return `${Math.round((seconds / 3600) * 10) / 10} 小时`;
  return `${Math.round((seconds / 86400) * 10) / 10} 天`;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function escapeAttr(value) {
  return escapeHtml(value).replaceAll("\n", " ");
}

window.addEventListener("resize", () => {
  if (state.metrics) renderCharts();
});
