// Закрытый реестр маркетинга.

const $ = id => document.getElementById(id);
const TOKEN_KEY = 'promo_admin_token';
let token = null;
let items = [];
const photoUrls = new Map();

try { token = sessionStorage.getItem(TOKEN_KEY); } catch {}

function show(el, on = true) { el.classList.toggle('hidden', !on); }

const esc = s => String(s ?? '').replace(/[&<>"']/g, c =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const STATUS = {
  pending: ['На проверке', 'pending'],
  approved: ['Допущена', 'approved'],
  rejected: ['Отклонена', 'rejected'],
};

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
    const r = await adminApi('/api/admin/applications');
    items = r.items;
    renderStats(r.stats);
    render();
  } catch (err) {
    $('list').innerHTML = `<div class="card error">${esc(err.message)}</div>`;
  }
}

function renderStats(s) {
  const tiles = [
    [s.total, 'всего заявок'],
    [s.pending, 'на проверке'],
    [s.approved, 'допущено'],
    [s.rejected, 'отклонено'],
    [s.duplicates, 'дубликатов чеков'],
    [s.participants, 'номеров участников'],
  ];
  $('stats').innerHTML = tiles.map(([n, t], i) =>
    `<div class="stat${i === tiles.length - 1 ? ' accent' : ''}"><b>${n}</b><span>${t}</span></div>`).join('');
}

function matches(a) {
  const f = $('statusFilter').value;
  if (f === 'dup' && !a.duplicate_of) return false;
  if (f && f !== 'dup' && a.status !== f) return false;
  const q = $('q').value.trim().toLowerCase();
  if (!q) return true;
  return [a.code, a.phone, a.fn, a.fd, a.fp, ...a.numbers].some(v => v && v.toLowerCase().includes(q));
}

function render() {
  const list = items.filter(matches);
  if (!list.length) {
    $('list').innerHTML = `<div class="card empty"><img src="img/car.svg" alt="">${items.length
      ? 'Под фильтр ничего не подходит. Сбросьте поиск или статус.'
      : 'Заявок пока нет. Они появятся здесь, как только клиенты отправят чеки.'}</div>`;
    return;
  }
  $('list').innerHTML = list.map(card).join('');
  list.forEach(a => loadPhoto(a.id));
}

function card(a) {
  const [label, cls] = STATUS[a.status];
  const date = new Date(a.created_at).toLocaleString('ru-RU');
  const locked = a.status === 'approved' ? 'disabled' : '';
  const src = a.fiscal_source === 'qr' ? 'из QR' : a.fiscal_source === 'manual' ? 'вручную' : '';
  return `
  <article class="card app" data-id="${a.id}">
    <div><img data-photo="${a.id}" alt="Фото чека"></div>
    <div>
      <h3>${esc(a.code)} <span class="tag ${cls}">${label}</span>
        ${a.duplicate_of ? `<span class="tag dup">Дубликат чека из ${esc(a.duplicate_of)}</span>` : ''}
        ${a.returned ? '<span class="tag rejected">Возврат</span>' : ''}</h3>
      <div class="meta"><span>${date}</span><span>${esc(a.phone)} ${a.phone_verified ? '✓ подтверждён' : '(не подтверждён)'}</span>${a.receipt_sum ? `<span>сумма ${esc(a.receipt_sum)} сом</span>` : ''}</div>
      ${a.reject_reason ? `<div class="meta">Причина: ${esc(a.reject_reason)}</div>` : ''}

      <div class="fiscal">
        <div><label>ФН</label><input data-f="fn" value="${esc(a.fn)}" ${locked} inputmode="numeric"></div>
        <div><label>ФД</label><input data-f="fd" value="${esc(a.fd)}" ${locked} inputmode="numeric"></div>
        <div><label>ФМ</label><input data-f="fp" value="${esc(a.fp)}" ${locked} inputmode="numeric"></div>
        <div><label>Комбо в чеке</label><input data-f="combos" type="number" min="1" max="20" value="${a.combos}" ${locked}></div>
      </div>
      ${src ? `<div class="meta">Реквизиты ${src}</div>` : '<div class="meta">QR не распознан: впишите ФН, ФД, ФМ с фото чека</div>'}

      <div class="checks">
        <label><input type="checkbox" data-c="check_purchase" ${a.check_purchase ? 'checked' : ''}> Покупка сверена с продажами</label>
        <label><input type="checkbox" data-c="check_issued" ${a.check_issued ? 'checked' : ''}> Игрушка выдана по чеку</label>
        <label><input type="checkbox" data-c="returned" ${a.returned ? 'checked' : ''}> Был возврат</label>
      </div>

      <div class="actions">
        ${a.status !== 'approved' ? '<button class="btn small" data-act="approve">Допустить</button>' : ''}
        ${a.status !== 'rejected' ? '<button class="btn small secondary" data-act="reject">Отклонить</button>' : ''}
        ${a.status !== 'pending' ? '<button class="btn small secondary" data-act="reset">Вернуть на проверку</button>' : ''}
        ${a.numbers.length ? `<span class="numbers">${a.numbers.map(plate).join('')}</span>` : ''}
      </div>
      <div class="error hidden" data-err></div>
    </div>
  </article>`;
}

const plate = n => `<span class="plate sm"><span class="num">${esc(n)}</span><span class="reg"><i></i><span>KG</span></span></span>`;

// Фото закрыто паролем, поэтому грузим его через fetch с заголовком авторизации.
async function loadPhoto(id) {
  const img = document.querySelector(`img[data-photo="${id}"]`);
  if (!img) return;
  if (!photoUrls.has(id)) {
    try {
      const res = await adminApi(`/api/admin/applications/${id}/photo`, { raw: true });
      photoUrls.set(id, URL.createObjectURL(await res.blob()));
    } catch { return; }
  }
  img.src = photoUrls.get(id);
}

function replaceItem(updated) {
  const i = items.findIndex(a => a.id === updated.id);
  if (i >= 0) items[i] = updated;
}

async function act(el, fn) {
  const cardEl = el.closest('[data-id]');
  const errEl = cardEl.querySelector('[data-err]');
  show(errEl, false);
  try {
    replaceItem(await fn(cardEl.dataset.id));
    // Дубликаты и статистика зависят от других заявок: перечитываем список целиком.
    await load();
  } catch (err) {
    errEl.textContent = err.message;
    show(errEl);
  }
}

$('list').addEventListener('change', e => {
  const el = e.target;
  if (el.dataset.f) {
    const value = el.dataset.f === 'combos' ? Number(el.value) : el.value;
    act(el, id => adminApi(`/api/admin/applications/${id}`, { method: 'PATCH', json: { [el.dataset.f]: value } }));
  } else if (el.dataset.c) {
    act(el, id => adminApi(`/api/admin/applications/${id}`, { method: 'PATCH', json: { [el.dataset.c]: el.checked } }));
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
  if (!action) return;
  if (action === 'reject') {
    const reason = prompt('Причина отклонения', 'Не прошла проверку');
    if (reason === null) return;
    act(el, id => adminApi(`/api/admin/applications/${id}/reject`, { method: 'POST', json: { reason } }));
  } else {
    act(el, id => adminApi(`/api/admin/applications/${id}/${action}`, { method: 'POST' }));
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
    $('importResult').textContent = `Строк в файле: ${r.rows}, совпало с заявками: ${r.matched}.`;
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
