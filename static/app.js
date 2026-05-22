const state = {
  conversationId: null,
  senders: [],
  messages: [],
  errors: [],
  metrics: null,
  selectedSender: null,
};

const els = {
  form: document.querySelector("#upload-form"),
  fileInput: document.querySelector("#chat-file"),
  fileLabel: document.querySelector("#file-label"),
  uploadButton: document.querySelector("#upload-button"),
  status: document.querySelector("#status"),
  identityPanel: document.querySelector("#identity-panel"),
  senderCount: document.querySelector("#sender-count"),
  senderOptions: document.querySelector("#sender-options"),
  identityWarning: document.querySelector("#identity-warning"),
  summaryPanel: document.querySelector("#summary-panel"),
  chartsPanel: document.querySelector("#charts-panel"),
  messagesPanel: document.querySelector("#messages-panel"),
  messagesBody: document.querySelector("#messages-body"),
  messageCount: document.querySelector("#message-count"),
  errorsPanel: document.querySelector("#errors-panel"),
  errorCount: document.querySelector("#error-count"),
  errorsList: document.querySelector("#errors-list"),
  dailyChart: document.querySelector("#daily-chart"),
  roleChart: document.querySelector("#role-chart"),
  replyChart: document.querySelector("#reply-chart"),
};

els.fileInput.addEventListener("change", () => {
  const file = els.fileInput.files[0];
  els.fileLabel.textContent = file ? file.name : "选择聊天记录文件";
});

els.form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const file = els.fileInput.files[0];
  if (!file) {
    setStatus("请先选择一个 .txt 或 .csv 文件。", true);
    return;
  }

  const formData = new FormData();
  formData.append("file", file);
  setStatus("正在解析文件...");
  els.uploadButton.disabled = true;

  try {
    const response = await fetch("/api/import", {
      method: "POST",
      body: formData,
    });
    const data = await readJson(response);
    state.conversationId = data.conversation_id;
    state.senders = data.senders || [];
    state.messages = data.messages || [];
    state.errors = data.errors || [];
    state.metrics = null;
    state.selectedSender = null;

    setStatus(`解析完成：${data.message_count} 条可用消息。`);
    renderSenderOptions();
    renderMessages();
    renderErrors();
    hideAnalysis();
  } catch (error) {
    setStatus(error.message, true);
  } finally {
    els.uploadButton.disabled = false;
  }
});

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
    els.identityWarning.textContent = "只识别到一个发送者，仍可预览消息，但双方对比统计会有限。";
    els.identityWarning.classList.remove("hidden");
  } else {
    els.identityWarning.classList.add("hidden");
  }
}

async function setSelfSender(sender) {
  if (!state.conversationId) return;
  setStatus("正在设置身份并计算指标...");

  try {
    const response = await fetch(`/api/conversations/${state.conversationId}/role`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ self_sender: sender }),
    });
    const data = await readJson(response);
    state.selectedSender = data.self_sender;
    state.messages = data.messages || [];
    state.metrics = data.metrics;
    renderSenderOptions();
    renderMessages();
    renderSummary();
    renderCharts();
    setStatus("身份已设置，基础统计已更新。");
  } catch (error) {
    setStatus(error.message, true);
  }
}

function hideAnalysis() {
  els.summaryPanel.classList.add("hidden");
  els.chartsPanel.classList.add("hidden");
}

function renderMessages() {
  els.messagesPanel.classList.remove("hidden");
  const maxRows = 500;
  const rows = state.messages.slice(0, maxRows);
  els.messageCount.textContent =
    state.messages.length > maxRows
      ? `${state.messages.length} 条，显示前 ${maxRows} 条`
      : `${state.messages.length} 条`;
  els.messagesBody.innerHTML = "";

  rows.forEach((message) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${escapeHtml(formatTime(message.timestamp))}</td>
      <td>${escapeHtml(message.sender)}</td>
      <td><span class="role-pill ${escapeHtml(message.sender_role)}">${escapeHtml(roleName(message.sender_role))}</span></td>
      <td>${escapeHtml(message.text)}</td>
    `;
    els.messagesBody.appendChild(tr);
  });
}

function renderErrors() {
  if (!state.errors.length) {
    els.errorsPanel.classList.add("hidden");
    return;
  }
  els.errorsPanel.classList.remove("hidden");
  els.errorCount.textContent = `${state.errors.length} 条`;
  els.errorsList.innerHTML = "";
  state.errors.slice(0, 50).forEach((item) => {
    const div = document.createElement("div");
    div.className = "error-item";
    const raw =
      typeof item.raw === "string" ? item.raw : JSON.stringify(item.raw, null, 0);
    div.textContent = `第 ${item.line} 行：${item.reason}。${raw}`;
    els.errorsList.appendChild(div);
  });
}

function renderSummary() {
  if (!state.metrics) return;
  const metrics = state.metrics;
  els.summaryPanel.classList.remove("hidden");
  const otherRatio =
    metrics.other_start_ratio === null
      ? "暂无"
      : `${Math.round(metrics.other_start_ratio * 100)}%`;
  const avgReply = formatDuration(metrics.avg_reply_interval_seconds);
  const medianReply = formatDuration(metrics.median_reply_interval_seconds);

  const cards = [
    ["总消息数", metrics.total_messages, `无时间消息 ${metrics.untimed_message_count} 条`],
    ["我的消息", metrics.role_counts.self, `${metrics.role_char_counts.self} 字`],
    ["对方消息", metrics.role_counts.other, `${metrics.role_char_counts.other} 字`],
    ["对方开启比例", otherRatio, `${metrics.conversation_starts} 次对话开启`],
    ["平均回复间隔", avgReply, `中位数 ${medianReply}`],
    ["长间隔回复", metrics.long_reply_interval_count, "超过 7 天，不计入平均值"],
    ["我的平均长度", metrics.role_avg_lengths.self, "字 / 条"],
    ["对方平均长度", metrics.role_avg_lengths.other, "字 / 条"],
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

function renderCharts() {
  if (!state.metrics) return;
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
  if (!daily.length) {
    drawEmpty(ctx, width, height, "没有可用于趋势图的时间数据");
    return;
  }
  clearChart(ctx, width, height);
  const padding = { top: 18, right: 18, bottom: 38, left: 44 };
  drawAxes(ctx, width, height, padding);
  const values = daily.map((day) => day.total);
  const max = Math.max(1, ...values);
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
  ctx.textAlign = "left";
  ctx.fillText(String(max), 8, padding.top + 4);
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
  if (!included.length) {
    drawEmpty(ctx, width, height, "没有足够的发送者切换记录");
    return;
  }
  clearChart(ctx, width, height);
  const padding = { top: 18, right: 18, bottom: 38, left: 50 };
  drawAxes(ctx, width, height, padding);
  const values = included.map((item) => item.hours);
  const max = Math.max(1, ...values);
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

  included.forEach((item, index) => {
    const x = included.length > 1 ? padding.left + xStep * index : padding.left + plotW / 2;
    const y = padding.top + plotH - (item.hours / max) * plotH;
    ctx.fillStyle = item.to_role === "other" ? "#0f766e" : "#2563eb";
    ctx.beginPath();
    ctx.arc(x, y, 3.5, 0, Math.PI * 2);
    ctx.fill();
  });

  ctx.fillStyle = "#64706d";
  ctx.font = "12px sans-serif";
  ctx.textAlign = "left";
  ctx.fillText(`${Math.round(max)} 小时`, 8, padding.top + 4);
  ctx.fillText(included[0].date.slice(5), padding.left, height - 12);
  ctx.textAlign = "right";
  ctx.fillText(included[included.length - 1].date.slice(5), width - padding.right, height - 12);
}

function roleName(role) {
  if (role === "self") return "我";
  if (role === "other") return "对方";
  return "未知";
}

function formatTime(value) {
  if (!value) return "无时间";
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

window.addEventListener("resize", () => {
  if (state.metrics) renderCharts();
});
