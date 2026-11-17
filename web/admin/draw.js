// Рандомайзер розыгрыша. Победителей выбирает сервер, страница только показывает результат.

const $ = id => document.getElementById(id);
const TOKEN_KEY = 'karavan_admin_token';
const reduceMotion = matchMedia('(prefers-reduced-motion: reduce)').matches;
let token = null;
let state = null;

try { token = sessionStorage.getItem(TOKEN_KEY); } catch {}

function show(el, on = true) { el.classList.toggle('hidden', !on); }

const esc = s => String(s ?? '').replace(/[&<>"']/g, c =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

function toast(text) {
  $('toast').textContent = text;
  show($('toast'));
  clearTimeout(toast.t);
  toast.t = setTimeout(() => show($('toast'), false), 3500);
}

function adminApi(path, opts = {}) {
  return api(path, { ...opts, token }).catch(err => {
    if (err.status === 401) logout();
    throw err;
  });
}

// ---------- вход (тот же пароль, что у реестра) ----------

$('loginForm').addEventListener('submit', async e => {
  e.preventDefault();
  show($('loginErr'), false);
  $('loginBtn').disabled = true;
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
  }
});

function logout() {
  token = null;
  try { sessionStorage.removeItem(TOKEN_KEY); } catch {}
  show($('drawView'), false);
  show($('loginView'));
}

function enter() {
  show($('loginView'), false);
  show($('drawView'));
  load();
}

// ---------- состояние ----------

async function load() {
  state = await adminApi('/api/admin/draw?only_checked=' + $('onlyChecked').checked);
  $('prizes').textContent = state.prizes;
  $('eligible').textContent = state.eligible;
  show($('reset'), state.reset_allowed);
  if (state.draw) renderDraw(state.draw, false);
  else {
    show($('setup'));
    $('slots').innerHTML = '';
    show($('results'), false);
    show($('audit'), false);
  }
}

$('onlyChecked').addEventListener('change', load);

$('run').addEventListener('click', async () => {
  show($('runErr'), false);
  const n = state.prizes;
  if (!confirm(`Провести розыгрыш ${n} поездок среди ${state.eligible} участников? Результат сохранится, повторно провести розыгрыш будет нельзя.`)) return;
  $('run').disabled = true;
  try {
    const d = await adminApi('/api/admin/draw', { method: 'POST', json: { only_checked: $('onlyChecked').checked } });
    const entries = await fetchEntries();
    renderDraw(d, true, entries);
  } catch (err) {
    $('runErr').textContent = err.message;
    show($('runErr'));
  } finally {
    $('run').disabled = false;
  }
});

async function fetchEntries() {
  const res = await adminApi('/api/admin/draw/entries.txt', { raw: true });
  return (await res.text()).split('\n').filter(Boolean);
}

// ---------- отрисовка ----------

const active = d => {
  // Для каждого места — последний выбранный победитель (переигранные остаются в истории).
  const byPlace = new Map();
  d.winners.forEach(w => { if (!w.replaced_reason) byPlace.set(w.place, w); });
  return [...byPlace.values()].sort((a, b) => a.place - b.place);
};

function renderDraw(d, animate, entries = []) {
  show($('setup'), false);
  const winners = active(d);
  $('slots').innerHTML = winners.map(w => `
    <li class="slot"><span class="slot-place">${w.place}</span>
      <span class="plate md"><span class="num" data-slot="${w.place}">${animate ? '—' : esc(w.entry)}</span></span></li>`).join('');

  if (animate && !reduceMotion && entries.length) {
    // Каждое место «крутится» по номерам участников и останавливается на победителе.
    winners.forEach((w, i) => {
      const el = document.querySelector(`[data-slot="${w.place}"]`);
      const stopAt = performance.now() + 1200 + i * 450;
      const tick = t => {
        if (t < stopAt) {
          el.textContent = entries[Math.floor(Math.random() * entries.length)];
          requestAnimationFrame(tick);
        } else {
          el.textContent = w.entry;
          el.closest('.slot').classList.add('landed');
          if (i === winners.length - 1) celebrate();
        }
      };
      requestAnimationFrame(tick);
    });
  } else {
    document.querySelectorAll('.slot').forEach(s => s.classList.add('landed'));
  }

  $('winners').innerHTML = `
    <table class="winners">
      <thead><tr><th>Место</th><th>Номер участника</th><th>Телефон</th><th>Заявка</th><th>Выдано на АЗС</th><th></th></tr></thead>
      <tbody>${d.winners.slice().sort((a, b) => a.place - b.place || a.id - b.id).map(w => `
        <tr class="${w.replaced_reason ? 'replaced' : ''}">
          <td>${w.place}</td><td><b>${esc(w.entry)}</b></td><td>${esc(w.phone)}</td><td>${esc(w.code)}</td>
          <td>${w.station ? '№' + esc(w.station) : ''}</td>
          <td>${w.replaced_reason ? `<span class="hint">переигран: ${esc(w.replaced_reason)}</span>`
            : `<button class="btn small secondary" data-redraw="${w.id}" type="button">Переиграть</button>`}</td>
        </tr>`).join('')}</tbody>
    </table>`;
  show($('results'));

  $('auditData').innerHTML = `
    <div><span class="hint">Проведён</span><b>${new Date(d.created_at).toLocaleString('ru-RU')}</b></div>
    <div><span class="hint">Участников</span><b>${d.entries_count}</b></div>
    <div><span class="hint">Условие</span><b>${d.only_checked ? 'покупка сверена' : 'все с выданной машинкой'}</b></div>
    <div class="hash"><span class="hint">SHA-256 списка участников</span><code>${esc(d.entries_hash)}</code></div>`;
  show($('audit'));
}

function celebrate() {
  if (!window.confetti || reduceMotion) return;
  const colors = ['#008ED7', '#AE4A84', '#FFFFFF', '#F07DB8'];
  confetti({ particleCount: 160, spread: 90, origin: { y: .35 }, colors });
}

$('winners').addEventListener('click', async e => {
  const id = e.target.dataset.redraw;
  if (!id) return;
  const reason = prompt('Почему переигрываете это место? Причина сохранится в истории розыгрыша.', 'Победитель не вышел на связь');
  if (!reason) return;
  try {
    const d = await adminApi(`/api/admin/draw/winners/${id}/redraw`, { method: 'POST', json: { reason } });
    renderDraw(d, false);
    toast('Место переиграно');
  } catch (err) {
    toast(err.message);
  }
});

$('downloadEntries').addEventListener('click', async () => {
  const res = await adminApi('/api/admin/draw/entries.txt', { raw: true });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(await res.blob());
  a.download = 'draw-entries.txt';
  a.click();
});

$('reset').addEventListener('click', async () => {
  if (!confirm('Сбросить розыгрыш? Это доступно только на тестовом стенде.')) return;
  try {
    await adminApi('/api/admin/draw', { method: 'DELETE' });
    await load();
  } catch (err) {
    toast(err.message);
  }
});

if (token) enter();
