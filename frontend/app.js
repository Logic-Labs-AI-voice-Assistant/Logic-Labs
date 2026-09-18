// Point this at your backend. When running locally with uvicorn,
// this is the default address.
const API_BASE = "http://127.0.0.1:8000/api";

let sessionId = null;

const profileBtn = document.getElementById("profile-btn");
const profileMenu = document.getElementById("profile-menu");
const logoutBtn = document.getElementById("logout-btn");
const settingsBtn = document.getElementById("settings-btn");

const chatView = document.getElementById("chat-view");
const dashboardView = document.getElementById("dashboard-view");
const ticketsBtn = document.getElementById("tickets-btn");
const backBtn = document.getElementById("back-btn");
const createBtn = document.getElementById("create-btn");
const startBtn = document.getElementById("start-btn");

const messageList = document.getElementById("message-list");
const sidebarTickets = document.getElementById("sidebar-tickets");
const ticketRows = document.getElementById("ticket-rows");
const statOpen = document.getElementById("stat-open");
const statProgress = document.getElementById("stat-progress");
const statResolved = document.getElementById("stat-resolved");

// ---- Profile dropdown ----

profileBtn.addEventListener("click", () => {
  profileMenu.classList.toggle("open");
});

document.addEventListener("click", (e) => {
  if (!profileBtn.contains(e.target) && !profileMenu.contains(e.target)) {
    profileMenu.classList.remove("open");
  }
});

logoutBtn.addEventListener("click", async () => {
  await fetch(`${API_BASE}/auth/logout`, { method: "POST" });
  alert("Logged out (mock) — wire this to a real redirect once auth is built.");
});

settingsBtn.addEventListener("click", () => {
  alert("Settings screen not built yet — placeholder for now.");
});

// ---- View switching ----

ticketsBtn.addEventListener("click", async () => {
  chatView.classList.add("hidden");
  dashboardView.classList.remove("hidden");
  await loadTickets();
});

backBtn.addEventListener("click", () => {
  dashboardView.classList.add("hidden");
  chatView.classList.remove("hidden");
});

// ---- Conversation ----

startBtn.addEventListener("click", async () => {
  const res = await fetch(`${API_BASE}/session/start`, { method: "POST" });
  const data = await res.json();
  sessionId = data.session_id;

  addMessage("user", "My VPN isn't working");

  const msgRes = await fetch(`${API_BASE}/message`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, text: "My VPN isn't working" }),
  });
  const msgData = await msgRes.json();
  addMessage("agent", msgData.text);
});

function addMessage(role, text) {
  const el = document.createElement("div");
  el.className = `message ${role}`;
  el.innerHTML = `<p class="role">${role === "user" ? "User" : "Agent"}</p><p>${text}</p>`;
  messageList.appendChild(el);
}

// ---- Tickets ----

async function loadTickets() {
  const res = await fetch(`${API_BASE}/tickets`);
  const tickets = await res.json();
  renderSidebarTickets(tickets);
  renderDashboardTickets(tickets);
}

function renderSidebarTickets(tickets) {
  sidebarTickets.innerHTML = "";
  tickets.slice(0, 3).forEach((t) => {
    const el = document.createElement("div");
    el.className = "ticket-card";
    el.innerHTML = `<div>#${t.id} &middot; ${t.issue}</div><div class="status">Status: ${t.status.replace("_", " ")}</div>`;
    sidebarTickets.appendChild(el);
  });
}

function renderDashboardTickets(tickets) {
  ticketRows.innerHTML = "";
  let open = 0, inProgress = 0, resolved = 0;

  tickets.forEach((t) => {
    if (t.status === "open") open++;
    if (t.status === "in_progress") inProgress++;
    if (t.status === "resolved") resolved++;

    const row = document.createElement("div");
    row.className = "ticket-row";
    row.innerHTML = `
      <span>#${t.id}</span>
      <span>${t.issue}</span>
      <span class="status-${t.status}">${t.status.replace("_", " ")}</span>
      <span>${t.opened}</span>
      <button class="delete-btn" data-id="${t.id}" aria-label="Delete ticket ${t.id}">Delete</button>
    `;
    ticketRows.appendChild(row);
  });

  statOpen.textContent = open;
  statProgress.textContent = inProgress;
  statResolved.textContent = resolved;

  document.querySelectorAll(".delete-btn").forEach((btn) => {
    btn.addEventListener("click", async () => {
      await fetch(`${API_BASE}/tickets/${btn.dataset.id}`, { method: "DELETE" });
      await loadTickets();
    });
  });
}

createBtn.addEventListener("click", async () => {
  const issue = prompt("Describe the issue:", "New ticket");
  if (!issue) return;
  await fetch(`${API_BASE}/tickets`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ issue }),
  });
  await loadTickets();
});

// Initial load so the sidebar isn't empty on page load
loadTickets();
