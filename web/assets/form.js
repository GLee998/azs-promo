// Мобильная форма регистрации чека.

const $ = id => document.getElementById(id);
const state = { photo: null, fiscal: null, smsRequired: true, smsSentTo: null };

function show(el, on = true) { el.classList.toggle('hidden', !on); }

function setError(id, text) {
  $(id).textContent = text || '';
  show($(id), !!text);
}

function normalizePhone(raw) {
  let d = raw.replace(/\D/g, '');
  if (d.length === 11 && d[0] === '8') d = '7' + d.slice(1);
  if (d.length === 10) d = '7' + d;
  return /^7\d{10}$/.test(d) ? '+' + d : null;
}

// Строка из QR-кода кассового чека: t=20261117T1030&s=850.00&fn=...&i=...&fp=...&n=1
function parseReceiptQr(text) {
  if (!text || !/fn=/.test(text)) return null;
  const p = new URLSearchParams(text.trim());
  const f = { fn: p.get('fn'), fd: p.get('i'), fp: p.get('fp'), sum: p.get('s'), date: p.get('t') };
  return f.fn && f.fd && f.fp ? f : null;
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

api('/api/config').then(c => { state.smsRequired = c.sms_required; }).catch(() => {});

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
    $('uploadText').textContent = 'Заменить фото';

    state.fiscal = decodeQr(img);
    if (state.fiscal) {
      status.className = 'qr-status ok';
      status.textContent = `QR-код чека распознан: ФД № ${state.fiscal.fd}, сумма ${state.fiscal.sum} ₽`;
    } else {
      status.className = 'qr-status warn';
      status.textContent = 'QR-код не распознан. Ничего страшного, мы проверим чек вручную. Убедитесь, что чек виден целиком.';
    }
  } catch {
    state.photo = null;
    status.className = 'qr-status warn';
    status.textContent = 'Не удалось открыть файл. Попробуйте другое фото.';
  }
});

$('phone').addEventListener('input', () => {
  setError('phoneErr');
  // Номер поменяли: код нужно запросить заново.
  if (state.smsSentTo && normalizePhone($('phone').value) !== state.smsSentTo) {
    state.smsSentTo = null;
    show($('smsBlock'), false);
    $('submit').textContent = 'Отправить заявку';
  }
});

$('resend').addEventListener('click', () => sendSms(state.smsSentTo));

$('rulesLink').addEventListener('click', e => {
  e.preventDefault();
  show($('rules'));
  $('rules').scrollIntoView({ behavior: 'smooth' });
});

async function sendSms(phone) {
  const r = await api('/api/sms/send', { method: 'POST', json: { phone } });
  state.smsSentTo = phone;
  show($('smsBlock'));
  $('smsHint').textContent = r.demo_code
    ? `Демо: SMS не отправляется, ваш код ${r.demo_code}`
    : `Отправили код на ${phone}`;
  $('submit').textContent = 'Подтвердить и отправить';
  $('smsCode').value = '';
  $('smsCode').focus();
}

function busy(on, text) {
  $('submit').disabled = on;
  if (text) $('submit').textContent = text;
}

$('form').addEventListener('submit', async e => {
  e.preventDefault();
  const phone = normalizePhone($('phone').value);
  const consent = $('consent').checked;
  setError('phoneErr', phone ? '' : 'Введите номер в формате +7XXXXXXXXXX');
  setError('photoErr', state.photo ? '' : 'Загрузите фото чека');
  setError('consentErr', consent ? '' : 'Нужно согласие на обработку данных');
  setError('smsErr');
  setError('submitErr');
  if (!phone || !state.photo || !consent) return;

  const label = $('submit').textContent;
  try {
    if (state.smsRequired && state.smsSentTo !== phone) {
      busy(true, 'Отправляем код…');
      await sendSms(phone);
      return;
    }
    if (state.smsRequired && !/^\d{4}$/.test($('smsCode').value.trim())) {
      setError('smsErr', 'Введите 4 цифры из SMS');
      return;
    }

    busy(true, 'Отправляем заявку…');
    const fd = new FormData();
    fd.append('phone', phone);
    fd.append('consent', 'true');
    fd.append('sms_code', $('smsCode').value.trim());
    fd.append('photo', state.photo, 'receipt.jpg');
    if (state.fiscal) {
      fd.append('fn', state.fiscal.fn);
      fd.append('fd', state.fiscal.fd);
      fd.append('fp', state.fiscal.fp);
      fd.append('receipt_sum', state.fiscal.sum || '');
      fd.append('receipt_date', state.fiscal.date || '');
    }
    const r = await api('/api/applications', { method: 'POST', form: fd });

    $('appId').textContent = r.code;
    show($('formCard'), false);
    show($('rules'), false);
    show($('successCard'));
    window.scrollTo({ top: 0 });
  } catch (err) {
    const smsProblem = /код/i.test(err.message);
    setError(smsProblem ? 'smsErr' : 'submitErr', err.message);
  } finally {
    busy(false);
    if ($('submit').textContent.endsWith('…')) $('submit').textContent = label;
  }
});

$('again').addEventListener('click', () => {
  $('form').reset();
  Object.assign(state, { photo: null, fiscal: null, smsSentTo: null });
  show($('preview'), false);
  show($('qrStatus'), false);
  show($('smsBlock'), false);
  $('uploadText').textContent = '📷 Нажмите, чтобы сфотографировать или выбрать фото';
  $('submit').textContent = 'Отправить заявку';
  show($('successCard'), false);
  show($('formCard'));
});
