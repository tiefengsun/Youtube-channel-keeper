'use strict';
const form = document.querySelector('#login-form');
const error = document.querySelector('#login-error');
const button = document.querySelector('#login-button');
form.addEventListener('submit', async event => {
  event.preventDefault();
  button.disabled = true;
  button.textContent = '正在登录…';
  error.hidden = true;
  try {
    const response = await fetch('/api/auth/login', {
      method: 'POST', credentials: 'same-origin',
      headers: {'Content-Type': 'application/json', 'X-Local-Request': '1'},
      body: JSON.stringify({username: form.elements.username.value.trim(), password: form.elements.password.value})
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || '登录失败');
    window.location.replace('/');
  } catch (cause) {
    error.textContent = cause.message;
    error.hidden = false;
  } finally {
    button.disabled = false;
    button.innerHTML = '登录工作空间 <span aria-hidden="true">→</span>';
  }
});
