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
      const duplicateCount = Number(counts.SKIPPED_DUPLICATE || 0);
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
      if (duplicateCount > 0) details.push(`duplicates skipped: ${duplicateCount}`);
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
              <span class="usage-tag">${u.unique_source_documents} source doc${u.unique_source_documents !== 1 ? "s" : ""}</span>
            `;
            msg.appendChild(bar);
          }

          /* Router explainability: which documents the metadata filter
             selected, with their match scores and matched terms. */
          if (meta.selected && meta.selected.length > 0) {
            const routerBar = document.createElement("div");
            routerBar.className = "usage-bar";
            meta.selected.forEach((doc) => {
              const tag = document.createElement("span");
              tag.className = "usage-tag filtered";
              const score = Number(doc.score);
              tag.textContent = `→ ${doc.title || doc.document_id}${Number.isFinite(score) ? ` · ${score.toFixed(2)}` : ""}`;
              if (doc.matched_terms && doc.matched_terms.length > 0) {
                tag.title = `matched terms: ${doc.matched_terms.join(", ")}`;
              }
              routerBar.appendChild(tag);
            });
            msg.appendChild(routerBar);
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
          selected: res.selected_documents,
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
    const expectedAnswerInput = document.getElementById("evalExpectedAnswer");
    const runBtn         = document.getElementById("runEvalButton");
    const statusLine     = document.getElementById("evalStatus");
    const compareSection = document.getElementById("evalCompare");
    const summaryLine    = document.getElementById("evalSummary");
    const tableBody      = document.getElementById("evalTableBody");
    const emptyState     = document.getElementById("evalEmptyState");
    const downloadBtn    = document.getElementById("downloadCsvButton");
    const clearBtn       = document.getElementById("clearHistoryButton");
    const testsetInput   = document.getElementById("testsetFile");
    const runBatchBtn    = document.getElementById("runBatchButton");
    const batchStatus    = document.getElementById("batchStatus");
    let latestResults    = [];

    const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

    /* The expected-document field accepts either a document ID (UUID) or the
       uploaded filename; filenames keep labeled test sets portable across
       re-uploads (document IDs change every upload). */
    function expectedDocumentPayload(value) {
      const trimmed = String(value || "").trim();
      if (!trimmed) return {};
      return UUID_PATTERN.test(trimmed)
        ? { expected_document_id: trimmed }
        : { expected_document_filename: trimmed };
    }

    function csvEscape(value) {
      const str = value === null || value === undefined ? "" : String(value);
      return /[",\n]/.test(str) ? `"${str.replace(/"/g, '""')}"` : str;
    }

    function buildCsv(results) {
      const headers = [
        "eval_id", "created_at", "question",
        "expected_document_id", "expected_document_filename", "expected_answer",
        "documents_selected",
        "unfiltered_retrieved_chunks", "filtered_retrieved_chunks",
        "unfiltered_context_tokens", "filtered_context_tokens",
        "context_token_reduction_percent",
        "unfiltered_unique_source_documents", "filtered_unique_source_documents",
        "unfiltered_context_precision", "filtered_context_precision",
        "unfiltered_reciprocal_rank", "filtered_reciprocal_rank",
        "unfiltered_correct_document_hit", "filtered_correct_document_hit",
        "unfiltered_answer_verdict", "filtered_answer_verdict",
        "unfiltered_total_ms", "filtered_total_ms",
        "unfiltered_answer", "filtered_answer",
      ];

      const rows = results.map((item) => [
        item.eval_id,
        item.created_at,
        item.question,
        item.expected_document_id ?? "",
        item.expected_document_filename ?? "",
        item.expected_answer ?? "",
        item.filtered.documents_selected,
        item.unfiltered.retrieved_chunks,
        item.filtered.retrieved_chunks,
        item.unfiltered.estimated_context_tokens,
        item.filtered.estimated_context_tokens,
        item.context_token_reduction_percent,
        item.unfiltered.unique_source_documents ?? "",
        item.filtered.unique_source_documents ?? "",
        item.unfiltered.context_precision ?? "",
        item.filtered.context_precision ?? "",
        item.unfiltered.reciprocal_rank ?? "",
        item.filtered.reciprocal_rank ?? "",
        item.unfiltered.correct_document_hit ?? "",
        item.filtered.correct_document_hit ?? "",
        item.unfiltered.answer_verdict ?? "",
        item.filtered.answer_verdict ?? "",
        item.unfiltered.total_ms ?? "",
        item.filtered.total_ms ?? "",
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

    function verdictClass(verdict) {
      if (verdict === "correct") return "hit";
      if (verdict === "incorrect") return "miss";
      return "";
    }

    function verdictLabel(verdict) {
      if (verdict === "correct") return "✓ correct";
      if (verdict === "partially_correct") return "~ partial";
      if (verdict === "incorrect") return "✗ incorrect";
      return "";
    }

    function isSet(value) {
      return value !== null && value !== undefined;
    }

    function renderStats(target, mode, result) {
      target.innerHTML = "";

      function addTag(label, cls, tooltip) {
        const span = document.createElement("span");
        span.className = `usage-tag${cls ? ` ${cls}` : ""}`;
        span.textContent = label;
        if (tooltip) span.title = tooltip;
        target.appendChild(span);
      }

      if (mode === "filtered") {
        addTag(`${result.documents_selected} doc${result.documents_selected === 1 ? "" : "s"} selected`);
      }
      addTag(`${result.retrieved_chunks} chunk${result.retrieved_chunks === 1 ? "" : "s"} retrieved`);
      addTag(`~${result.estimated_context_tokens} ctx tokens`);
      if (isSet(result.unique_source_documents)) {
        addTag(`${result.unique_source_documents} source doc${result.unique_source_documents === 1 ? "" : "s"}`);
      }
      if (isSet(result.context_precision)) {
        addTag(
          `precision ${Math.round(result.context_precision * 100)}%`,
          "",
          "Share of retrieved chunks that come from the expected document"
        );
      }
      if (isSet(result.distractor_chunks) && result.distractor_chunks > 0) {
        addTag(`${result.distractor_chunks} distractor chunk${result.distractor_chunks === 1 ? "" : "s"}`, "miss");
      }
      if (isSet(result.first_relevant_rank)) {
        addTag(`first hit at rank ${result.first_relevant_rank}`);
      }
      if (isSet(result.total_ms)) {
        addTag(`${(result.total_ms / 1000).toFixed(1)}s`);
      }
      if (isSet(result.correct_document_hit)) {
        addTag(hitLabel(result.correct_document_hit), hitClass(result.correct_document_hit));
      }
      if (result.answer_verdict) {
        addTag(
          `answer ${verdictLabel(result.answer_verdict)}`,
          verdictClass(result.answer_verdict),
          result.answer_verdict_reason || ""
        );
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

      function versus(unfilteredValue, filteredValue, suffix) {
        const u = isSet(unfilteredValue) ? `${unfilteredValue}${suffix}` : "–";
        const f = isSet(filteredValue) ? `${filteredValue}${suffix}` : "–";
        return `${u} → ${f}`;
      }

      const parts = [
        `${summary.count} run${summary.count === 1 ? "" : "s"}`,
        `avg ${summary.avg_context_token_reduction_percent}% context reduction`,
      ];
      if (isSet(summary.avg_unfiltered_unique_documents) || isSet(summary.avg_filtered_unique_documents)) {
        parts.push(`source docs ${versus(summary.avg_unfiltered_unique_documents, summary.avg_filtered_unique_documents, "")}`);
      }
      if (summary.labeled_count > 0) {
        parts.push(`hit rate ${versus(summary.unfiltered_hit_rate_percent, summary.filtered_hit_rate_percent, "%")}`);
        if (isSet(summary.avg_unfiltered_context_precision) || isSet(summary.avg_filtered_context_precision)) {
          parts.push(`precision ${versus(summary.avg_unfiltered_context_precision, summary.avg_filtered_context_precision, "")}`);
        }
        if (isSet(summary.unfiltered_mrr) || isSet(summary.filtered_mrr)) {
          parts.push(`MRR ${versus(summary.unfiltered_mrr, summary.filtered_mrr, "")}`);
        }
        parts.push(`${summary.labeled_count} labeled`);
      }
      if (summary.judged_count > 0) {
        parts.push(
          `answer accuracy ${versus(summary.unfiltered_answer_accuracy_percent, summary.filtered_answer_accuracy_percent, "%")} (${summary.judged_count} judged)`
        );
      }
      summaryLine.textContent = `unfiltered → filtered: ${parts.join(" · ")}`;
    }

    function renderHistory(results) {
      tableBody.innerHTML = "";
      emptyState.hidden = results.length > 0;
      downloadBtn.disabled = results.length === 0;
      clearBtn.disabled = results.length === 0;

      function pair(unfilteredValue, filteredValue, format) {
        const fmt = format || String;
        const u = isSet(unfilteredValue) ? fmt(unfilteredValue) : "–";
        const f = isSet(filteredValue) ? fmt(filteredValue) : "–";
        return `${u} → ${f}`;
      }

      results.forEach((item) => {
        const row = document.createElement("tr");
        const filteredHit = item.filtered.correct_document_hit;
        const filteredVerdict = item.filtered.answer_verdict;
        const cells = [
          { text: item.question },
          { text: String(item.filtered.documents_selected) },
          { text: pair(item.unfiltered.retrieved_chunks, item.filtered.retrieved_chunks) },
          { text: pair(item.unfiltered.unique_source_documents, item.filtered.unique_source_documents) },
          { text: `${item.context_token_reduction_percent}%` },
          {
            text: pair(item.unfiltered.context_precision, item.filtered.context_precision,
              (v) => `${Math.round(v * 100)}%`),
          },
          {
            text: pair(item.unfiltered.correct_document_hit, item.filtered.correct_document_hit,
              (v) => (v ? "✓" : "✗")),
            cls: hitClass(filteredHit),
          },
          {
            text: pair(item.unfiltered.answer_verdict, item.filtered.answer_verdict, verdictLabel),
            cls: verdictClass(filteredVerdict),
          },
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

      const expectedAnswer = expectedAnswerInput.value.trim();

      runBtn.disabled = true;
      statusLine.textContent = "Running filtered and unfiltered retrieval…";

      try {
        const record = await postJson("/eval", {
          question,
          ...expectedDocumentPayload(expectedInput.value),
          expected_answer: expectedAnswer || undefined,
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

    /* ── Batch test-set runner ─────────────────── */

    function readTestsetFile(file) {
      return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => {
          try {
            const parsed = JSON.parse(String(reader.result));
            if (!Array.isArray(parsed) || parsed.length === 0) {
              throw new Error("Test set must be a non-empty JSON array.");
            }
            resolve(parsed);
          } catch (err) {
            reject(new Error(`Invalid test set: ${err.message}`));
          }
        };
        reader.onerror = () => reject(new Error("Could not read the test set file."));
        reader.readAsText(file);
      });
    }

    testsetInput.addEventListener("change", () => {
      runBatchBtn.disabled = !testsetInput.files || testsetInput.files.length === 0;
      batchStatus.textContent = "";
    });

    runBatchBtn.addEventListener("click", async () => {
      if (!requireApiBase()) return;
      const file = testsetInput.files && testsetInput.files[0];
      if (!file) return;

      runBatchBtn.disabled = true;
      runBtn.disabled = true;
      let failures = 0;

      try {
        const testset = await readTestsetFile(file);

        /* Each case is one POST /eval (both pipelines + judge). Runs are
           sequential to stay friendly to the LLM API's rate limits. */
        for (let i = 0; i < testset.length; i += 1) {
          const testCase = testset[i] || {};
          const question = String(testCase.question || "").trim();
          batchStatus.textContent = `Running ${i + 1}/${testset.length}…`;

          if (!question) {
            failures += 1;
            continue;
          }

          try {
            const record = await postJson("/eval", {
              question,
              expected_document_id: testCase.expected_document_id || undefined,
              expected_document_filename: testCase.expected_document_filename || undefined,
              expected_answer: testCase.expected_answer || undefined,
            });
            renderComparison(record);
          } catch (err) {
            failures += 1;
          }
        }

        batchStatus.textContent = failures
          ? `Done: ${testset.length - failures} of ${testset.length} runs succeeded, ${failures} failed.`
          : `Done: all ${testset.length} runs succeeded.`;
        await loadHistory();
      } catch (err) {
        batchStatus.textContent = err.message || "Batch run failed.";
      } finally {
        runBatchBtn.disabled = !testsetInput.files || testsetInput.files.length === 0;
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
