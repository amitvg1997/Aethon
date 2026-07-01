(function () {
  const config = window.AETHON_CONFIG || {};
  const apiBaseUrl = String(config.apiBaseUrl || "").replace(/\/$/, "");

  function formatBytes(bytes) {
    if (!Number.isFinite(bytes) || bytes <= 0) return "0 B";
    const units = ["B", "KB", "MB", "GB"];
    const i = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
    const v = bytes / 1024 ** i;
    return `${v.toFixed(v >= 10 || i === 0 ? 0 : 1)} ${units[i]}`;
  }

  function requireApiBase() {
    if (apiBaseUrl) return true;
    alert("Frontend config is missing the API endpoint. Deploy through GitHub Actions or set Frontend/config.js locally.");
    return false;
  }

  async function postJson(path, payload) {
    const res = await fetch(`${apiBaseUrl}${path}`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(payload),
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(body.message || `Request failed with status ${res.status}`);
    return body;
  }

  async function getJson(path) {
    const res = await fetch(`${apiBaseUrl}${path}`, { method: "GET" });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(body.message || `Request failed with status ${res.status}`);
    return body;
  }

  async function deleteJson(path) {
    const res = await fetch(`${apiBaseUrl}${path}`, { method: "DELETE" });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(body.message || `Request failed with status ${res.status}`);
    return body;
  }

  /* ── Upload page ───────────────────────────── */

  function initUploadPage() {
    const fileInput  = document.getElementById("fileInput");
    const dropzone   = document.getElementById("dropzone");
    const uploadBtn  = document.getElementById("uploadButton");
    const clearBtn   = document.getElementById("clearUploadsButton");
    const fileList   = document.getElementById("fileList");
    const statusLine = document.getElementById("uploadStatus");
    const syncBtn    = document.getElementById("syncButton");
    const refreshSyncBtn = document.getElementById("refreshSyncButton");
    const syncStatus = document.getElementById("syncStatus");
    let files = [];
    let syncPollTimer = null;

    const fileIcon = `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>`;

    function renderFiles(states) {
      fileList.innerHTML = "";
      files.forEach((file, i) => {
        const state = (states && states[i]) || { label: "Ready", kind: "" };
        const row = document.createElement("article");
        row.className = "file-row";
        row.innerHTML = `
          <div class="file-icon">${fileIcon}</div>
          <div class="file-info">
            <div class="file-name"></div>
            <div class="file-meta">${formatBytes(file.size)} · PDF</div>
          </div>
          <span class="pill ${state.kind || ""}">${state.label}</span>
        `;
        row.querySelector(".file-name").textContent = file.name;
        fileList.appendChild(row);
      });

      uploadBtn.disabled = files.length === 0;
      statusLine.textContent = files.length
        ? `${files.length} file${files.length === 1 ? "" : "s"} selected`
        : "No files selected";
    }

    function setFiles(next) {
      files = Array.from(next || []);
      renderFiles();
    }

    function uploadToS3(url, file, contentType) {
      return new Promise((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        xhr.open("PUT", url);
        xhr.setRequestHeader("Content-Type", contentType);
        xhr.onload = () =>
          xhr.status >= 200 && xhr.status < 300
            ? resolve()
            : reject(new Error(`S3 upload failed: ${xhr.status}`));
        xhr.onerror = () => reject(new Error("S3 upload failed."));
        xhr.send(file);
      });
    }

    async function uploadFile(file, index, states) {
      states[index] = { label: "Requesting…", kind: "" };
      renderFiles(states);

      const contentType = file.type || "application/octet-stream";
      const upload = await postJson("/uploads", { filename: file.name, content_type: contentType });

      states[index] = { label: "Uploading…", kind: "" };
      renderFiles(states);
      await uploadToS3(upload.upload_url, file, contentType);

      states[index] = { label: "Uploaded", kind: "done" };
      renderFiles(states);
      return upload.object_key;
    }

    function renderSyncStatus(data) {
      if (!syncStatus) return;

      const counts = data.status_counts || {};
      const readyCount = Number(data.ready_for_ingestion_count || 0);
      const syncedCount = Number(counts.SYNCED || counts.COMPLETE || counts.COMPLETED || 0);
      const metadataReadyCount = Number(counts.READY_FOR_INGESTION || 0);
      const extractingCount =
        Number(counts.PENDING || 0) +
        Number(counts.TEXTRACT_IN_PROGRESS || 0) +
        Number(counts.TEXTRACT_STARTED || 0);
      const failedCount =
        Number(counts.FAILED || 0) +
        Number(counts.INGESTION_FAILED || 0);
      const active = data.active_ingestion_job
        ? data.active_ingestion_job.status || "running"
        : null;

      let message;
      if (active) {
        message = `Knowledge Base sync is ${active}. Wait until it completes before asking questions from the latest uploads.`;
      } else if (readyCount > 0) {
        message = `${readyCount} document${readyCount === 1 ? "" : "s"} have metadata ready and need Knowledge Base sync. Click "Sync Knowledge Base" before asking questions from the latest uploads.`;
      } else if (syncedCount > 0 && extractingCount === 0 && failedCount === 0) {
        message = `Knowledge Base is synced. You can now ask questions from ${syncedCount} indexed document${syncedCount === 1 ? "" : "s"}, including the latest synced uploads.`;
      } else if (extractingCount > 0) {
        message = `${extractingCount} document${extractingCount === 1 ? " is" : "s are"} still being processed. Wait for metadata extraction to finish, then sync the Knowledge Base.`;
      } else {
        message = "No synced documents found yet. Upload PDFs, wait for metadata extraction, then sync the Knowledge Base.";
      }

      const details = [];
      if (metadataReadyCount > 0) details.push(`metadata ready: ${metadataReadyCount}`);
      if (syncedCount > 0) details.push(`synced: ${syncedCount}`);
      if (extractingCount > 0) details.push(`processing: ${extractingCount}`);
      if (failedCount > 0) details.push(`failed: ${failedCount}`);

      syncStatus.textContent = details.length ? `${message} (${details.join(", ")})` : message;

      if (syncPollTimer && !active) {
        window.clearInterval(syncPollTimer);
        syncPollTimer = null;
      }
    }

    async function refreshSyncStatus() {
      if (!requireApiBase()) return;
      if (refreshSyncBtn) refreshSyncBtn.disabled = true;
      try {
        const data = await getJson("/sync");
        renderSyncStatus(data);
      } catch (err) {
        syncStatus.textContent = err.message || "Failed to load Knowledge Base sync status.";
      } finally {
        if (refreshSyncBtn) refreshSyncBtn.disabled = false;
      }
    }

    async function startKnowledgeBaseSync() {
      if (!requireApiBase()) return;
      syncBtn.disabled = true;
      syncStatus.textContent = "Starting Knowledge Base sync...";
      try {
        const data = await postJson("/sync", {});
        syncStatus.textContent = data.message || "Knowledge Base sync request completed.";
        await refreshSyncStatus();
        if (!syncPollTimer) {
          syncPollTimer = window.setInterval(refreshSyncStatus, 15000);
        }
      } catch (err) {
        syncStatus.textContent = err.message || "Failed to start Knowledge Base sync.";
      } finally {
        syncBtn.disabled = false;
      }
    }

    fileInput.addEventListener("change", () => setFiles(fileInput.files));

    ["dragenter", "dragover"].forEach((e) =>
      dropzone.addEventListener(e, (ev) => { ev.preventDefault(); dropzone.classList.add("dragging"); })
    );
    ["dragleave", "drop"].forEach((e) =>
      dropzone.addEventListener(e, () => dropzone.classList.remove("dragging"))
    );
    dropzone.addEventListener("drop", (ev) => { ev.preventDefault(); setFiles(ev.dataTransfer.files); });

    clearBtn.addEventListener("click", () => { fileInput.value = ""; setFiles([]); });
    if (syncBtn) syncBtn.addEventListener("click", startKnowledgeBaseSync);
    if (refreshSyncBtn) refreshSyncBtn.addEventListener("click", refreshSyncStatus);

    uploadBtn.addEventListener("click", async () => {
      if (!requireApiBase() || files.length === 0) return;

      uploadBtn.disabled = true;
      const states = files.map(() => ({ label: "Queued", kind: "" }));
      renderFiles(states);

      const results = await Promise.allSettled(
        files.map((file, i) => uploadFile(file, i, states))
      );

      results.forEach((r, i) => {
        if (r.status === "rejected") states[i] = { label: "Failed", kind: "error" };
      });

      renderFiles(states);
      const uploaded = results.filter((r) => r.status === "fulfilled").length;
      statusLine.textContent = `${uploaded} of ${files.length} file${files.length === 1 ? "" : "s"} uploaded. Metadata extraction may take a few minutes; refresh sync status before asking questions.`;
      uploadBtn.disabled = files.length === 0;
      await refreshSyncStatus();
    });

    renderFiles();
    refreshSyncStatus();
  }

  /* ── Chat page ─────────────────────────────── */

  function initChatPage() {
    const form      = document.getElementById("questionForm");
    const input     = document.getElementById("questionInput");
    const messages  = document.getElementById("messages");
    const askBtn    = document.getElementById("askButton");
    const clearBtn  = document.getElementById("clearChatButton");

    function addMessage(role, content, meta) {
      const msg = document.createElement("div");
      msg.className = `message ${role}`;

      if (content === "__thinking__") {
        msg.innerHTML = `<div class="message-bubble"><div class="typing-dots"><span></span><span></span><span></span></div></div>`;
      } else {
        const bubble = document.createElement("div");
        bubble.className = "message-bubble";
        const p = document.createElement("p");
        p.textContent = content;
        bubble.appendChild(p);
        msg.appendChild(bubble);

        if (role === "assistant" && meta) {
          if (meta.usage) {
            const u = meta.usage;
            const bar = document.createElement("div");
            bar.className = "usage-bar";
            const filtered = meta.filterApplied;
            bar.innerHTML = `
              <span class="usage-tag ${filtered ? "filtered" : ""}">${filtered ? "Metadata filtered" : "Unfiltered search"}</span>
              <span class="usage-tag">${u.documents_selected} doc${u.documents_selected !== 1 ? "s" : ""} matched</span>
              <span class="usage-tag">${u.retrieved_chunks} chunk${u.retrieved_chunks !== 1 ? "s" : ""} retrieved</span>
              <span class="usage-tag">~${u.estimated_context_tokens} ctx tokens</span>
              <span class="usage-tag">${u.potential_context_token_reduction_percent}% context reduction</span>
            `;
            msg.appendChild(bar);
          }

          if (meta.citations && meta.citations.length > 0) {
            const cites = document.createElement("div");
            cites.className = "citations";
            meta.citations.forEach((c) => {
              const el = document.createElement("div");
              el.className = "citation";
              el.textContent = c.preview;
              cites.appendChild(el);
            });
            msg.appendChild(cites);
          }
        }
      }

      messages.appendChild(msg);
      messages.scrollTop = messages.scrollHeight;
      return msg;
    }

    function resetChat() {
      messages.innerHTML = "";
      addMessage("assistant", "Ask me anything about the documents you have uploaded. I'll search the knowledge base and answer based on what I find.");
    }

    clearBtn.addEventListener("click", resetChat);

    /* Auto-resize textarea */
    input.addEventListener("input", () => {
      input.style.height = "auto";
      input.style.height = Math.min(input.scrollHeight, 140) + "px";
    });

    /* Enter to submit, Shift+Enter for new line */
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        form.requestSubmit();
      }
    });

    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      if (!requireApiBase()) return;

      const question = input.value.trim();
      if (!question) { input.focus(); return; }

      input.value = "";
      input.style.height = "auto";
      addMessage("user", question);
      const pending = addMessage("assistant", "__thinking__");
      askBtn.disabled = true;

      try {
        const res = await postJson("/query", { question });
        pending.remove();
        addMessage("assistant", res.answer || "No answer was returned.", {
          usage: res.usage_summary,
          filterApplied: res.metadata_filter_applied,
          citations: res.citations,
        });
      } catch (err) {
        pending.remove();
        addMessage("assistant", err.message || "The query failed. Please try again.");
      } finally {
        askBtn.disabled = false;
        input.focus();
      }
    });
  }

  /* ── Evaluate page ─────────────────────────── */

  function initEvalPage() {
    const form           = document.getElementById("evalForm");
    const questionInput  = document.getElementById("evalQuestion");
    const expectedInput  = document.getElementById("evalExpectedDoc");
    const runBtn         = document.getElementById("runEvalButton");
    const statusLine     = document.getElementById("evalStatus");
    const compareSection = document.getElementById("evalCompare");
    const summaryLine    = document.getElementById("evalSummary");
    const tableBody      = document.getElementById("evalTableBody");
    const emptyState     = document.getElementById("evalEmptyState");
    const downloadBtn    = document.getElementById("downloadCsvButton");
    const clearBtn       = document.getElementById("clearHistoryButton");
    let latestResults    = [];

    function csvEscape(value) {
      const str = value === null || value === undefined ? "" : String(value);
      return /[",\n]/.test(str) ? `"${str.replace(/"/g, '""')}"` : str;
    }

    function buildCsv(results) {
      const headers = [
        "eval_id", "created_at", "question", "expected_document_id",
        "documents_selected",
        "unfiltered_retrieved_chunks", "filtered_retrieved_chunks",
        "unfiltered_context_tokens", "filtered_context_tokens",
        "context_token_reduction_percent",
        "unfiltered_correct_document_hit", "filtered_correct_document_hit",
        "unfiltered_answer", "filtered_answer",
      ];

      const rows = results.map((item) => [
        item.eval_id,
        item.created_at,
        item.question,
        item.expected_document_id ?? "",
        item.filtered.documents_selected,
        item.unfiltered.retrieved_chunks,
        item.filtered.retrieved_chunks,
        item.unfiltered.estimated_context_tokens,
        item.filtered.estimated_context_tokens,
        item.context_token_reduction_percent,
        item.unfiltered.correct_document_hit ?? "",
        item.filtered.correct_document_hit ?? "",
        item.unfiltered.answer,
        item.filtered.answer,
      ]);

      return [headers, ...rows].map((row) => row.map(csvEscape).join(",")).join("\r\n");
    }

    function downloadCsv(results) {
      const csv = buildCsv(results);
      const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
      const url = URL.createObjectURL(blob);
      const stamp = new Date().toISOString().replace(/[:.]/g, "-");
      const a = document.createElement("a");
      a.href = url;
      a.download = `aethon-eval-results-${stamp}.csv`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    }

    function hitClass(hit) {
      if (hit === null || hit === undefined) return "";
      return hit ? "hit" : "miss";
    }

    function hitLabel(hit) {
      if (hit === null || hit === undefined) return "";
      return hit ? "✓ hit" : "✗ miss";
    }

    function renderStats(target, mode, result) {
      target.innerHTML = "";
      const tags = [
        mode === "filtered"
          ? `${result.documents_selected} doc${result.documents_selected === 1 ? "" : "s"} scanned`
          : null,
        `${result.retrieved_chunks} chunk${result.retrieved_chunks === 1 ? "" : "s"} retrieved`,
        `~${result.estimated_context_tokens} ctx tokens`,
      ].filter(Boolean);

      tags.forEach((label) => {
        const span = document.createElement("span");
        span.className = "usage-tag";
        span.textContent = label;
        target.appendChild(span);
      });

      if (result.correct_document_hit !== null && result.correct_document_hit !== undefined) {
        const span = document.createElement("span");
        span.className = `usage-tag ${hitClass(result.correct_document_hit)}`;
        span.textContent = hitLabel(result.correct_document_hit);
        target.appendChild(span);
      }
    }

    function renderCitations(target, citations) {
      target.innerHTML = "";
      (citations || []).forEach((c) => {
        const el = document.createElement("div");
        el.className = "citation";
        el.textContent = c.preview;
        target.appendChild(el);
      });
    }

    function renderComparison(record) {
      compareSection.hidden = false;

      renderStats(document.getElementById("unfilteredStats"), "unfiltered", record.unfiltered);
      document.getElementById("unfilteredAnswer").textContent = record.unfiltered.answer;
      renderCitations(document.getElementById("unfilteredCitations"), record.unfiltered.citations);

      renderStats(document.getElementById("filteredStats"), "filtered", record.filtered);
      document.getElementById("filteredAnswer").textContent = record.filtered.answer;
      renderCitations(document.getElementById("filteredCitations"), record.filtered.citations);
    }

    function renderSummary(summary) {
      if (!summary || !summary.count) {
        summaryLine.textContent = "";
        return;
      }
      const parts = [
        `${summary.count} run${summary.count === 1 ? "" : "s"}`,
        `avg ${summary.avg_context_token_reduction_percent}% context reduction`,
      ];
      if (summary.labeled_count > 0) {
        const unfilteredRate = summary.unfiltered_hit_rate_percent ?? "–";
        const filteredRate = summary.filtered_hit_rate_percent ?? "–";
        parts.push(
          `hit rate: unfiltered ${unfilteredRate}% vs filtered ${filteredRate}% (${summary.labeled_count} labeled)`
        );
      }
      summaryLine.textContent = parts.join(" · ");
    }

    function renderHistory(results) {
      tableBody.innerHTML = "";
      emptyState.hidden = results.length > 0;
      downloadBtn.disabled = results.length === 0;
      clearBtn.disabled = results.length === 0;

      results.forEach((item) => {
        const row = document.createElement("tr");
        const cells = [
          { text: item.question },
          { text: String(item.filtered.documents_selected) },
          { text: String(item.unfiltered.retrieved_chunks) },
          { text: String(item.filtered.retrieved_chunks) },
          { text: `${item.context_token_reduction_percent}%` },
          { text: hitLabel(item.unfiltered.correct_document_hit) || "–", cls: hitClass(item.unfiltered.correct_document_hit) },
          { text: hitLabel(item.filtered.correct_document_hit) || "–", cls: hitClass(item.filtered.correct_document_hit) },
        ];

        cells.forEach(({ text, cls }) => {
          const td = document.createElement("td");
          td.textContent = text;
          if (cls) td.className = cls;
          row.appendChild(td);
        });

        tableBody.appendChild(row);
      });
    }

    async function loadHistory() {
      try {
        const data = await getJson("/eval");
        latestResults = data.results || [];
        renderSummary(data.summary);
        renderHistory(latestResults);
      } catch (err) {
        summaryLine.textContent = err.message || "Failed to load evaluation history.";
      }
    }

    downloadBtn.addEventListener("click", () => {
      if (latestResults.length === 0) return;
      downloadCsv(latestResults);
    });

    clearBtn.addEventListener("click", async () => {
      if (latestResults.length === 0) return;
      const confirmed = window.confirm(
        `Delete all ${latestResults.length} evaluation run${latestResults.length === 1 ? "" : "s"}? This cannot be undone.`
      );
      if (!confirmed) return;

      clearBtn.disabled = true;
      try {
        await deleteJson("/eval");
        await loadHistory();
      } catch (err) {
        summaryLine.textContent = err.message || "Failed to clear evaluation history.";
      } finally {
        clearBtn.disabled = latestResults.length === 0;
      }
    });

    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      if (!requireApiBase()) return;

      const question = questionInput.value.trim();
      if (!question) { questionInput.focus(); return; }

      const expectedDocumentId = expectedInput.value.trim();

      runBtn.disabled = true;
      statusLine.textContent = "Running filtered and unfiltered retrieval…";

      try {
        const record = await postJson("/eval", {
          question,
          expected_document_id: expectedDocumentId || undefined,
        });
        renderComparison(record);
        statusLine.textContent = "Done.";
        await loadHistory();
      } catch (err) {
        statusLine.textContent = err.message || "Comparison failed. Please try again.";
      } finally {
        runBtn.disabled = false;
      }
    });

    loadHistory();
  }

  /* ── Init ──────────────────────────────────── */

  document.addEventListener("DOMContentLoaded", () => {
    const page = document.body.dataset.page;
    if (page === "upload") initUploadPage();
    if (page === "chat") initChatPage();
    if (page === "eval") initEvalPage();
  });
})();
