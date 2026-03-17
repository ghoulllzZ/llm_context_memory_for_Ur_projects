const state = {
  projects: [],
  pendingKnowledge: [],
  selectedProjectId: null,
};

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.error || `Request failed: ${response.status}`);
  }
  return payload;
}

async function loadOverview() {
  try {
    const overview = await api("/api/overview");
    state.projects = overview.projects || [];
    state.pendingKnowledge = overview.pending_knowledge || [];
    if (state.selectedProjectId && !state.projects.some((project) => project.id === state.selectedProjectId)) {
      state.selectedProjectId = null;
      document.getElementById("timelineTitle").textContent = "Select a project";
      document.getElementById("timeline").innerHTML = '<div class="timeline empty-state">No project selected.</div>';
    }
    renderProjects();
    renderReviewQueue();
    renderProjectSelect();
    document.getElementById("serverStatus").textContent = "Connected";
  } catch (error) {
    document.getElementById("serverStatus").textContent = error.message;
  }
}

function renderProjects() {
  const list = document.getElementById("projectList");
  const count = document.getElementById("projectCount");
  list.innerHTML = "";
  count.textContent = `${state.projects.length} projects`;
  if (!state.projects.length) {
    list.innerHTML = '<div class="empty-state">No projects yet.</div>';
    return;
  }
  for (const project of state.projects) {
    const card = document.createElement("div");
    card.className = "card";
    card.innerHTML = `
      <h3>${escapeHtml(project.name)}</h3>
      <p>${escapeHtml(project.description || "No description")}</p>
      <div class="badge-row">
        <span class="badge">${project.status}</span>
        <span class="badge">${project.conversation_count || 0} conversations</span>
        <span class="badge">${project.knowledge_count || 0} knowledge units</span>
      </div>
      <div class="inline-actions">
        <button data-project-action="open">Open</button>
        <button class="secondary" data-project-action="delete">Delete</button>
      </div>
    `;
    card.querySelector('[data-project-action="open"]').addEventListener("click", () => {
      state.selectedProjectId = project.id;
      loadProjectTimeline(project.id);
    });
    card.querySelector('[data-project-action="delete"]').addEventListener("click", async () => {
      const confirmed = window.confirm(`Delete project "${project.name}"? This removes the project, its links, and generated context packs, but keeps imported conversations.`);
      if (!confirmed) return;
      await api(`/api/projects/${project.id}`, { method: "DELETE" });
      if (state.selectedProjectId === project.id) {
        state.selectedProjectId = null;
      }
      await loadOverview();
    });
    list.appendChild(card);
  }
}

function renderProjectSelect() {
  const select = document.getElementById("contextProjectSelect");
  select.innerHTML = "";
  for (const project of state.projects) {
    const option = document.createElement("option");
    option.value = project.id;
    option.textContent = project.name;
    select.appendChild(option);
  }
  if (state.selectedProjectId) {
    select.value = state.selectedProjectId;
  }
}

async function loadProjectTimeline(projectId) {
  const timeline = document.getElementById("timeline");
  const title = document.getElementById("timelineTitle");
  timeline.innerHTML = "<div class=\"empty-state\">Loading...</div>";
  const payload = await api(`/api/projects/${projectId}/timeline`);
  title.textContent = payload.project.name;
  renderProjectSelect();
  const cards = [];
  for (const conversation of payload.conversations || []) {
    cards.push(`
      <div class="card timeline-item">
        <h4>Conversation: ${escapeHtml(conversation.title)}</h4>
        <p>${escapeHtml(conversation.platform)} · ${escapeHtml(conversation.last_message_at || "")}</p>
        <div class="inline-actions">
          <button class="secondary" data-action="attach-project" data-conversation-id="${conversation.id}">Confirm Project</button>
        </div>
      </div>
    `);
  }
  for (const unit of payload.knowledge_units || []) {
    cards.push(`
      <div class="card timeline-item">
        <h4>${escapeHtml(unit.title)}</h4>
        <p>${escapeHtml(unit.body)}</p>
        <div class="badge-row">
          <span class="badge">${escapeHtml(unit.type)}</span>
          <span class="badge">${escapeHtml(unit.review_status)}</span>
        </div>
      </div>
    `);
  }
  for (const pack of payload.context_packs || []) {
    cards.push(`
      <div class="card timeline-item">
        <h4>Context Pack · ${escapeHtml(pack.template_type)}</h4>
        <p>${escapeHtml(pack.task_goal)}</p>
      </div>
    `);
  }
  timeline.innerHTML = cards.length ? cards.join("") : '<div class="empty-state">No project activity yet.</div>';
  timeline.querySelectorAll("[data-action='attach-project']").forEach((button) => {
    button.addEventListener("click", async () => {
      await api(`/api/conversations/${button.dataset.conversationId}/projects`, {
        method: "POST",
        body: JSON.stringify({ project_ids: [projectId] }),
      });
      await Promise.all([loadOverview(), loadProjectTimeline(projectId)]);
    });
  });
}

function renderReviewQueue() {
  const container = document.getElementById("reviewQueue");
  const counter = document.getElementById("reviewCount");
  counter.textContent = `${state.pendingKnowledge.length} pending`;
  container.innerHTML = "";
  if (!state.pendingKnowledge.length) {
    container.innerHTML = '<div class="empty-state">No pending knowledge units.</div>';
    return;
  }
  for (const item of state.pendingKnowledge) {
    const wrapper = document.createElement("div");
    wrapper.className = "card";
    wrapper.innerHTML = `
      <h4>${escapeHtml(item.title)}</h4>
      <p>${escapeHtml(item.body)}</p>
      <div class="badge-row">
        <span class="badge">${escapeHtml(item.type)}</span>
        <span class="badge">${escapeHtml(item.conversation_title || "Unknown conversation")}</span>
      </div>
      <label class="field" style="margin-top:12px;">
        <span>Edit before approval</span>
        <textarea rows="4">${escapeHtml(item.body)}</textarea>
      </label>
      <div class="inline-actions">
        <button data-review="approve">Approve</button>
        <button class="secondary" data-review="reject">Reject</button>
        <button class="secondary" data-review="delete">Delete</button>
      </div>
    `;
    const textarea = wrapper.querySelector("textarea");
    wrapper.querySelector("[data-review='approve']").addEventListener("click", async () => {
      const updatedFields = textarea.value !== item.body ? { body: textarea.value, title: item.title } : null;
      await api("/api/reviews", {
        method: "POST",
        body: JSON.stringify({
          target_type: "knowledge_unit",
          target_id: item.id,
          action: "approve",
          updated_fields: updatedFields,
        }),
      });
      await loadOverview();
      if (state.selectedProjectId) {
        await loadProjectTimeline(state.selectedProjectId);
      }
    });
    wrapper.querySelector("[data-review='reject']").addEventListener("click", async () => {
      await api("/api/reviews", {
        method: "POST",
        body: JSON.stringify({ target_type: "knowledge_unit", target_id: item.id, action: "reject" }),
      });
      await loadOverview();
    });
    wrapper.querySelector("[data-review='delete']").addEventListener("click", async () => {
      const confirmed = window.confirm("Delete this review queue item permanently?");
      if (!confirmed) return;
      await api(`/api/knowledge-units/${item.id}`, { method: "DELETE" });
      await loadOverview();
      if (state.selectedProjectId) {
        await loadProjectTimeline(state.selectedProjectId);
      }
    });
    container.appendChild(wrapper);
  }
}

async function createProject() {
  const name = document.getElementById("projectName").value.trim();
  const description = document.getElementById("projectDescription").value.trim();
  if (!name) return;
  await api("/api/projects", {
    method: "POST",
    body: JSON.stringify({ name, description, status: "active" }),
  });
  document.getElementById("projectName").value = "";
  document.getElementById("projectDescription").value = "";
  await loadOverview();
}

async function importChatGPTExport() {
  const input = document.getElementById("chatgptExportInput");
  const file = input.files[0];
  if (!file) return;
  const contentBase64 = await fileToBase64(file);
  await api("/api/ingest/chatgpt-export", {
    method: "POST",
    body: JSON.stringify({ filename: file.name, content_base64: contentBase64 }),
  });
  input.value = "";
  await loadOverview();
}

async function generateContextPack() {
  const projectId = document.getElementById("contextProjectSelect").value;
  if (!projectId) return;
  const payload = await api("/api/context-packs/generate", {
    method: "POST",
    body: JSON.stringify({
      project_id: projectId,
      template_type: document.getElementById("contextTemplateSelect").value,
      task_goal: document.getElementById("contextTaskGoal").value,
      budget_mode: "chars",
      budget_value: Number(document.getElementById("contextBudget").value || 3600),
      reviewed_only: document.getElementById("reviewedOnlyCheckbox").checked,
    }),
  });
  document.getElementById("contextOutput").value = payload.output_text;
  await loadOverview();
  if (state.selectedProjectId === projectId) {
    await loadProjectTimeline(projectId);
  }
}

async function search() {
  const query = document.getElementById("searchInput").value.trim();
  const results = document.getElementById("searchResults");
  results.innerHTML = "";
  if (!query) return;
  const payload = await api(`/api/search?q=${encodeURIComponent(query)}`);
  if (!payload.length) {
    results.innerHTML = '<div class="empty-state">No matches found.</div>';
    return;
  }
  results.innerHTML = payload.map((item) => `
    <div class="card">
      <h4>${escapeHtml(item.owner_type)}</h4>
      <p>${escapeHtml(item.text)}</p>
    </div>
  `).join("");
}

function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = reader.result;
      if (typeof result !== "string") {
        reject(new Error("Could not read file"));
        return;
      }
      resolve(result.split(",")[1]);
    };
    reader.onerror = () => reject(reader.error || new Error("Could not read file"));
    reader.readAsDataURL(file);
  });
}

function escapeHtml(value) {
  return String(value || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

document.getElementById("refreshButton").addEventListener("click", loadOverview);
document.getElementById("createProjectButton").addEventListener("click", createProject);
document.getElementById("importExportButton").addEventListener("click", importChatGPTExport);
document.getElementById("generateContextButton").addEventListener("click", generateContextPack);
document.getElementById("searchButton").addEventListener("click", search);
document.getElementById("searchInput").addEventListener("keydown", (event) => {
  if (event.key === "Enter") search();
});

loadOverview();
