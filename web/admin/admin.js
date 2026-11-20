// Закрытый реестр маркетинга.

const $ = id => document.getElementById(id);
const TOKEN_KEY = 'karavan_admin_token';
let token = null;
let items = [];
const photoUrls = new Map();

try { token = sessionStorage.getItem(TOKEN_KEY); } catch {}

function show(el, on = true) { el.classList.toggle('hidden', !on); }

const esc = s => String(s ?? '').replace(/[&<>"']/g, c =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const STATUS = {
  waiting: 'Ждёт выдачи',
  issued: 'Машинка выдана',
  cancelled: 'Отменена',
  disqualified: 'Снята с розыгрыша',
};

const plate = n => `<span class="plate sm"><span class="num">${esc(n)}</span><span class="reg"><i></i><span>KG</span></span></span>`;

function adminApi(path, opts = {}) {
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
  $('loginBtn').textContent = 'Проверяем…';
  try {
    token = (await staffLogin($('login').value, $('password').value, true)).token;
    $('password').value = '';
    try { sessionStorage.setItem(TOKEN_KEY, token); } catch {}
    enter();
  } catch (err) {
    $('loginErr').textContent = err.message;
    show($('loginErr'));
  } finally {
    $('loginBtn').disabled = false;
    $('loginBtn').textContent = 'Войти';
  }
});

function logout() {
  staffLogout(token);
  token = null;
  try { sessionStorage.removeItem(TOKEN_KEY); } catch {}
  show($('registryView'), false);
  show($('toolbar'), false);
  show($('loginView'));
}

function enter() {
  show($('loginView'), false);
  show($('registryView'));
  show($('toolbar'));
  load();
}

$('logout').addEventListener('click', logout);
$('refresh').addEventListener('click', () => load());

// ---------- список ----------

// Реестр постранично: фильтр и поиск выполняет сервер, браузер получает одну страницу.
const PER_PAGE = 50;
let page = 1;
let total = 0;

async function load() {
  const params = new URLSearchParams({ page, per_page: PER_PAGE });
  if ($('statusFilter').value) params.set('status', $('statusFilter').value);
  if ($('q').value.trim()) params.set('q', $('q').value.trim());
  try {
    const r = await adminApi('/api/admin/registrations?' + params);
    items = r.items;
    total = r.total;
    renderStats(r.stats);
    render();
  } catch (err) {
    $('list').innerHTML = `<div class="card error">${esc(err.message)}</div>`;
  }
}

async function loadStats() {
  try { renderStats(await adminApi('/api/admin/stats')); } catch {}
}

function renderStats(s) {
  const tiles = [
    [s.total, 'всего регистраций'],
    [s.waiting, 'ждут выдачи'],
    [s.unchecked, 'выдано, покупка не сверена'],
    [s.cancelled, 'отменено'],
    [s.disqualified, 'снято с розыгрыша'],
    [s.issued, 'участников розыгрыша'],
  ];
  $('stats').innerHTML = tiles.map(([n, t], i) =>
    `<div class="stat${i === tiles.length - 1 ? ' accent' : ''}"><b>${n}</b><span>${t}</span></div>`).join('');
}

function render() {
  const filtered = $('statusFilter').value || $('q').value.trim();
  $('list').innerHTML = items.length ? items.map(card).join('') :
    `<div class="card empty"><img src="shared/img/caravan-truck.svg" alt="">${filtered
      ? 'Под фильтр ничего не подходит. Сбросьте поиск или статус.'
      : 'Регистраций пока нет. Они появятся здесь, как только клиенты отсканируют QR на кассе.'}</div>`;
  $('list').querySelectorAll('[data-photo]').forEach(el => thumbs.observe(el));
  const from = total ? (page - 1) * PER_PAGE + 1 : 0;
  $('range').textContent = `${from}–${Math.min(page * PER_PAGE, total)} из ${total}`;
  $('prev').disabled = page <= 1;
  $('next').disabled = page * PER_PAGE >= total;
  show($('pager'), total > PER_PAGE);
}

function card(a) {
  const date = new Date(a.created_at).toLocaleString('ru-RU');
  const issued = a.issued_at ? new Date(a.issued_at).toLocaleString('ru-RU') : null;
  const src = a.fiscal_source === 'qr' ? 'из QR' : a.fiscal_source === 'cashier' ? 'введены кассиром' : '';
  return `
  <article class="card app" data-id="${a.id}">
    <div><button class="photo-btn" type="button" data-photo="${a.id}" aria-label="Открыть фото чека"><img alt=""></button></div>
    <div>
      <h3>${plate(a.code)} <span class="tag ${a.status}">${STATUS[a.status]}</span>
        ${a.entry ? `<span class="tag pink">${esc(a.entry)}</span>` : ''}
        ${a.returned ? '<span class="tag disqualified">Возврат</span>' : ''}</h3>
      <div class="meta"><span>${date}</span><span>${esc(a.phone)}</span>${a.station ? `<span>QR АЗС №${esc(a.station)}</span>` : ''}${a.receipt_sum ? `<span>${esc(a.receipt_sum)} сом</span>` : ''}</div>
      ${issued ? `<div class="meta">Выдано ${issued}${a.issued_station ? ` на АЗС №${esc(a.issued_station)}` : ''}</div>` : ''}
      ${a.reason ? `<div class="meta">Причина: ${esc(a.reason)}</div>` : ''}
      <div class="fiscal-read">${a.fn ? `Чек: ФН ${esc(a.fn)}, ФД ${esc(a.fd)}, ФМ ${esc(a.fm)}${src ? ` (${src})` : ''}` : 'Реквизиты чека ещё не введены'}</div>

      <div class="checks">
        <label><input type="checkbox" data-c="check_purchase" ${a.check_purchase ? 'checked' : ''}> Покупка сверена с продажами</label>
        <label><input type="checkbox" data-c="returned" ${a.returned ? 'checked' : ''}> Был возврат</label>
      </div>

      <div class="actions">
        ${a.status === 'issued' ? '<button class="btn small secondary" data-act="disqualify">Снять с розыгрыша</button>' : ''}
        ${a.status === 'disqualified' ? '<button class="btn small secondary" data-act="restore">Вернуть в розыгрыш</button>' : ''}
      </div>
      <div class="error hidden" data-err></div>
    </div>
  </article>`;
}

// Фото закрыто входом, поэтому грузим его через fetch с заголовком авторизации и показываем blob-адрес.
async function photoUrl(id, size) {
  const key = `${id}:${size}`;
  if (!photoUrls.has(key)) {
    const res = await adminApi(`/api/admin/registrations/${id}/photo?size=${size}`, { raw: true });
    photoUrls.set(key, URL.createObjectURL(await res.blob()));
  }
  return photoUrls.get(key);
}

// Превью грузим только для карточек, до которых долистали.
const thumbs = new IntersectionObserver(entries => entries.forEach(async en => {
  if (!en.isIntersecting) return;
  thumbs.unobserve(en.target);
  try { en.target.querySelector('img').src = await photoUrl(en.target.dataset.photo, 'thumb'); } catch {}
}), { rootMargin: '300px' });

// После изменения перерисовываем одну карточку из ответа сервера, а не весь список.
async function act(el, fn) {
  const cardEl = el.closest('[data-id]');
  const errEl = cardEl.querySelector('[data-err]');
  show(errEl, false);
  try {
    const updated = await fn(cardEl.dataset.id);
    const i = items.findIndex(a => a.id === updated.id);
    if (i >= 0) items[i] = updated;
    cardEl.outerHTML = card(updated);
    const fresh = $('list').querySelector(`[data-id="${updated.id}"] [data-photo]`);
    if (fresh) thumbs.observe(fresh);
    loadStats();
  } catch (err) {
    errEl.textContent = err.message;
    show(errEl);
    if (el.type === 'checkbox') el.checked = !el.checked;  // вернуть галочку, раз сервер не принял
  }
}

$('list').addEventListener('change', e => {
  const el = e.target;
  if (el.dataset.c) {
    act(el, id => adminApi(`/api/admin/registrations/${id}`, { method: 'PATCH', json: { [el.dataset.c]: el.checked } }));
  }
});

$('list').addEventListener('click', async e => {
  const photo = e.target.closest('[data-photo]');
  if (photo) {
    try {
      $('lightbox').querySelector('img').src = await photoUrl(photo.dataset.photo, 'full');
      show($('lightbox'));
    } catch (err) { alert(err.message); }
    return;
  }
  const el = e.target;
  const action = el.dataset.act;
  if (action === 'disqualify') {
    const reason = prompt('Причина снятия с розыгрыша', 'Покупка не подтверждена выгрузкой продаж');
    if (reason === null) return;
    act(el, id => adminApi(`/api/admin/registrations/${id}/disqualify`, { method: 'POST', json: { reason } }));
  } else if (action === 'restore') {
    act(el, id => adminApi(`/api/admin/registrations/${id}/restore`, { method: 'POST' }));
  }
});

$('lightbox').addEventListener('click', () => show($('lightbox'), false));

// Поиск и фильтр уходят на сервер; ждём паузу в наборе, чтобы не слать запрос на каждую букву.
let searchTimer = null;
$('q').addEventListener('input', () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => { page = 1; load(); }, 300);
});
$('statusFilter').addEventListener('change', () => { page = 1; load(); });
$('prev').addEventListener('click', () => { page -= 1; load(); scrollTo({ top: 0 }); });
$('next').addEventListener('click', () => { page += 1; load(); scrollTo({ top: 0 }); });

// ---------- сверка и выгрузка ----------

$('importFile').addEventListener('change', async e => {
  const file = e.target.files[0];
  if (!file) return;
  const fd = new FormData();
  fd.append('file', file);
  $('importResult').textContent = 'Загружаем…';
  try {
    const r = await adminApi(`/api/admin/import/${$('importKind').value}`, { method: 'POST', form: fd });
    $('importResult').textContent = `Строк в файле: ${r.rows}, совпало с регистрациями: ${r.matched}.`;
    load();
  } catch (err) {
    $('importResult').textContent = err.message;
  }
  e.target.value = '';
});

$('export').addEventListener('click', async () => {
  try {
    const res = await adminApi('/api/admin/export.csv', { raw: true });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(await res.blob());
    a.download = 'registry.csv';
    a.click();
  } catch (err) {
    alert(err.message);
  }
});

if (token) enter();
