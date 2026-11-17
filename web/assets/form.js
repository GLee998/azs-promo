// Мобильная форма регистрации чека: клиент заполняет её на кассе до выдачи машинки.

const $ = id => document.getElementById(id);
const state = { photo: null, fiscal: null };
const STATION = new URLSearchParams(location.search).get('azs') || '';
const TICKET_KEY = 'karavan_ticket';
let pollTimer = null;

function show(el, on = true) { el.classList.toggle('hidden', !on); }

function setError(id, text) {
  $(id).textContent = text || '';
  show($(id), !!text);
}

// Кыргызский номер: +996 XXX XXX XXX. Принимаем и местную запись 0XXX XXX XXX.
function normalizePhone(raw) {
  let d = raw.replace(/\D/g, '');
  if (d.startsWith('9960')) d = '996' + d.slice(4);
  if (d.length === 10 && d[0] === '0') d = d.slice(1);
  if (d.length === 9) d = '996' + d;
  return /^996\d{9}$/.test(d) ? '+' + d : null;
}

// Показываем номер группами по мере ввода: +996 700 123 456
function formatPhone(raw) {
  let d = raw.replace(/\D/g, '');
  if (d.startsWith('996')) d = d.slice(3);
  // Коды операторов не начинаются с 0: это местная запись 0XXX, ноль убираем.
  d = d.replace(/^0+/, '').slice(0, 9);
  const parts = [d.slice(0, 3), d.slice(3, 6), d.slice(6, 9)].filter(Boolean);
  return '+996 ' + parts.join(' ');
}

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

if (STATION) {
  $('stationChip').textContent = 'АЗС №' + STATION;
  show($('stationChip'));
}

$('photo').addEventListener('change', async e => {
  const file = e.target.files[0];
  if (!file) return;
  setError('photoErr');
  const status = $('qrStatus');
  status.className = 'qr-status';
  status.textContent = 'Обрабатываем фото…';
  show(status);
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
    $('uploadText').textContent = 'Заменить фото';

    state.fiscal = decodeQr(img);
    if (state.fiscal) {
      status.className = 'qr-status ok';
      status.textContent = `QR-код чека распознан: ФД № ${state.fiscal.fd}, сумма ${state.fiscal.sum} сом`;
    } else {
      status.className = 'qr-status warn';
      status.textContent = 'QR-код не распознан. Можно отправить так: кассир сверит чек вручную.';
    }
    race('update');
  } catch {
    state.photo = null;
    status.className = 'qr-status warn';
    status.textContent = 'Не удалось открыть файл. Попробуйте другое фото.';
  }
});

$('phone').addEventListener('focus', () => {
  if (!$('phone').value) $('phone').value = '+996 ';
});

$('phone').addEventListener('input', e => {
  // Не мешаем стирать: группируем цифры только при наборе.
  if (!e.inputType || !e.inputType.startsWith('delete')) $('phone').value = formatPhone($('phone').value);
  setError('phoneErr');
});

$('form').addEventListener('submit', async e => {
  e.preventDefault();
  const phone = normalizePhone($('phone').value);
  const consent = $('consent').checked;
  setError('phoneErr', phone ? '' : 'Введите номер в формате +996 XXX XXX XXX');
  setError('photoErr', state.photo ? '' : 'Загрузите фото чека');
  setError('consentErr', consent ? '' : 'Нужно принять правила акции и согласие на обработку данных');
  setError('submitErr');
  if (!phone || !state.photo || !consent) return;

  $('submit').disabled = true;
  $('submit').textContent = 'Проверяем номер и чек…';
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
    setError('submitErr', err.message);
  } finally {
    $('submit').disabled = false;
    $('submit').textContent = 'Зарегистрировать чек';
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

function openTicket(r) {
  show($('formCard'), false);
  show($('ticket'));
  document.querySelectorAll('[data-code]').forEach(el => { el.textContent = r.code; });
  renderStatus(r, false);
  $('roadWrap').scrollIntoView({ behavior: 'smooth' });
  race('arrive');
  clearInterval(pollTimer);
  pollTimer = setInterval(poll, 4000);
}

function renderStatus(r, celebrate) {
  show($('stWaiting'), r.status === 'waiting');
  show($('stIssued'), r.status === 'issued' || r.status === 'disqualified');
  show($('stCancelled'), r.status === 'cancelled');
  if (r.entry) $('entryNumber').textContent = 'Номер участника ' + r.entry;
  show($('entryNumber'), !!r.entry);
  if (r.status === 'cancelled') $('cancelReason').textContent = r.reason || '';
  if (r.status !== 'waiting') {
    clearInterval(pollTimer);
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

$('again').addEventListener('click', () => {
  forgetTicket();
  $('form').reset();
  Object.assign(state, { photo: null, fiscal: null });
  show($('preview'), false);
  show($('qrStatus'), false);
  show($('camIcon'));
  show($('uploadHint'));
  $('uploadBox').classList.remove('has-photo');
  $('uploadText').textContent = 'Сфотографировать чек';
  show($('ticket'), false);
  show($('formCard'));
  race('reset');
});

// Если клиент случайно обновил страницу на кассе, показываем его заявку снова.
(async () => {
  const token = loadTicket();
  if (!token) return;
  try {
    const r = await api('/api/registrations/status/' + encodeURIComponent(token));
    if (r.status === 'waiting') openTicket(r);
    else forgetTicket();
  } catch { forgetTicket(); }
})();

// Машинка на дороге-прогрессе (assets/fx.js). Без анимаций форма работает так же.
function race(action) {
  if (typeof Race !== 'undefined') Race[action]();
}
