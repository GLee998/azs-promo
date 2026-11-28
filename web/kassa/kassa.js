// Служебный интерфейс кассира: заявки клиентов, подтверждение чека (с сувениром или без), отмена.

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

// Поиск по коду идёт на сервере и находит заявку любой АЗС. Ждём паузу в наборе, чтобы не слать запрос на каждую букву.
let searchTimer = null;
$('q').addEventListener('input', () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(load, 300);
});

// ---------- очередь ----------

const REASONS = ['Чек не подходит под условия акции', 'В чеке нет комбо', 'Клиент передумал'];

async function load() {
  // Не перерисовываем, пока кассир вводит реквизиты чека или выбирает причину отмены.
  if (document.activeElement && document.activeElement.closest('#queue input')) return;
  if (document.querySelector('#queue [data-reasons]:not(.hidden)')) return;
  const q = $('q').value.replace(/\s/g, '');
  try {
    data = await kassaApi('/api/cashier/queue' + (q.length >= 3 ? '?q=' + encodeURIComponent(q) : ''));
    render(q.length >= 3);
    renderSouvenirs(data.souvenirs_left);
  } catch (err) {
    $('queue').innerHTML = `<div class="card error">${esc(err.message)}</div>`;
  }
}

function render(searching = false) {
  // Заявки своей АЗС сверху, затем заявки без АЗС (QR без номера кассы).
  const list = [...data.waiting.filter(r => r.station === station), ...data.waiting.filter(r => r.station !== station)];
  $('queue').innerHTML = list.length ? list.map(card).join('') :
    `<div class="card empty"><img src="shared/img/caravan-truck.svg" alt="">${searching
      ? 'Заявки с таким кодом нет. Проверьте код на экране клиента или попросите обновить страницу.'
      : 'Сейчас нет заявок на подтверждение. Новые появятся здесь сами.'}</div>`;
  list.forEach(r => loadThumb(r.id));

  $('recent').innerHTML = data.recent.length ? data.recent.map(r => `
    <div class="done-line">${plate(r.code, 'sm')}<span>${esc(r.phone)}</span>
      <span class="tag issued">подтверждён${r.issued_station ? ' на АЗС №' + esc(r.issued_station) : ''}</span>
      ${r.souvenir ? '<span class="tag issued">сувенир</span>' : ''}
      <span class="tag pink">${esc(r.entry || '')}</span></div>`).join('')
    : '<p class="hint" style="margin:0">Пока ничего не подтверждено.</p>';
}

function renderSouvenirs(left) {
  const el = $('souvenirsLeft');
  if (typeof left !== 'number') { show(el, false); return; }
  el.textContent = left > 0 ? `Сувениров осталось по всей акции: ${left}`
    : 'Сувениры закончились. Чеки подтверждаются без сувенира, клиенты по-прежнему участвуют в розыгрыше.';
  el.classList.toggle('out', left === 0);
  show(el);
}

// Сверху то, что кассир сверяет с экраном клиента: код, телефон, время. Под ними кнопки.
// Фото чека — маленькое превью справа, полное открывается по нажатию.
function card(r) {
  const time = new Date(r.created_at).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
  const mine = station && r.station === station;
  return `
  <article class="card kassa-card${mine ? ' mine' : ''}" data-id="${r.id}">
    <div class="kassa-main">
      <div class="who">${plate(r.code)}<span class="phone">${esc(r.phone)}</span></div>
      <div class="meta"><span>в ${time}</span>${r.station ? `<span>QR АЗС №${esc(r.station)}</span>` : ''}${r.receipt_sum ? `<span>${esc(r.receipt_sum)} сом</span>` : ''}</div>
      ${r.needs_fiscal ? `
        <p class="qr-status warn" style="margin-top:10px">QR на фото не распознан. Введите реквизиты с бумажного чека клиента.</p>
        <div class="fiscal">
          <div><label>ФН</label><input data-f="fn" inputmode="numeric"></div>
          <div><label>ФД</label><input data-f="fd" inputmode="numeric"></div>
          <div><label>ФМ</label><input data-f="fm" inputmode="numeric"></div>
        </div>` : ''}
      <p class="souvenir-note ${r.souvenir_due ? 'give' : 'none'}">${r.souvenir_due
        ? 'Выдать сувенир: этот номер его ещё не получал'
        : 'Без сувенира: на этот номер он уже выдан или запас закончился'}</p>
      <div class="actions">
        ${r.souvenir_due
          ? `<button class="btn small" type="button" data-act="issue">Подтвердить и выдать сувенир</button>
             <button class="btn small secondary" type="button" data-act="issue-nosouvenir">Подтвердить без сувенира</button>`
          : '<button class="btn small" type="button" data-act="issue">Подтвердить чек</button>'}
        <button class="btn small secondary" type="button" data-act="cancel">Отменить заявку</button>
      </div>
      <div class="reasons hidden" data-reasons>
        <p class="hint">Почему отменяете? Клиент увидит причину.</p>
        <div class="actions">
          ${REASONS.map(t => `<button class="btn small secondary" type="button" data-reason="${esc(t)}">${esc(t)}</button>`).join('')}
          <button class="btn small secondary" type="button" data-act="other">Другое…</button>
          <button class="btn small secondary" type="button" data-act="keep">Не отменять</button>
        </div>
        <div class="other-reason hidden" data-other>
          <input type="text" maxlength="200" placeholder="Причина отмены" aria-label="Причина отмены">
          <button class="btn small" type="button" data-act="cancel-other">Отменить заявку</button>
        </div>
      </div>
      <div class="error hidden" data-err></div>
      ${r.needs_fiscal ? '' : `<div class="fiscal-read">Чек: ФН ${esc(r.fn)}, ФД ${esc(r.fd)}, ФМ ${esc(r.fm)}</div>`}
    </div>
    <button class="thumb" type="button" data-photo="${r.id}" aria-label="Открыть фото чека"><img alt=""></button>
  </article>`;
}

// Фото закрыто входом, поэтому грузим через fetch с заголовком авторизации и показываем blob-адрес.
async function photoUrl(id, size) {
  const key = `${id}:${size}`;
  if (!photoUrls.has(key)) {
    const res = await kassaApi(`/api/cashier/registrations/${id}/photo?size=${size}`, { raw: true });
    photoUrls.set(key, URL.createObjectURL(await res.blob()));
  }
  return photoUrls.get(key);
}

async function loadThumb(id) {
  try {
    const url = await photoUrl(id, 'thumb');
    const img = document.querySelector(`[data-photo="${id}"] img`);
    if (img) img.src = url;
  } catch {}
}

async function cancel(cardEl, reason) {
  await kassaApi(`/api/cashier/registrations/${cardEl.dataset.id}/cancel`, { method: 'POST', json: { reason } });
  toast('Заявка отменена, сувенир не выдавайте');
}

$('queue').addEventListener('click', async e => {
  const thumb = e.target.closest('[data-photo]');
  if (thumb) {
    try {
      $('lightbox').querySelector('img').src = await photoUrl(thumb.dataset.photo, 'full');
      show($('lightbox'));
    } catch (err) { toast(err.message); }
    return;
  }
  const el = e.target.closest('button');
  if (!el || !el.closest('[data-id]')) return;
  const cardEl = el.closest('[data-id]');
  const errEl = cardEl.querySelector('[data-err]');
  const reasons = cardEl.querySelector('[data-reasons]');
  const other = cardEl.querySelector('[data-other]');
  const action = el.dataset.reason ? 'reason' : el.dataset.act;
  show(errEl, false);

  // Выбор причины отмены: панель с кнопками вместо системного окна prompt().
  if (action === 'cancel') { show(reasons, reasons.classList.contains('hidden')); return; }
  if (action === 'keep') { show(reasons, false); show(other, false); return; }
  if (action === 'other') { show(other); other.querySelector('input').focus(); return; }

  try {
    el.disabled = true;
    if (action === 'issue' || action === 'issue-nosouvenir') {
      // АЗС сервер берёт из учётки кассира или из QR клиента; положен ли сувенир, решает сервер.
      const body = { souvenir: action === 'issue' };
      cardEl.querySelectorAll('[data-f]').forEach(i => { body[i.dataset.f] = i.value; });
      const r = await kassaApi(`/api/cashier/registrations/${cardEl.dataset.id}/issue`, { method: 'POST', json: body });
      toast(r.souvenir ? `Чек подтверждён. Выдайте сувенир. Купон ${r.entry}`
        : `Чек подтверждён без сувенира. Купон ${r.entry}`);
    } else if (action === 'reason') {
      await cancel(cardEl, el.dataset.reason);
    } else if (action === 'cancel-other') {
      const reason = other.querySelector('input').value.trim();
      if (!reason) throw new Error('Напишите причину отмены');
      await cancel(cardEl, reason);
    } else {
      return;
    }
    show(reasons, false);
    await load();
  } catch (err) {
    errEl.textContent = err.message;
    show(errEl);
  } finally {
    el.disabled = false;
  }
});

$('lightbox').addEventListener('click', () => show($('lightbox'), false));

if (token) enter();
