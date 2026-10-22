document.addEventListener("DOMContentLoaded", () => {
  const form = document.getElementById("chat-form");
  const input = document.getElementById("msg-input");
  const win = document.getElementById("chat-window");
  if (!form) return;

  function append(role, text) {
    const div = document.createElement("div");
    div.className = "msg " + role;
    div.textContent = text;
    win.appendChild(div);
    win.scrollTop = win.scrollHeight;
  }

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const text = input.value.trim();
    if (!text) return;
    append("user", text);
    input.value = "";
    input.disabled = true;

    try {
      const res = await fetch("/send_message", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text })
      });
      const data = await res.json();
      if (data.reply) append("assistant", data.reply);
      else append("assistant", data.error || "Something went wrong.");
    } catch (err) {
      append("assistant", "Connection error. Please try again.");
    }
    input.disabled = false;
    input.focus();
    win.scrollTop = win.scrollHeight;
  });
});
