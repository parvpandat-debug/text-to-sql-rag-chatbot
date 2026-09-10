let isLoginMode = true;

const authModal = document.getElementById("authModal");
const authForm = document.getElementById("authForm");
const authTitle = document.getElementById("authTitle");
const authSubtitle = document.getElementById("authSubtitle");
const authToggleLink = document.getElementById("authToggleLink");
const toggleMsg = document.getElementById("toggleMsg");
const appLayout = document.getElementById("appLayout");
const userDisplay = document.getElementById("userDisplay");
const logoutBtn = document.getElementById("logoutBtn");

const chatForm = document.getElementById("chatForm");
const userInput = document.getElementById("userInput");
const messagesContainer = document.getElementById("messagesContainer");
const historyList = document.getElementById("historyList");
const sendBtn = document.getElementById("sendBtn");

// Toggle Login / Register View
authToggleLink.onclick = (e) => {
  e.preventDefault();
  isLoginMode = !isLoginMode;
  authTitle.innerText = isLoginMode ? "Sign In" : "Create Account";
  authSubtitle.innerText = isLoginMode 
    ? "Log in to access your persistent database queries" 
    : "Register to track your SQL conversation logs";
  authForm.querySelector("button").innerText = isLoginMode ? "Sign In" : "Register";
  toggleMsg.innerText = isLoginMode ? "Don't have an account?" : "Already have an account?";
  authToggleLink.innerText = isLoginMode ? "Sign Up" : "Sign In";
};

// Handle Authentication Submission via httpOnly cookies (Phase 1, item 7)
authForm.onsubmit = async (e) => {
  e.preventDefault();
  const username = document.getElementById("authUsername").value.trim();
  const password = document.getElementById("authPassword").value.trim();
  const endpoint = isLoginMode ? "/api/auth/login" : "/api/auth/register";

  try {
    const res = await fetch(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Authentication failed");

    // Cookie is set by server with httpOnly and SameSite flags
    await initApp();
  } catch (err) {
    alert(err.message);
  }
};

logoutBtn.onclick = async () => {
  try {
    await fetch("/api/auth/logout", { method: "POST" });
  } catch (err) {
    console.error("Logout request error:", err);
  }
  appLayout.style.display = "none";
  authModal.style.display = "flex";
};

function appendMessage(role, text, sql = null) {
  const msgWrapper = document.createElement("div");
  msgWrapper.className = `message ${role}-message`;

  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.innerText = text;
  msgWrapper.appendChild(bubble);

  if (sql) {
    const sqlBox = document.createElement("pre");
    sqlBox.className = "sql-block";
    sqlBox.textContent = `SQL: ${sql}`;
    msgWrapper.appendChild(sqlBox);
  }

  messagesContainer.appendChild(msgWrapper);
  messagesContainer.scrollTop = messagesContainer.scrollHeight;
}

// Check session status and restore history via secure cookie
async function initApp() {
  try {
    // 1. Verify session with /api/auth/me
    const meRes = await fetch("/api/auth/me");
    if (!meRes.ok) {
      authModal.style.display = "flex";
      appLayout.style.display = "none";
      return;
    }
    const meData = await meRes.json();

    // 2. Session is valid - show layout
    authModal.style.display = "none";
    appLayout.style.display = "flex";
    userDisplay.innerText = meData.username;

    messagesContainer.innerHTML = "";
    historyList.innerHTML = "";

    // Welcome greeting
    appendMessage("assistant", `Welcome back, ${meData.username}! Ask any question about your database.`);

    // 3. Fetch long-term history
    const historyRes = await fetch("/api/history");
    if (historyRes.ok) {
      const data = await historyRes.json();
      data.history.forEach((turn) => {
        appendMessage("user", turn.question);
        appendMessage("assistant", turn.answer, turn.generated_sql);

        // Add to sidebar
        const li = document.createElement("li");
        li.className = "history-item";
        li.innerHTML = `
          <span class="history-q">${turn.question}</span>
          <span class="history-date">${turn.created_at}</span>
        `;
        li.onclick = () => {
          userInput.value = turn.question;
          userInput.focus();
        };
        historyList.prepend(li);
      });
    }
  } catch (err) {
    authModal.style.display = "flex";
    appLayout.style.display = "none";
  }
}

chatForm.onsubmit = async (e) => {
  e.preventDefault();
  const question = userInput.value.trim();
  if (!question) return;

  appendMessage("user", question);
  userInput.value = "";
  userInput.disabled = true;
  sendBtn.disabled = true;

  const loadingWrapper = document.createElement("div");
  loadingWrapper.className = "message assistant-message";
  loadingWrapper.innerHTML = `<div class="bubble">Evaluating context & executing query...</div>`;
  messagesContainer.appendChild(loadingWrapper);
  messagesContainer.scrollTop = messagesContainer.scrollHeight;

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify({ question })
    });

    const data = await res.json();
    loadingWrapper.remove();

    if (res.ok) {
      appendMessage("assistant", data.answer, data.query);

      // Add to sidebar list
      const li = document.createElement("li");
      li.className = "history-item";
      li.innerHTML = `
        <span class="history-q">${question}</span>
        <span class="history-date">Just now</span>
      `;
      li.onclick = () => {
        userInput.value = question;
        userInput.focus();
      };
      historyList.prepend(li);
    } else {
      appendMessage("assistant", `Error: ${data.detail || "Failed to process query."}`);
    }
  } catch (err) {
    loadingWrapper.remove();
    appendMessage("assistant", `Network error: ${err.message}`);
  } finally {
    userInput.disabled = false;
    sendBtn.disabled = false;
    userInput.focus();
  }
};

// Initialize on page load
initApp();