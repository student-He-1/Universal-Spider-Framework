/* ================================================================
   万能爬虫可视化控制台 - 前端逻辑
   ================================================================ */

let socket = null;
let currentTaskId = null;

// ================================================================
// 初始化
// ================================================================
document.addEventListener("DOMContentLoaded", () => {
    initWebSocket();
    loadTemplates();
    initPaginationToggle();
    initFieldEditor();
    initButtons();
    initProbe();
    // 状态栏进场提示
    const bar = document.getElementById("status-bar");
    if (bar && bar.querySelector(".status-idle")) {
        bar.innerHTML = `<span class="status-idle">就绪 · 等待配置</span>`;
    }
});

// ================================================================
// WebSocket 连接
// ================================================================
function initWebSocket() {
    socket = io();

    socket.on("connect", () => {
        setWsStatus(true);
        console.log("[WebSocket] 已连接");
    });

    socket.on("disconnect", () => {
        setWsStatus(false);
        console.log("[WebSocket] 已断开");
    });

    // 实时日志
    socket.on("crawl_log", (data) => {
        if (data.task_id === currentTaskId) {
            appendLog(data.text);
            // 解析"找到 X 条"实时更新统计
            const match = data.text.match(/找到\s+(\d+)\s+条/);
            if (match) {
                taskItemCount = parseInt(match[1]);
                const elapsed = formatElapsed(Date.now() - taskStartTime);
                updateTaskStats(`<span class="stat-value-mono">${taskItemCount} 条</span> <span class="api-hint">· ${taskFieldCount} 字段 · ${elapsed}</span>`);
            }
        }
    });

    // 爬取完成
    socket.on("crawl_done", (data) => {
        if (data.task_id === currentTaskId) {
            if (data.status === "completed") {
                const elapsed = formatElapsed(Date.now() - taskStartTime);
                setStatus("completed", `爬取完成，共 ${data.count} 条`);
                updateTaskStats(`<span class="stat-value-mono">${data.count} 条</span> <span class="api-hint">· ${taskFieldCount} 字段 · ${elapsed}</span>`);
                loadResult(data.task_id);
            } else {
                setStatus("failed", `爬取失败：${data.error || "未知错误"}`);
                updateTaskStats('<span class="status-failed">失败</span>');
            }
            document.getElementById("start-crawl").disabled = false;
        }
    });
}

function setWsStatus(connected) {
    const el = document.getElementById("ws-status");
    el.textContent = connected ? "已连接" : "未连接";
    el.className = "ws-status " + (connected ? "connected" : "disconnected");
}

// ================================================================
// 预设模板
// ================================================================
async function loadTemplates() {
    try {
        const resp = await fetch("/api/templates");
        const templates = await resp.json();
        const select = document.getElementById("template-select");
        templates.forEach(t => {
            const opt = document.createElement("option");
            opt.value = t.key;
            opt.textContent = t.name;
            select.appendChild(opt);
        });
    } catch (e) {
        console.error("加载模板失败:", e);
    }
}

async function loadTemplateConfig(key) {
    try {
        const resp = await fetch(`/api/templates/${key}`);
        const config = await resp.json();
        fillConfigForm(config);
        appendLog(`[模板] 已加载：${config.name}\n`, "log-highlight");
    } catch (e) {
        console.error("加载模板配置失败:", e);
    }
}

// ================================================================
// 翻页策略切换
// ================================================================
function initPaginationToggle() {
    const select = document.getElementById("cfg-pagination-type");
    select.addEventListener("change", updatePaginationFields);
    updatePaginationFields();
}

function updatePaginationFields() {
    const type = document.getElementById("cfg-pagination-type").value;

    // 隐藏所有翻页配置
    document.querySelectorAll(".pagination-config").forEach(el => el.classList.add("hidden"));
    document.getElementById("pg-first-url").classList.add("hidden");

    // 显示对应配置
    if (type === "url_pattern") {
        document.getElementById("pg-url-pattern").classList.remove("hidden");
    } else if (type === "next_button") {
        document.getElementById("pg-next-button").classList.remove("hidden");
        document.getElementById("pg-first-url").classList.remove("hidden");
    } else if (type === "infinite_scroll") {
        document.getElementById("pg-infinite-scroll").classList.remove("hidden");
        document.getElementById("pg-first-url").classList.remove("hidden");
    } else if (type === "none") {
        document.getElementById("pg-single-url").classList.remove("hidden");
    }
}

// ================================================================
// 字段编辑器
// ================================================================
function initFieldEditor() {
    document.getElementById("add-field").addEventListener("click", addFieldRow);
    // 默认添加3个字段行
    addFieldRow("标题", "h3 a::text");
    addFieldRow("链接", "h3 a::attr(href)");
    addFieldRow("价格", "span.price::text");
}

function addFieldRow(name = "", selector = "") {
    const container = document.getElementById("fields-container");
    const row = document.createElement("div");
    row.className = "field-row";
    row.innerHTML = `
        <input type="text" placeholder="列名" value="${name}" class="field-name">
        <input type="text" placeholder="选择器（如 div.title::text）" value="${selector}" class="field-selector">
        <button class="remove-field" title="删除">×</button>
    `;
    row.querySelector(".remove-field").addEventListener("click", () => row.remove());
    container.appendChild(row);
}

// ================================================================
// 按钮事件
// ================================================================
function initButtons() {
    document.getElementById("load-template").addEventListener("click", () => {
        const key = document.getElementById("template-select").value;
        if (key) loadTemplateConfig(key);
    });

    document.getElementById("start-crawl").addEventListener("click", startCrawl);

    document.getElementById("clear-log").addEventListener("click", () => {
        document.getElementById("log-panel").innerHTML = "";
    });
}

// ================================================================
// 站点探测器
// ================================================================
function initProbe() {
    const runBtn = document.getElementById("probe-run");
    const input = document.getElementById("probe-url");
    runBtn.addEventListener("click", runProbe);
    input.addEventListener("keydown", (e) => { if (e.key === "Enter") runProbe(); });
}

async function runProbe() {
    const url = document.getElementById("probe-url").value.trim();
    if (!url) { alert("请输入网址"); return; }
    const statusEl = document.getElementById("probe-status");
    const resultEl = document.getElementById("probe-result");
    statusEl.textContent = "探测中...";
    statusEl.className = "probe-status running";
    resultEl.classList.add("hidden");

    try {
        const resp = await fetch("/api/probe", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ url }),
        });
        const data = await resp.json();

        if (!data.ok) {
            statusEl.textContent = "探测失败";
            statusEl.className = "probe-status failed";
            resultEl.innerHTML = `<div class="probe-row">⚠ ${data.error || "未知错误"}</div>`;
            resultEl.classList.remove("hidden");
            return;
        }

        statusEl.textContent = "完成";
        statusEl.className = "probe-status success";

        const items = (data.item_candidates || []).map(c =>
            `<div class="probe-item" data-sel="${c.selector}">
                <code>${c.selector}</code><span class="probe-count">${c.count} 个</span>
             </div>`).join("");
        const fields = Object.entries(data.fields_candidates || {}).map(([k, v]) =>
            `<span class="probe-chip" data-sel="${v}" data-name="${k}">${k} · ${v}</span>`).join("");

        resultEl.innerHTML = `
            <div class="probe-row"><b>标题：</b>${data.title || "—"}</div>
            <div class="probe-row"><b>渲染：</b><span class="tag tag-${data.render}">${data.render}</span>
                 <b>翻页：</b><span class="tag">${data.pagination}</span>
                 ${data.js_dynamic ? '<span class="tag tag-warn">JS动态</span>' : ''}
                 ${data.challenge ? '<span class="tag tag-danger">WAF/挑战</span>' : ''}
                 ${data.has_ssr_json ? '<span class="tag">SSR内嵌</span>' : ''}</div>
            ${data.pagination_note ? `<div class="probe-row probe-note">💡 ${data.pagination_note}</div>` : ''}
            <div class="probe-row"><b>内容容器候选：</b></div>
            <div class="probe-items">${items || '<span class="api-hint">未识别到常见容器</span>'}</div>
            <div class="probe-row"><b>字段候选（点击填入表单）：</b></div>
            <div class="probe-chips">${fields || '<span class="api-hint">未识别到字段</span>'}</div>`;
        resultEl.classList.remove("hidden");

        // 字段 chip 点击填表
        resultEl.querySelectorAll(".probe-chip").forEach(chip => {
            chip.addEventListener("click", () => {
                const name = chip.dataset.name, sel = chip.dataset.sel;
                addFieldRow(name, sel);
                chip.style.opacity = "0.5";
            });
        });
        // 容器候选点击填 item_selector
        resultEl.querySelectorAll(".probe-item").forEach(it => {
            it.addEventListener("click", () => {
                document.getElementById("cfg-item-selector").value = it.dataset.sel;
                resultEl.querySelectorAll(".probe-item").forEach(x => x.style.opacity = "0.5");
                it.style.opacity = "1";
            });
        });
        // 渲染方式回填
        if (data.render) document.getElementById("cfg-render").value = data.render;
    } catch (e) {
        statusEl.textContent = "探测异常";
        statusEl.className = "probe-status failed";
        resultEl.innerHTML = `<div class="probe-row">⚠ ${e.message}</div>`;
        resultEl.classList.remove("hidden");
    }
}

// ================================================================
// 当前任务实时统计
// ================================================================
let taskStartTime = null;
let taskItemCount = 0;
let taskFieldCount = 0;

function updateTaskStats(html) {
    const el = document.getElementById("task-stats-content");
    if (el) el.innerHTML = html;
}

function resetTaskStats() {
    taskStartTime = Date.now();
    taskItemCount = 0;
    taskFieldCount = 0;
    updateTaskStats('<span class="api-hint">准备中...</span>');
}

function formatElapsed(ms) {
    const s = Math.floor(ms / 1000);
    if (s < 60) return s + "s";
    return Math.floor(s / 60) + "m" + (s % 60) + "s";
}

// ================================================================
// 收集配置并提交
// ================================================================
function collectConfig() {
    const config = {
        name: document.getElementById("cfg-name").value.trim() || "未命名任务",
        render: document.getElementById("cfg-render").value,
        item_selector: document.getElementById("cfg-item-selector").value.trim(),
        delay: parseFloat(document.getElementById("cfg-delay").value) || 1.0,
    };

    // 页面等待时间（playwright用）
    const pageWait = parseInt(document.getElementById("cfg-page-wait").value);
    if (pageWait > 0) config.page_wait = pageWait;

    // 代理（可选）
    const proxy = document.getElementById("cfg-proxy").value.trim();
    if (proxy) config.proxy = proxy;

    // API 接口提取（可选，JSON）
    const apiText = document.getElementById("cfg-api").value.trim();
    if (apiText) {
        try {
            config.api = JSON.parse(apiText);
        } catch (e) {
            alert("API 配置不是合法的 JSON：" + e.message);
            return null;
        }
    }

    // 字段后处理（可选，JSON）
    const ppText = document.getElementById("cfg-postprocess").value.trim();
    if (ppText) {
        try {
            config.postprocess = JSON.parse(ppText);
        } catch (e) {
            alert("后处理配置不是合法的 JSON：" + e.message);
            return null;
        }
    }

    // 去重（可选：填了字段才启用）
    const dedupKey = document.getElementById("cfg-dedup-key").value.trim();
    if (dedupKey) config.dedup = { key: dedupKey };

    // 并发数（翻页）
    const conc = parseInt(document.getElementById("cfg-concurrency").value);
    if (conc >= 2) config.concurrency = conc;

    // 断点续爬
    if (document.getElementById("cfg-resume").checked) config.resume = true;

    // 自定义请求头（可选，JSON）
    const headersText = document.getElementById("cfg-headers").value.trim();
    if (headersText) {
        try {
            config.headers = JSON.parse(headersText);
        } catch (e) {
            alert("请求头不是合法的 JSON：" + e.message);
            return null;
        }
    }

    // 存储配置（可选，JSON）
    const storageText = document.getElementById("cfg-storage").value.trim();
    if (storageText) {
        try {
            config.storage = JSON.parse(storageText);
        } catch (e) {
            alert("存储配置不是合法的 JSON：" + e.message);
            return null;
        }
    }

    // 翻页配置
    const pgType = document.getElementById("cfg-pagination-type").value;
    const pagination = { type: pgType };

    if (pgType === "url_pattern") {
        pagination.url_pattern = document.getElementById("cfg-url-pattern").value.trim();
        pagination.start_page = parseInt(document.getElementById("cfg-start-page").value) || 1;
        pagination.max_pages = parseInt(document.getElementById("cfg-max-pages").value) || 1;
    } else if (pgType === "next_button") {
        pagination.next_selector = document.getElementById("cfg-next-selector").value.trim();
        pagination.disabled_marker = document.getElementById("cfg-disabled-marker").value.trim() || "disabled";
        pagination.max_pages = parseInt(document.getElementById("cfg-max-pages-nb").value) || 3;
        config.first_url = document.getElementById("cfg-first-url").value.trim();
    } else if (pgType === "infinite_scroll") {
        pagination.scroll_pause = parseFloat(document.getElementById("cfg-scroll-pause").value) || 1.5;
        config.first_url = document.getElementById("cfg-first-url").value.trim();
    } else if (pgType === "none") {
        config.first_url = document.getElementById("cfg-first-url-single").value.trim();
    }

    config.pagination = pagination;

    // 字段配置
    const fields = {};
    document.querySelectorAll(".field-row").forEach(row => {
        const name = row.querySelector(".field-name").value.trim();
        const selector = row.querySelector(".field-selector").value.trim();
        if (name && selector) fields[name] = selector;
    });
    config.fields = fields;

    return config;
}

function fillConfigForm(config) {
    document.getElementById("cfg-name").value = config.name || "";
    document.getElementById("cfg-render").value = config.render || "direct";
    document.getElementById("cfg-item-selector").value = config.item_selector || "";
    document.getElementById("cfg-delay").value = config.delay || 1.0;
    if (config.page_wait) document.getElementById("cfg-page-wait").value = config.page_wait;
    if (config.proxy) document.getElementById("cfg-proxy").value = config.proxy;
    if (config.api) document.getElementById("cfg-api").value = JSON.stringify(config.api, null, 2);
    if (config.postprocess) document.getElementById("cfg-postprocess").value = JSON.stringify(config.postprocess, null, 2);
    if (config.dedup && config.dedup.key) document.getElementById("cfg-dedup-key").value = config.dedup.key;
    if (config.concurrency) document.getElementById("cfg-concurrency").value = config.concurrency;
    if (config.resume) document.getElementById("cfg-resume").checked = true;
    if (config.headers) document.getElementById("cfg-headers").value = JSON.stringify(config.headers, null, 2);
    if (config.storage) document.getElementById("cfg-storage").value = JSON.stringify(config.storage);

    const pg = config.pagination || {};
    document.getElementById("cfg-pagination-type").value = pg.type || "url_pattern";
    updatePaginationFields();

    if (pg.type === "url_pattern") {
        document.getElementById("cfg-url-pattern").value = pg.url_pattern || "";
        document.getElementById("cfg-start-page").value = pg.start_page || 1;
        document.getElementById("cfg-max-pages").value = pg.max_pages || 1;
    } else if (pg.type === "next_button") {
        document.getElementById("cfg-next-selector").value = pg.next_selector || "";
        document.getElementById("cfg-disabled-marker").value = pg.disabled_marker || "disabled";
        document.getElementById("cfg-max-pages-nb").value = pg.max_pages || 3;
        document.getElementById("cfg-first-url").value = config.first_url || "";
    } else if (pg.type === "infinite_scroll") {
        document.getElementById("cfg-scroll-pause").value = pg.scroll_pause || 1.5;
        document.getElementById("cfg-first-url").value = config.first_url || "";
    } else if (pg.type === "none") {
        document.getElementById("cfg-first-url-single").value = config.first_url || "";
    }

    // 填充字段
    const container = document.getElementById("fields-container");
    container.innerHTML = "";
    const fields = config.fields || {};
    if (Object.keys(fields).length === 0) {
        addFieldRow();
    } else {
        for (const [name, selector] of Object.entries(fields)) {
            addFieldRow(name, selector);
        }
    }
}

// ================================================================
// 开始爬取
// ================================================================
async function startCrawl() {
    const config = collectConfig();
    if (!config) return; // 收集失败（如 API 配置非法）已提示

    // 基本校验
    if (!config.item_selector) {
        alert("请填写\"每条内容的盒子选择器\"");
        return;
    }
    if (Object.keys(config.fields).length === 0) {
        alert("请至少添加一个字段");
        return;
    }
    if (config.pagination.type === "url_pattern" && !config.pagination.url_pattern) {
        alert("请填写网址规律");
        return;
    }
    if ((config.pagination.type === "next_button" || config.pagination.type === "infinite_scroll" || config.pagination.type === "none") && !config.first_url) {
        alert("请填写起始网址");
        return;
    }

    // 清空日志和结果
    document.getElementById("log-panel").innerHTML = "";
    document.getElementById("result-section").classList.add("hidden");

    setStatus("running", "正在爬取...");
    document.getElementById("start-crawl").disabled = true;

    // 初始化当前任务统计
    taskFieldCount = Object.keys(config.fields).length;
    resetTaskStats();
    updateTaskStats(`<span class="api-hint">爬取中... 0 条 · ${taskFieldCount} 字段</span>`);

    // 更新顶部统计卡片
    document.getElementById("stat-render").textContent = config.render;
    const conc = config.concurrency || 1;
    document.getElementById("stat-concurrency").textContent = `${conc} · ${config.delay}s`;

    appendLog(`[提交] 任务：${config.name}\n`, "log-highlight");
    appendLog(`[配置] 渲染=${config.render}  翻页=${config.pagination.type}  字段=${Object.keys(config.fields).length}个\n\n`);

    try {
        const resp = await fetch("/api/crawl", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(config),
        });
        const data = await resp.json();
        if (data.task_id) {
            currentTaskId = data.task_id;
            appendLog(`[任务ID] ${currentTaskId}\n\n`);
        } else {
            throw new Error(data.error || "提交失败");
        }
    } catch (e) {
        setStatus("failed", `提交失败：${e.message}`);
        appendLog(`[错误] ${e.message}\n`, "log-error");
        document.getElementById("start-crawl").disabled = false;
    }
}

// ================================================================
// 加载结果
// ================================================================
async function loadResult(taskId) {
    try {
        const resp = await fetch(`/api/result/${taskId}`);
        const data = await resp.json();

        document.getElementById("result-count").textContent = data.count;
        document.getElementById("result-section").classList.remove("hidden");

        // 表头
        const thead = document.getElementById("result-head");
        thead.innerHTML = "";
        data.columns.forEach(col => {
            const th = document.createElement("th");
            th.textContent = col;
            thead.appendChild(th);
        });

        // 表体（最多显示200行，避免卡顿）
        const tbody = document.getElementById("result-body");
        tbody.innerHTML = "";
        const displayRows = data.rows.slice(0, 200);
        displayRows.forEach(row => {
            const tr = document.createElement("tr");
            data.columns.forEach(col => {
                const td = document.createElement("td");
                td.textContent = row[col] || "";
                td.title = row[col] || "";
                tr.appendChild(td);
            });
            tbody.appendChild(tr);
        });

        if (data.rows.length > 200) {
            appendLog(`\n[提示] 共 ${data.rows.length} 条，界面仅显示前200条，完整数据请下载Excel\n`, "log-highlight");
        }

        // 下载链接
        document.getElementById("download-btn").href = `/api/download/${taskId}`;

    } catch (e) {
        console.error("加载结果失败:", e);
    }
}

// ================================================================
// 工具函数
// ================================================================
function setStatus(type, text) {
    const bar = document.getElementById("status-bar");
    bar.innerHTML = `<span class="status-${type}">${text}</span>`;
}

function appendLog(text, className = "") {
    const panel = document.getElementById("log-panel");
    const line = document.createElement("div");
    line.className = "log-line " + className;
    line.textContent = text;
    panel.appendChild(line);
    panel.scrollTop = panel.scrollHeight;
}
