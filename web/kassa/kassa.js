// Служебный интерфейс кассира: одобренные системой заявки, выдача машинки, отмена.

const $ = id => document.getElementById(id);
const TOKEN_KEY = 'karavan_cashier_token';
const STATION_KEY = 'karavan_cashier_station';
let token = null;
let station = '';  // АЗС из учётки кассира; у маркетинга пусто — видны все АЗС
let data = { waiting: [], recent: [] };
let refreshTimer = null;
const photoUrls = new Map();

try {
  token = sessionStorage.getItem(TOKEN_KEY);
  station = sessionStorage.getItem(STATION_KEY) || '';
} catch {}

function show(el, on = true) { el.classList.toggle('hidden', !on); }

const esc = s => String(s ?? '').replace(/[&<>"']/g, c =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const plate = (code, size = 'md') =>
  `<span class="plate ${size}"><span class="num">${esc(code)}</span><span class="reg"><i></i><span>KG</span></span></span>`;

function toast(text) {
  $('toast').textContent = text;
  show($('toast'));
  clearTimeout(toast.t);
  toast.t = setTimeout(() => show($('toast'), false), 3500);
}

function kassaApi(path, opts = {}) {
  return api(path, { ...opts, token }).catch(err => {
    if (err.status === 401) logout();
    throw err;
  });
}

// ---------- вход ----------

$('loginForm').addEventListener('submit', async e => {
  e.preventDefault();
  show($('loginErr'), false);
  $('loginBtn').disabled = true;
  try {
    const r = await staffLogin($('login').value, $('password').value);
    token = r.token;
    station = r.station || '';
    $('password').value = '';
    try {
      sessionStorage.setItem(TOKEN_KEY, token);
      sessionStorage.setItem(STATION_KEY, station);
    } catch {}
    enter();
  } catch (err) {
    $('loginErr').textContent = err.message;
    show($('loginErr'));
  } finally {
    $('loginBtn').disabled = false;
  }
});

function logout() {
  staffLogout(token);
  token = null;
  station = '';
  clearInterval(refreshTimer);
  try {
    sessionStorage.removeItem(TOKEN_KEY);
    sessionStorage.removeItem(STATION_KEY);
  } catch {}
  $('kassaView').hidden = true;
  show($('toolbar'), false);
  show($('loginView'));
}

function enter() {
  $('stationChip').textContent = station ? `АЗС №${station}` : 'Все АЗС';
  show($('loginView'), false);
  $('kassaView').hidden = false;
  show($('toolbar'));
  load();
  clearInterval(refreshTimer);
  refreshTimer = setInterval(load, 5000);
}

$('logout').addEventListener('click', logout);
$('q').addEventListener('input', render);

// ---------- очередь ----------

async function load() {
  // Не перерисовываем, пока кассир вводит реквизиты чека.
  if (document.activeElement && document.activeElement.closest('#queue input')) return;
  try {
    data = await kassaApi('/api/cashier/queue');
    render();
  } catch (err) {
    $('queue').innerHTML = `<div class="card error">${esc(err.message)}</div>`;
  }
}

function render() {
  const q = $('q').value.replace(/\s/g, '').toUpperCase();
  let list = data.waiting.filter(r => !q || r.code.replace(/\s/g, '').includes(q));
  // Заявки своей АЗС сверху.
  list = [...list.filter(r => r.station === station), ...list.filter(r => r.station !== station)];

  $('queue').innerHTML = list.length ? list.map(r => card(r, station)).join('') :
    `<div class="card empty"><img src="shared/img/caravan-truck.svg" alt="">${q
      ? 'Заявки с таким кодом нет. Проверьте код на экране клиента или попросите обновить страницу.'
      : 'Сейчас никто не ждёт машинку. Новые заявки появятся здесь сами.'}</div>`;
  list.forEach(r => loadPhoto(r.id));

  $('recent').innerHTML = data.recent.length ? data.recent.map(r => `
    <div class="done-line">${plate(r.code, 'sm')}<span>${esc(r.phone)}</span>
      <span class="tag issued">выдано${r.issued_station ? ' на АЗС №' + esc(r.issued_station) : ''}</span>
      <span class="tag pink">${esc(r.entry || '')}</span></div>`).join('')
    : '<p class="hint" style="margin:0">Пока ничего не выдано.</p>';
}

function card(r, station) {
  const time = new Date(r.created_at).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
  const mine = station && r.station === station;
  return `
  <article class="card kassa-card${mine ? ' mine' : ''}" data-id="${r.id}">
    <div><img data-photo="${r.id}" alt="Фото чека"></div>
    <div>
      <div class="who">${plate(r.code)}<span class="phone">${esc(r.phone)}</span></div>
      <div class="meta"><span>зарегистрирован в ${time}</span>${r.station ? `<span>QR кассы АЗС №${esc(r.station)}</span>` : ''}${r.receipt_sum ? `<span>${esc(r.receipt_sum)} сом</span>` : ''}</div>
      ${r.needs_fiscal ? `
        <p class="qr-status warn" style="margin-top:10px">QR на фото не распознан. Введите реквизиты с бумажного чека клиента.</p>
        <div class="fiscal">
          <div><label>ФН</label><input data-f="fn" inputmode="numeric"></div>
          <div><label>ФД</label><input data-f="fd" inputmode="numeric"></div>
          <div><label>ФМ</label><input data-f="fm" inputmode="numeric"></div>
        </div>` : `<div class="fiscal-read">Чек: ФН ${esc(r.fn)}, ФД ${esc(r.fd)}, ФМ ${esc(r.fm)}</div>`}
      <div class="actions">
        <button class="btn small" data-act="issue">Машинка выдана</button>
        <button class="btn small secondary" data-act="cancel">Отменить заявку</button>
      </div>
      <div class="error hidden" data-err></div>
    </div>
  </article>`;
}

async function loadPhoto(id) {
  const img = document.querySelector(`img[data-photo="${id}"]`);
  if (!img) return;
  if (!photoUrls.has(id)) {
    try {
      const res = await kassaApi(`/api/cashier/registrations/${id}/photo`, { raw: true });
      photoUrls.set(id, URL.createObjectURL(await res.blob()));
    } catch { return; }
  }
  img.src = photoUrls.get(id);
}

$('queue').addEventListener('click', async e => {
  const el = e.target;
  if (el.dataset.photo && el.src) {
    $('lightbox').querySelector('img').src = el.src;
    show($('lightbox'));
    return;
  }
  const action = el.dataset.act;
  if (!action) return;
  const cardEl = el.closest('[data-id]');
  const errEl = cardEl.querySelector('[data-err]');
  show(errEl, false);
  const id = cardEl.dataset.id;

  try {
    if (action === 'issue') {
      // АЗС выдачи сервер берёт из учётки кассира.
      const body = {};
      cardEl.querySelectorAll('[data-f]').forEach(i => { body[i.dataset.f] = i.value; });
      el.disabled = true;
      const r = await kassaApi(`/api/cashier/registrations/${id}/issue`, { method: 'POST', json: body });
      toast(`Выдано. Клиент участвует в розыгрыше, номер ${r.entry}`);
    } else {
      const reason = prompt('Почему отменяете заявку? Клиент увидит эту причину.', 'Чек не подходит под условия акции');
      if (reason === null) return;
      await kassaApi(`/api/cashier/registrations/${id}/cancel`, { method: 'POST', json: { reason } });
      toast('Заявка отменена, машинку не выдавайте');
    }
    await load();
  } catch (err) {
    el.disabled = false;
    errEl.textContent = err.message;
    show(errEl);
  }
});

$('lightbox').addEventListener('click', () => show($('lightbox'), false));

if (token) enter();
