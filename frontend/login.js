const API_BASE = "http://127.0.0.1:8000/api";

let sessionId = null;
const state = {
  authMode: "login",
  user: null,
};

const authScreen = document.getElementById("auth-screen");
const appShell = document.getElementById("app-shell");
const authForm = document.getElementById("auth-form");
const authMessageEl = document.getElementById("auth-message");
const authSubmitBtn = document.getElementById("auth-submit");
const authSwitchBtn = document.getElementById("auth-switch");
const authSwitchLabel = document.getElementById("auth-switch-label");
const fullNameRow = document.getElementById("full-name-row");
const confirmPasswordRow = document.getElementById("confirm-password-row");

const profileBtn = document.getElementById("profile-btn");
const profileMenu = document.getElementById("profile-menu");
const logoutBtn = document.getElementById("logout-btn");
const settingsBtn = document.getElementById("settings-btn");
const sessionLabel = document.getElementById("session-label");

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

function setAuthMessage(message, type = "error") {
  authMessageEl.textContent = message || "";
  authMessageEl.classList.toggle("error", type === "error");
  authMessageEl.classList.toggle("success", type === "success");
}

function setAuthMode(mode) {
  state.authMode = mode;
  const isSignup = mode === "signup";

  fullNameRow.classList.toggle("hidden", !isSignup);
  confirmPasswordRow.classList.toggle("hidden", !isSignup);

  document.getElementById("full-name").required = isSignup;
  document.getElementById("confirm-password").required = isSignup;

  document.querySelectorAll(".auth-tab").forEach((tab) => {
    tab.classList.toggle("active", tab.dataset.mode === mode);
  });

  if (isSignup) {
    authSubmitBtn.textContent = "Create account";
    authSwitchLabel.textContent = "Already have an account?";
    authSwitchBtn.textContent = "Login";
    document.getElementById("remember-me").closest(".form-row").classList.add("hidden");
  } else {
    authSubmitBtn.textContent = "Log in";
    authSwitchLabel.textContent = "Need an account?";
    authSwitchBtn.textContent = "Sign up";
    document.getElementById("remember-me").closest(".form-row").classList.remove("hidden");
  }
}

function getInitials(name) {
  const cleanName = (name || "Customer").trim();
  const parts = cleanName.split(/\s+/).filter(Boolean);
  if (parts.length >= 2) {
    return `${parts[0][0]}${parts[1][0]}`.toUpperCase();
  }
  return cleanName.slice(0, 2).toUpperCase() || "CU";
}

function updateProfile() {
  if (!state.user) {
    profileBtn.textContent = "CU";
    profileBtn.title = "Customer";
    sessionLabel.textContent = "Logged out";
    return;
  }

  profileBtn.textContent = getInitials(state.user.full_name || state.user.email);
  profileBtn.title = state.user.full_name || state.user.email;
  sessionLabel.textContent = `Welcome, ${state.user.full_name?.split(" ")[0] || "customer"}`;
}

function showAppView() {
  authScreen.classList.add("hidden");
  appShell.classList.remove("hidden");
  updateProfile();
  loadTickets();
}

function showAuthView() {
  appShell.classList.add("hidden");
  authScreen.classList.remove("hidden");
  setAuthMessage("");
  updateProfile();
}

async function apiFetch(path, options = {}) {
  const requestHeaders = { Accept: "application/json", ...(options.headers || {}) };

  if (options.body && !requestHeaders["Content-Type"]) {
    requestHeaders["Content-Type"] = "application/json";
  }

  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    credentials: "include",
    headers: requestHeaders,
  });

  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("application/json") ? await response.json() : await response.text();

  if (!response.ok) {
    const message = payload?.detail || payload?.message || "Request failed.";
    const errorMessage = Array.isArray(message) ? message.map((entry) => entry.msg || entry).join(" ") : message;
    throw new Error(errorMessage);
  }

  return payload;
}

async function checkAuthState() {
  try {
    state.user = await apiFetch("/auth/me");
    showAppView();
  } catch (error) {
    state.user = null;
    showAuthView();
  }
}

async function loadTickets() {
  try {
    const tickets = await apiFetch("/tickets");
    renderSidebarTickets(tickets);
    renderDashboardTickets(tickets);
  } catch (error) {
    console.error("Unable to load tickets:", error);
  }
}

function addMessage(role, text) {
  const el = document.createElement("div");
  el.className = `message ${role}`;
  el.innerHTML = `<p class="role">${role === "user" ? "User" : "Agent"}</p><p>${text}</p>`;
  messageList.appendChild(el);
}

function renderSidebarTickets(tickets) {
  sidebarTickets.innerHTML = "";
  tickets.slice(0, 3).forEach((ticket) => {
    const el = document.createElement("div");
    el.className = "ticket-card";
    el.innerHTML = `<div>#${ticket.id} &middot; ${ticket.issue}</div><div class="status">Status: ${ticket.status.replace("_", " ")}</div>`;
    sidebarTickets.appendChild(el);
  });
}

function renderDashboardTickets(tickets) {
  ticketRows.innerHTML = "";
  let open = 0;
  let inProgress = 0;
  let resolved = 0;

  tickets.forEach((ticket) => {
    if (ticket.status === "open") open += 1;
    if (ticket.status === "in_progress") inProgress += 1;
    if (ticket.status === "resolved") resolved += 1;

    const row = document.createElement("div");
    row.className = "ticket-row";
    row.innerHTML = `
      <span>#${ticket.id}</span>
      <span>${ticket.issue}</span>
      <span class="status-${ticket.status}">${ticket.status.replace("_", " ")}</span>
      <span>${ticket.opened}</span>
      <button class="delete-btn" data-id="${ticket.id}" aria-label="Delete ticket ${ticket.id}">Delete</button>
    `;
    ticketRows.appendChild(row);
  });

  statOpen.textContent = open;
  statProgress.textContent = inProgress;
  statResolved.textContent = resolved;

  document.querySelectorAll(".delete-btn").forEach((btn) => {
    btn.addEventListener("click", async () => {
      await apiFetch(`/tickets/${btn.dataset.id}`, { method: "DELETE" });
      await loadTickets();
    });
  });
}

async function handleAuthSubmit(event) {
  event.preventDefault();
  const formData = new FormData(authForm);
  const fullName = (formData.get("full_name") || "").toString().trim();
  const email = (formData.get("email") || "").toString().trim();
  const password = (formData.get("password") || "").toString();
  const confirmPassword = (formData.get("confirm_password") || "").toString();

  try {
    if (state.authMode === "signup") {
      if (!fullName) {
        throw new Error("Full name is required.");
      }
      if (password !== confirmPassword) {
        throw new Error("Passwords do not match.");
      }

      await apiFetch("/auth/register", {
        method: "POST",
        body: JSON.stringify({ full_name: fullName, email, password, confirm_password: confirmPassword }),
      });

      setAuthMode("login");
      document.getElementById("email").value = email;
      setAuthMessage("Account created successfully. You can now log in.", "success");
      return;
    }

    if (!email || !password) {
      throw new Error("Email and password are required.");
    }

    const user = await apiFetch("/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    });

    state.user = user;
    showAppView();
  } catch (error) {
    setAuthMessage(error.message || "Unable to process your request.");
  }
}

function bindAuthControls() {
  document.querySelectorAll(".auth-tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      setAuthMode(tab.dataset.mode);
      setAuthMessage("");
    });
  });

  authSwitchBtn.addEventListener("click", () => {
    const nextMode = state.authMode === "login" ? "signup" : "login";
    setAuthMode(nextMode);
    setAuthMessage("");
  });

  authForm.addEventListener("submit", handleAuthSubmit);

  document.querySelectorAll(".toggle-password").forEach((button) => {
    button.addEventListener("click", () => {
      const input = document.getElementById(button.dataset.target);
      if (!input) return;
      const isPassword = input.type === "password";
      input.type = isPassword ? "text" : "password";
      button.textContent = isPassword ? "Hide" : "Show";
      button.setAttribute("aria-label", isPassword ? "Hide password" : "Show password");
    });
  });
}

profileBtn.addEventListener("click", () => {
  profileMenu.classList.toggle("open");
});

document.addEventListener("click", (event) => {
  if (!profileBtn.contains(event.target) && !profileMenu.contains(event.target)) {
    profileMenu.classList.remove("open");
  }
});

logoutBtn.addEventListener("click", async () => {
  try {
    await apiFetch("/auth/logout", { method: "POST" });
    state.user = null;
    showAuthView();
  } catch (error) {
    console.error("Logout failed:", error);
    showAuthView();
  }
});

settingsBtn.addEventListener("click", () => {
  alert("Settings screen not built yet — placeholder for now.");
});

ticketsBtn.addEventListener("click", async () => {
  chatView.classList.add("hidden");
  dashboardView.classList.remove("hidden");
  await loadTickets();
});

backBtn.addEventListener("click", () => {
  dashboardView.classList.add("hidden");
  chatView.classList.remove("hidden");
});

startBtn.addEventListener("click", async () => {
  const response = await apiFetch("/session/start", { method: "POST" });
  sessionId = response.session_id;

  addMessage("user", "My VPN isn't working");

  const msg = await apiFetch("/message", {
    method: "POST",
    body: JSON.stringify({ session_id: sessionId, text: "My VPN isn't working" }),
  });

  addMessage("agent", msg.text);
});

createBtn.addEventListener("click", async () => {
  const issue = prompt("Describe the issue:", "New ticket");
  if (!issue) return;

  await apiFetch("/tickets", {
    method: "POST",
    body: JSON.stringify({ issue }),
  });

  await loadTickets();
});

bindAuthControls();
setAuthMode("login");
checkAuthState();
