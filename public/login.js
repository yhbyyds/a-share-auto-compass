const form = document.querySelector("#login-form");
const errorBox = document.querySelector("#login-error");
const submitButton = document.querySelector("#login-submit");
const passwordInput = form.elements.password;
const passwordToggle = document.querySelector("#password-toggle");

function destination() {
  const value = new URLSearchParams(window.location.search).get("next");
  if (!value || !value.startsWith("/") || value.startsWith("//")) {
    return "/index.html";
  }
  return value;
}

passwordToggle.addEventListener("click", () => {
  const visible = passwordInput.type === "text";
  passwordInput.type = visible ? "password" : "text";
  passwordToggle.textContent = visible ? "显示" : "隐藏";
  passwordToggle.setAttribute("aria-label", visible ? "显示密码" : "隐藏密码");
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  errorBox.hidden = true;
  submitButton.disabled = true;
  submitButton.textContent = "正在验证…";
  const data = new FormData(form);
  try {
    const response = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        username: data.get("username"),
        password: data.get("password"),
      }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "登录失败");
    window.location.replace(destination());
  } catch (error) {
    errorBox.textContent = error.message || "登录失败，请稍后再试";
    errorBox.hidden = false;
    submitButton.disabled = false;
    submitButton.textContent = "进入罗盘";
  }
});
