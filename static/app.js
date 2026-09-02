let token = localStorage.getItem("sql_auth_token");
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
  authSubtitle.innerText = isLoginMode ? "Log in to access your persistent database queries" : "Register to track your SQL conversation logs";
  authForm.querySelector("button").innerText = isLoginMode ? "Sign In" : "Register";
  toggleMsg.innerText = isLoginMode ? "Don't have an account?" : "Already have an account?";
  authToggleLink.innerText = isLoginMode ? "Sign Up" : "Sign In";
};

// Handle Authentication Submission
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

    token = data.token;
    localStorage.setItem("sql_auth_token", token);
    initApp();
  } catch (err) {
    alert(err.message);
  }
};

logoutBtn.onclick = () => {
  localStorage.removeItem("sql_auth_token");
  token = null;
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

// Fetch Long-Term History and render to both chat container and sidebar
async function initApp() {
  if (!token) {
    authModal.style.display = "flex";
    appLayout.style.display = "none";
    return;
  }

  try {
    const res = await fetch("/api/history", {
      headers: { Authorization: `Bearer ${token}` }
    });
    if (!res.ok) throw new Error("Unauthorized");

    const data = await res.json();
    authModal.style.display = "none";
    appLayout.style.display = "flex";
    userDisplay.innerText = data.username;

    messagesContainer.innerHTML = "";
    historyList.innerHTML = "";

    // Welcome greeting
    appendMessage("assistant", `Welcome back, ${data.username}! Ask any question about your database.`);

    // Restore conversation from long-term memory
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
  } catch (err) {
    localStorage.removeItem("sql_auth_token");
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
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`
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

initApp();