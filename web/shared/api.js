// Адрес API берётся из shared/config.js.
const API_BASE = window.API_BASE || '';

async function api(path, { method = 'GET', json, form, token, raw } = {}) {
  const headers = {};
  if (token) headers.Authorization = 'Bearer ' + token;
  let body;
  if (json) { headers['Content-Type'] = 'application/json'; body = JSON.stringify(json); }
  if (form) body = form;

  let res;
  try {
    res = await fetch(API_BASE + path, { method, headers, body });
  } catch {
    throw new Error('Нет связи с сервером. Проверьте интернет и попробуйте ещё раз.');
  }
  if (!res.ok) {
    let msg = 'Ошибка сервера (' + res.status + ')';
    try {
      const d = await res.json();
      if (typeof d.detail === 'string') msg = d.detail;
    } catch {}
    const err = new Error(msg);
    err.status = res.status;
    throw err;
  }
  return raw ? res : res.json();
}

// Вход сотрудника по личному логину. Реестр и розыгрыш открыты только маркетингу (requireAdmin).
async function staffLogin(login, password, requireAdmin = false) {
  const r = await api('/api/auth/login', { method: 'POST', json: { login, password } });
  if (requireAdmin && r.role !== 'admin') {
    staffLogout(r.token);
    throw new Error('Нет доступа для этой учётной записи');
  }
  return r;
}

// Закрываем сессию на сервере; если сети нет, она истечёт сама.
function staffLogout(token) {
  if (token) api('/api/auth/logout', { method: 'POST', token }).catch(() => {});
}

// Бесплатный сервер на Render засыпает: будим его, пока человек заполняет форму.
api('/api/health').catch(() => {});
