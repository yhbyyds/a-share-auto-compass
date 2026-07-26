"use client";

import { useState } from "react";

function safeDestination() {
  const value = new URLSearchParams(window.location.search).get("next");
  if (!value || !value.startsWith("/") || value.startsWith("//")) {
    return "/index.html";
  }
  return value;
}

export default function LoginPage() {
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [showPassword, setShowPassword] = useState(false);

  async function submit(event) {
    event.preventDefault();
    setError("");
    setLoading(true);
    const form = new FormData(event.currentTarget);
    try {
      const response = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          username: form.get("username"),
          password: form.get("password"),
        }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "登录失败");
      window.location.replace(safeDestination());
    } catch (requestError) {
      setError(requestError.message || "登录失败，请稍后再试");
      setLoading(false);
    }
  }

  return (
    <>
      <link rel="stylesheet" href="/login.css" />
      <main className="login-shell">
        <section className="login-copy">
          <div className="login-brand">
            <span>北</span>
            <div>
              <strong>A股下周罗盘</strong>
              <small>PRIVATE RESEARCH WORKSPACE</small>
            </div>
          </div>
          <p className="login-eyebrow">SECURE ACCESS · VERSION 11</p>
          <h1>把判断留在<br /><em>安全边界内</em></h1>
          <p className="login-intro">
            预测数据、行业曲线与自动更新结果仅在通过认证后加载。
            登录不会提高模型准确率，也不构成收益保证。
          </p>
          <div className="login-points">
            <span>12小时安全会话</span>
            <span>HttpOnly Cookie</span>
            <span>服务端密码校验</span>
          </div>
        </section>

        <section className="login-card" aria-labelledby="login-title">
          <div className="login-card-head">
            <span className="login-lock" aria-hidden="true">↗</span>
            <div>
              <p>ACCOUNT ACCESS</p>
              <h2 id="login-title">登录研究系统</h2>
            </div>
          </div>
          <form onSubmit={submit}>
            <label>
              <span>账号</span>
              <input
                name="username"
                type="text"
                autoComplete="username"
                maxLength="80"
                required
                autoFocus
              />
            </label>
            <label>
              <span>密码</span>
              <div className="password-field">
                <input
                  name="password"
                  type={showPassword ? "text" : "password"}
                  autoComplete="current-password"
                  maxLength="256"
                  required
                />
                <button
                  type="button"
                  onClick={() => setShowPassword((value) => !value)}
                  aria-label={showPassword ? "隐藏密码" : "显示密码"}
                >
                  {showPassword ? "隐藏" : "显示"}
                </button>
              </div>
            </label>
            {error ? <p className="login-error" role="alert">{error}</p> : null}
            <button className="login-submit" type="submit" disabled={loading}>
              {loading ? "正在验证…" : "进入罗盘"}
            </button>
          </form>
          <p className="login-footnote">
            连续输错会触发临时限制。请勿使用银行卡、邮箱或其他网站的相同密码。
          </p>
        </section>
      </main>
    </>
  );
}
