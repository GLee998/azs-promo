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
    await api('/api/admin/login', { method: 'POST', token: $('password').value });
    token = $('password').value;
    try { sessionStorage.setItem(TOKEN_KEY, token); } catch {}
    enter();
  } catch (err) {
    $('loginErr').textContent = err.status === 401 ? 'Неверный пароль' : err.message;
    show($('loginErr'));
  } finally {
    $('loginBtn').disabled = false;
    $('loginBtn').textContent = 'Войти';
  }
});

function logout() {
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

async function load() {
  try {
    const r = await adminApi('/api/admin/registrations');
    items = r.items;
    renderStats(r.stats);
    render();
  } catch (err) {
    $('list').innerHTML = `<div class="card error">${esc(err.message)}</div>`;
  }
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

function matches(a) {
  const f = $('statusFilter').value;
  if (f === 'unchecked' && !(a.status === 'issued' && !a.check_purchase)) return false;
  if (f && f !== 'unchecked' && a.status !== f) return false;
  const q = $('q').value.trim().toLowerCase().replace(/\s/g, '');
  if (!q) return true;
  return [a.code, a.phone, a.fn, a.fd, a.fm, a.entry].some(v => v && v.toLowerCase().replace(/\s/g, '').includes(q));
}

function render() {
  const list = items.filter(matches);
  if (!list.length) {
    $('list').innerHTML = `<div class="card empty"><img src="shared/img/caravan-truck.svg" alt="">${items.length
      ? 'Под фильтр ничего не подходит. Сбросьте поиск или статус.'
      : 'Регистраций пока нет. Они появятся здесь, как только клиенты отсканируют QR на кассе.'}</div>`;
    return;
  }
  $('list').innerHTML = list.map(card).join('');
  list.forEach(a => loadPhoto(a.id));
}

function card(a) {
  const date = new Date(a.created_at).toLocaleString('ru-RU');
  const issued = a.issued_at ? new Date(a.issued_at).toLocaleString('ru-RU') : null;
  const src = a.fiscal_source === 'qr' ? 'из QR' : a.fiscal_source === 'cashier' ? 'введены кассиром' : '';
  return `
  <article class="card app" data-id="${a.id}">
    <div><img data-photo="${a.id}" alt="Фото чека"></div>
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

// Фото закрыто паролем, поэтому грузим его через fetch с заголовком авторизации.
async function loadPhoto(id) {
  const img = document.querySelector(`img[data-photo="${id}"]`);
  if (!img) return;
  if (!photoUrls.has(id)) {
    try {
      const res = await adminApi(`/api/admin/registrations/${id}/photo`, { raw: true });
      photoUrls.set(id, URL.createObjectURL(await res.blob()));
    } catch { return; }
  }
  img.src = photoUrls.get(id);
}

async function act(el, fn) {
  const cardEl = el.closest('[data-id]');
  const errEl = cardEl.querySelector('[data-err]');
  show(errEl, false);
  try {
    await fn(cardEl.dataset.id);
    await load();
  } catch (err) {
    errEl.textContent = err.message;
    show(errEl);
  }
}

$('list').addEventListener('change', e => {
  const el = e.target;
  if (el.dataset.c) {
    act(el, id => adminApi(`/api/admin/registrations/${id}`, { method: 'PATCH', json: { [el.dataset.c]: el.checked } }));
  }
});

$('list').addEventListener('click', e => {
  const el = e.target;
  if (el.dataset.photo && el.src) {
    $('lightbox').querySelector('img').src = el.src;
    show($('lightbox'));
    return;
  }
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
$('q').addEventListener('input', render);
$('statusFilter').addEventListener('change', render);

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
