// Мобильная форма регистрации чека: клиент заполняет её на кассе, кассир подтверждает чек.
// Каждый подтверждённый чек — купон в розыгрыше, сувенир выдаётся один раз на номер.

const $ = id => document.getElementById(id);
const state = { photo: null, fiscal: null, qr: null, ticket: null };
const t = (key, vars) => I18n.t(key, vars);
const STATION = new URLSearchParams(location.search).get('azs') || '';
const TICKET_KEY = 'karavan_ticket';
let pollTimer = null;

function show(el, on = true) { el.classList.toggle('hidden', !on); }

// Поле, к которому относится сообщение об ошибке: для экранного диктора и подсветки рамки.
const ERR_FIELD = { phoneErr: 'phone', photoErr: 'photo', consentErr: 'consent' };

function setError(id, text) {
  $(id).textContent = text || '';
  show($(id), !!text);
  const field = ERR_FIELD[id] && $(ERR_FIELD[id]);
  if (field) field.setAttribute('aria-invalid', text ? 'true' : 'false');
  if (id === 'photoErr') $('uploadBox').classList.toggle('invalid', !!text);
}

// Число поездок приходит из настроек розыгрыша (/api/config), по умолчанию 8.
let prizes = 8;
function ruPlural(n, one, few, many) {
  const m10 = n % 10, m100 = n % 100;
  if (m10 === 1 && m100 !== 11) return one;
  if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return few;
  return many;
}
function renderPrizes() {
  document.querySelectorAll('.prizes').forEach(el => { el.textContent = prizes; });
  document.querySelectorAll('.prizes-word').forEach(el => {
    el.textContent = prizes + ' ' + ruPlural(prizes, 'поездка', 'поездки', 'поездок');
  });
}
api('/api/config').then(c => {
  if (Number.isInteger(c.prizes) && c.prizes > 0) { prizes = c.prizes; renderPrizes(); }
  // Сувениры закончились: предупреждаем до регистрации, что участие в розыгрыше продолжается.
  show($('soldOut'), c.souvenirs_left === 0);
}).catch(() => {});

// Номер телефона: normalizePhone и formatPhone из phone.js.
const { normalizePhone, formatPhone } = window.Phone;

// QR-код фискального чека. Кыргызская ККМ онлайн ведёт на сервис ГНС:
//   https://tax.salyk.kg/client/api/v1/ticket?date=20261117T1030&fn_number=...&fd_number=...&fm=...&sum=900.00
// Поддерживаем и короткую запись t=...&s=...&fn=...&i=...&fp=...
function parseReceiptQr(text) {
  if (!text || !/fn(_number)?=/.test(text)) return null;
  const query = text.includes('?') ? text.slice(text.indexOf('?') + 1) : text;
  const p = new URLSearchParams(query.trim());
  const get = (...keys) => keys.map(k => p.get(k)).find(Boolean) || null;
  const f = {
    fn: get('fn_number', 'fn'),
    fd: get('fd_number', 'i'),
    fm: get('fm', 'fp'),
    sum: get('sum', 's'),
    date: get('date', 't'),
  };
  return f.fn && f.fd && f.fm ? f : null;
}

function loadImage(file) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = reject;
    img.src = URL.createObjectURL(file);
  });
}

function drawScaled(img, maxSide) {
  const k = Math.min(1, maxSide / Math.max(img.width, img.height));
  const c = document.createElement('canvas');
  c.width = Math.round(img.width * k);
  c.height = Math.round(img.height * k);
  c.getContext('2d').drawImage(img, 0, 0, c.width, c.height);
  return c;
}

// Пробуем прочитать QR-код чека на нескольких масштабах.
function decodeQr(img) {
  if (typeof jsQR !== 'function') return null;
  for (const side of [1600, 1000, 700]) {
    const c = drawScaled(img, side);
    const data = c.getContext('2d').getImageData(0, 0, c.width, c.height);
    const res = jsQR(data.data, c.width, c.height, { inversionAttempts: 'attemptBoth' });
    if (res) return parseReceiptQr(res.data);
  }
  return null;
}

// Подсказка под фото: храним ключ, чтобы перевести её при смене языка.
function setQr(key) {
  state.qr = key;
  const status = $('qrStatus');
  status.className = 'qr-status' + ({ qrOk: ' ok', qrNo: ' warn', fileBad: ' warn' }[key] || '');
  status.textContent = t(key, state.fiscal || {});
  show(status);
}

if (STATION) {
  $('stationChip').textContent = t('station', { n: STATION });
  show($('stationChip'));
}

$('photo').addEventListener('change', async e => {
  const file = e.target.files[0];
  if (!file) return;
  setError('photoErr');
  setQr('processing');
  try {
    const img = await loadImage(file);
    // Сжимаем перед отправкой: на мобильном интернете это заметно быстрее.
    const canvas = drawScaled(img, 1800);
    state.photo = await new Promise(r => canvas.toBlob(r, 'image/jpeg', 0.85));
    $('preview').src = URL.createObjectURL(state.photo);
    show($('preview'));
    show($('camIcon'), false);
    show($('uploadHint'), false);
    $('uploadBox').classList.add('has-photo');
    $('uploadText').textContent = t('replacePhoto');

    state.fiscal = decodeQr(img);
    setQr(state.fiscal ? 'qrOk' : 'qrNo');
    race('update');
    // Чек готов: следующий шаг — номер телефона.
    if (!normalizePhone($('phone').value)) {
      $('phoneField').scrollIntoView({ behavior: 'smooth', block: 'center' });
      $('phone').focus({ preventScroll: true });
    }
  } catch {
    state.photo = null;
    setQr('fileBad');
  }
});

$('phone').addEventListener('focus', () => {
  if (!$('phone').value) $('phone').value = '+996 ';
});

$('phone').addEventListener('input', e => {
  // Не мешаем стирать: группируем цифры только при наборе.
  if (!e.inputType || !e.inputType.startsWith('delete')) $('phone').value = formatPhone($('phone').value);
  setError('phoneErr');
  // Галочка: номер набран полностью и в верном формате.
  show($('phoneOk'), !!normalizePhone($('phone').value));
});

$('form').addEventListener('submit', async e => {
  e.preventDefault();
  const phone = normalizePhone($('phone').value);
  const consent = $('consent').checked;
  setError('phoneErr', phone ? '' : t('phoneErr'));
  setError('photoErr', state.photo ? '' : t('photoErr'));
  setError('consentErr', consent ? '' : t('consentErr'));
  setError('submitErr');
  // Ошибки выше кнопки: ведём к первой, иначе кажется, что кнопка не сработала.
  const firstBad = !state.photo ? 'photo' : !phone ? 'phone' : !consent ? 'consent' : null;
  if (firstBad) {
    const box = firstBad === 'photo' ? $('uploadBox') : $(firstBad);
    box.scrollIntoView({ behavior: 'smooth', block: 'center' });
    $(firstBad).focus({ preventScroll: true });
    return;
  }

  $('submit').disabled = true;
  $('submit').textContent = t('checking');
  try {
    const fd = new FormData();
    fd.append('phone', phone);
    fd.append('consent', 'true');
    fd.append('station', STATION);
    fd.append('photo', state.photo, 'receipt.jpg');
    if (state.fiscal) {
      fd.append('fn', state.fiscal.fn);
      fd.append('fd', state.fiscal.fd);
      fd.append('fm', state.fiscal.fm);
      fd.append('receipt_sum', state.fiscal.sum || '');
      fd.append('receipt_date', state.fiscal.date || '');
    }
    const r = await api('/api/registrations', { method: 'POST', form: fd });
    saveTicket(r.token);
    openTicket(r);
  } catch (err) {
    setError('submitErr', I18n.server(err.message));
  } finally {
    $('submit').disabled = false;
    $('submit').textContent = t('submit');
  }
});

// ---------- экран заявки: его клиент показывает кассиру ----------

function saveTicket(token) {
  try { localStorage.setItem(TICKET_KEY, token); } catch {}
}

function loadTicket() {
  try { return localStorage.getItem(TICKET_KEY); } catch { return null; }
}

function forgetTicket() {
  try { localStorage.removeItem(TICKET_KEY); } catch {}
}

function openTicket(r, scroll = true) {
  show($('formCard'), false);
  show($('ticket'));
  document.querySelectorAll('[data-code]').forEach(el => { el.textContent = r.code; });
  race('arrive');
  clearInterval(pollTimer);
  if (r.status === 'waiting') pollTimer = setInterval(poll, 4000);
  renderStatus(r, false);
  if (r.status === 'issued') race('finish', true);
  // К самой заявке, а не к полосе прогресса: иначе липкая полоса закрывает заголовок.
  if (scroll) $('ticket').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function renderStatus(r, celebrate) {
  state.ticket = r;
  show($('stWaiting'), r.status === 'waiting');
  show($('stIssued'), r.status === 'issued' || r.status === 'disqualified');
  show($('stCancelled'), r.status === 'cancelled');
  $('ticket').classList.toggle('is-waiting', r.status === 'waiting');
  $('entryNum').textContent = r.entry || '';
  show($('entryNumber'), !!r.entry);
  // Сувенир: при ожидании — положен ли он по этому чеку, после подтверждения — выдан ли.
  show($('wSouvenirYes'), r.souvenir_due !== false);
  show($('wSouvenirNo'), r.souvenir_due === false);
  show($('iTitleSouvenir'), !!r.souvenir);
  show($('iTitleCoupon'), !r.souvenir);
  if (r.status === 'cancelled') $('cancelReason').textContent = I18n.server(r.reason || '');
  if (r.status !== 'waiting') {
    clearInterval(pollTimer);
    closeCashierView();
    if (r.status === 'issued' && celebrate) race('finish');
  }
}

async function poll() {
  const token = loadTicket();
  if (!token) return;
  try {
    const r = await api('/api/registrations/status/' + encodeURIComponent(token));
    renderStatus(r, true);
  } catch {}
}

function newReceipt() {
  forgetTicket();
  $('form').reset();
  Object.assign(state, { photo: null, fiscal: null, qr: null, ticket: null });
  show($('preview'), false);
  show($('qrStatus'), false);
  show($('phoneOk'), false);
  show($('camIcon'));
  show($('uploadHint'));
  $('uploadBox').classList.remove('has-photo');
  $('uploadText').textContent = t('takePhoto');
  show($('ticket'), false);
  show($('formCard'));
  race('reset');
}

$('again').addEventListener('click', newReceipt);
// Ещё один чек тем же номером: номер оставляем в поле, чтобы не набирать заново.
$('another').addEventListener('click', () => {
  const phone = $('phone').value || (state.ticket && state.ticket.phone) || '';
  newReceipt();
  if (phone) { $('phone').value = phone; show($('phoneOk'), !!normalizePhone(phone)); }
  $('formCard').scrollIntoView({ behavior: 'smooth', block: 'start' });
});

// Клиент обновил страницу или вернулся позже: показываем его заявку снова, в любом статусе.
// После выдачи здесь остаётся номер участника для розыгрыша. Заявку забываем, только если её нет на сервере;
// при сбое сети токен сохраняем, заявка покажется при следующем открытии.
(async () => {
  const token = loadTicket();
  if (!token) return;
  try {
    const r = await api('/api/registrations/status/' + encodeURIComponent(token));
    openTicket(r, r.status === 'waiting');
  } catch (err) {
    if (err.status === 404) forgetTicket();
  }
})();

// ---------- режим «Показать кассиру»: код на весь экран, экран не гаснет ----------

let wakeLock = null;

async function keepScreenOn() {
  try { wakeLock = await navigator.wakeLock?.request('screen'); } catch { wakeLock = null; }
}

function openCashierView() {
  show($('cashierView'));
  document.body.classList.add('no-scroll');
  keepScreenOn();
  $('cvClose').focus();
}

function closeCashierView() {
  if ($('cashierView').classList.contains('hidden')) return;
  show($('cashierView'), false);
  document.body.classList.remove('no-scroll');
  wakeLock?.release().catch(() => {});
  wakeLock = null;
  $('showCashier').focus({ preventScroll: true });
}

$('showCashier').addEventListener('click', openCashierView);
$('cvClose').addEventListener('click', closeCashierView);
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeCashierView(); });
// Браузер снимает блокировку экрана, когда вкладку сворачивают: возвращаем её.
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible' && !$('cashierView').classList.contains('hidden')) keepScreenOn();
});

// Смена языка: переводим всё, что form.js подставил сам. Ошибки проверки скрываем,
// они появятся снова на нужном языке при следующей отправке.
function applyLang() {
  if (STATION) $('stationChip').textContent = t('station', { n: STATION });
  $('uploadText').textContent = t(state.photo ? 'replacePhoto' : 'takePhoto');
  if (state.qr) setQr(state.qr);
  if (!$('submit').disabled) $('submit').textContent = t('submit');
  ['phoneErr', 'photoErr', 'consentErr', 'submitErr'].forEach(id => setError(id));
  renderPrizes();
  if (state.ticket) renderStatus(state.ticket, false);
}
document.addEventListener('langchange', applyLang);
applyLang();

// Машинка на дороге-прогрессе (fx.js). Без анимаций форма работает так же.
function race(action, arg) {
  if (typeof Race !== 'undefined') Race[action](arg);
}
