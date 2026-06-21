(function () {
  const config = window.AETHON_CONFIG || {};
  const apiBaseUrl = String(config.apiBaseUrl || "").replace(/\/$/, "");

  function formatBytes(bytes) {
    if (!Number.isFinite(bytes) || bytes <= 0) {
      return "0 B";
    }

    const units = ["B", "KB", "MB", "GB"];
    const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
    const value = bytes / 1024 ** index;
    return `${value.toFixed(value >= 10 || index === 0 ? 0 : 1)} ${units[index]}`;
  }

  function requireApiBase() {
    if (apiBaseUrl) {
      return true;
    }

    alert("Frontend config is missing the API endpoint. Deploy through GitHub Actions or set Frontend/config.js locally.");
    return false;
  }

  async function postJson(path, payload) {
    const response = await fetch(`${apiBaseUrl}${path}`, {
      method: "POST",
      headers: {"content-type": "application/json"},
      body: JSON.stringify(payload),
    });

    const body = await response.json().catch(() => ({}));

    if (!response.ok) {
      throw new Error(body.message || `Request failed with status ${response.status}`);
    }

    return body;
  }

  function initUploadPage() {
    const fileInput = document.getElementById("fileInput");
    const dropzone = document.getElementById("dropzone");
    const uploadButton = document.getElementById("uploadButton");
    const clearButton = document.getElementById("clearUploadsButton");
    const fileList = document.getElementById("fileList");
    const statusLine = document.getElementById("uploadStatus");
    let files = [];

    function renderFiles(states) {
      fileList.innerHTML = "";

      files.forEach((file, index) => {
        const state = states && states[index] ? states[index] : {label: "Ready", kind: ""};
        const row = document.createElement("article");
        row.className = "file-row";
        row.innerHTML = `
          <div>
            <div class="file-name"></div>
            <div class="file-meta">${formatBytes(file.size)} - ${file.type || "document"}</div>
          </div>
          <div class="pill ${state.kind || ""}">${state.label}</div>
        `;
        row.querySelector(".file-name").textContent = file.name;
        fileList.appendChild(row);
      });

      uploadButton.disabled = files.length === 0;
      statusLine.textContent = files.length
        ? `${files.length} file${files.length === 1 ? "" : "s"} selected.`
        : "No files selected.";
    }

    function setFiles(nextFiles) {
      files = Array.from(nextFiles || []);
      renderFiles();
    }

    function uploadToS3(uploadUrl, file, contentType) {
      return new Promise((resolve, reject) => {
        const request = new XMLHttpRequest();
        request.open("PUT", uploadUrl);
        request.setRequestHeader("Content-Type", contentType);

        request.onload = () => {
          if (request.status >= 200 && request.status < 300) {
            resolve();
          } else {
            reject(new Error(`S3 upload failed with status ${request.status}`));
          }
        };
        request.onerror = () => reject(new Error("S3 upload failed."));
        request.send(file);
      });
    }

    async function uploadFile(file, index, states) {
      states[index] = {label: "Requesting URL", kind: ""};
      renderFiles(states);

      const contentType = file.type || "application/octet-stream";
      const upload = await postJson("/uploads", {
        filename: file.name,
        content_type: contentType,
      });

      states[index] = {label: "Uploading", kind: ""};
      renderFiles(states);
      await uploadToS3(upload.upload_url, file, contentType);

      states[index] = {label: "Uploaded", kind: "done"};
      renderFiles(states);
      return upload.object_key;
    }

    fileInput.addEventListener("change", () => setFiles(fileInput.files));

    ["dragenter", "dragover"].forEach((eventName) => {
      dropzone.addEventListener(eventName, (event) => {
        event.preventDefault();
        dropzone.classList.add("dragging");
      });
    });

    ["dragleave", "drop"].forEach((eventName) => {
      dropzone.addEventListener(eventName, () => dropzone.classList.remove("dragging"));
    });

    dropzone.addEventListener("drop", (event) => {
      event.preventDefault();
      setFiles(event.dataTransfer.files);
    });

    clearButton.addEventListener("click", () => {
      fileInput.value = "";
      setFiles([]);
    });

    uploadButton.addEventListener("click", async () => {
      if (!requireApiBase() || files.length === 0) {
        return;
      }

      uploadButton.disabled = true;
      const states = files.map(() => ({label: "Queued", kind: ""}));
      renderFiles(states);

      const results = await Promise.allSettled(
        files.map((file, index) => uploadFile(file, index, states))
      );

      results.forEach((result, index) => {
        if (result.status === "rejected") {
          states[index] = {label: "Failed", kind: "error"};
        }
      });

      renderFiles(states);
      const uploaded = results.filter((result) => result.status === "fulfilled").length;
      statusLine.textContent = `${uploaded} of ${files.length} file${files.length === 1 ? "" : "s"} uploaded.`;
      uploadButton.disabled = files.length === 0;
    });

    renderFiles();
  }

  function initChatPage() {
    const form = document.getElementById("questionForm");
    const input = document.getElementById("questionInput");
    const messages = document.getElementById("messages");
    const askButton = document.getElementById("askButton");
    const clearButton = document.getElementById("clearChatButton");

    function addMessage(role, text, usageSummary) {
      const message = document.createElement("article");
      message.className = `message ${role}`;

      const paragraph = document.createElement("p");
      paragraph.textContent = text;
      message.appendChild(paragraph);

      if (role === "assistant" && usageSummary) {
        const usage = document.createElement("p");
        usage.className = "usage-summary";
        usage.textContent = `Retrieval: ${usageSummary.documents_selected} document(s) selected, ${usageSummary.retrieved_chunks} chunk(s) used, about ${usageSummary.estimated_context_tokens} context tokens sent to the LLM. Potential context reduction versus the unfiltered maximum: ${usageSummary.potential_context_token_reduction_percent}%.`;
        message.appendChild(usage);
      }

      messages.appendChild(message);
      messages.scrollTop = messages.scrollHeight;
      return message;
    }

    clearButton.addEventListener("click", () => {
      messages.innerHTML = "";
      addMessage("assistant", "Ask a question about the documents you have uploaded.");
    });

    form.addEventListener("submit", async (event) => {
      event.preventDefault();

      if (!requireApiBase()) {
        return;
      }

      const question = input.value.trim();
      if (!question) {
        input.focus();
        return;
      }

      input.value = "";
      addMessage("user", question);
      const pending = addMessage("assistant", "Thinking through the retrieved document context...");
      askButton.disabled = true;

      try {
        const response = await postJson("/query", {question});
        pending.remove();
        addMessage("assistant", response.answer || "No answer was returned.", response.usage_summary);
      } catch (error) {
        pending.remove();
        addMessage("assistant", error.message || "The query failed.");
      } finally {
        askButton.disabled = false;
        input.focus();
      }
    });
  }

  document.addEventListener("DOMContentLoaded", () => {
    const page = document.body.dataset.page;

    if (page === "upload") {
      initUploadPage();
    }

    if (page === "chat") {
      initChatPage();
    }
  });
})();
