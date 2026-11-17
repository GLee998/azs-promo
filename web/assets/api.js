// Адрес API. Если сайт открыт с GitHub Pages — обращаемся к серверу на Render,
// иначе (локально или на самом Render) API живёт на том же адресе.
const API_BASE = location.hostname.endsWith('github.io')
  ? 'https://azs-promo-api.onrender.com'
  : '';

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

// Бесплатный сервер на Render засыпает: будим его, пока человек заполняет форму.
api('/api/health').catch(() => {});
