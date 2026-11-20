// Генератор тестовых чеков (только тестовый стенд).
// Отдельным файлом, а не в HTML: политика безопасности (CSP) запрещает встроенные скрипты.

const $ = id => document.getElementById(id);
const KEY = 'promo_test_receipts';
let receipts = [];
try { receipts = JSON.parse(localStorage.getItem(KEY)) || []; } catch {}
const persist = () => { try { localStorage.setItem(KEY, JSON.stringify(receipts)); } catch {} };
const digits = n => Array.from({ length: n }, (_, i) => (i === 0 ? 1 + Math.floor(Math.random() * 9) : Math.floor(Math.random() * 10))).join('');
const PRICE = 450;

function qrString(r) {
  return `https://tax.salyk.kg/client/api/v1/ticket?date=${r.t}&type=3&operation_type=1&fn_number=${r.fn}&fd_number=${r.fd}&fm=${r.fp}&sum=${r.sum}`;
}

function renderReceipt(r) {
  const qr = qrcode(0, 'M');
  qr.addData(qrString(r));
  qr.make();
  $('receipt').innerHTML = `
    <div class="stamp">ТЕСТОВЫЙ ОБРАЗЕЦ</div>
    <div style="text-align:center">АЗС · ДЕМО<br>Кассовый чек · Приход</div><hr>
    <div class="line"><span>Комбо «Караван Coca-Cola»</span><span>${r.sum} сом</span></div>
    <div class="line"><b>ИТОГО</b><b>${r.sum} сом</b></div><hr>
    <div>ФН ${r.fn}</div><div>ФД ${r.fd}</div><div>ФМ ${r.fp}</div>
    <div id="rqr">${qr.createSvgTag({ cellSize: 5, margin: 4, scalable: false })}</div>`;
  $('receipt').dataset.qr = qr.createDataURL(8, 16);
  $('receipt').classList.remove('hidden');
  $('savePng').classList.remove('hidden');
}

function updateCount() { $('count').textContent = receipts.length; }

$('gen').onclick = () => {
  const qty = 1;
  const d = new Date();
  const p = v => String(v).padStart(2, '0');
  const r = {
    fn: '0000' + digits(12), fd: digits(5), fp: digits(10), qty,
    sum: (PRICE * qty).toFixed(2),
    t: `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}T${p(d.getHours())}${p(d.getMinutes())}`,
  };
  receipts.push(r);
  persist();
  updateCount();
  renderReceipt(r);
};

// Для загрузки в форму достаточно картинки QR-кода с реквизитами.
$('savePng').onclick = () => {
  const a = document.createElement('a');
  a.href = $('receipt').dataset.qr;
  a.download = 'test-receipt-qr.gif';
  a.click();
};

function download(name, rows) {
  if (!receipts.length) return alert('Сначала создайте чек');
  const blob = new Blob(['﻿' + rows.join('\n')], { type: 'text/csv' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = name;
  a.click();
}

$('dlSales').onclick = () => download('sales.csv', ['fn;fd;fm', ...receipts.map(r => `${r.fn};${r.fd};${r.fp}`)]);
$('dlReturns').onclick = () => {
  const r = receipts[receipts.length - 1];
  download('returns.csv', ['fn;fd;fm', ...(r ? [`${r.fn};${r.fd};${r.fp}`] : [])]);
};
$('reset').onclick = () => { receipts = []; persist(); updateCount(); $('receipt').classList.add('hidden'); $('savePng').classList.add('hidden'); };

updateCount();
if (receipts.length) renderReceipt(receipts[receipts.length - 1]);
