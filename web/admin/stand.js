// Плакат с QR для кассы: QR ведёт на форму клиента с номером АЗС.
// Отдельным файлом, а не в HTML: политика безопасности (CSP) запрещает встроенные скрипты.

const input = document.getElementById('stationNo');
input.value = new URLSearchParams(location.search).get('azs') || '';

// У каждой кассы свой QR: номер АЗС попадает в заявку и подсвечивается у кассира.
function draw() {
  const no = input.value.trim();
  const url = new URL(window.CLIENT_URL || new URL('/', location.href).href);
  if (no) url.searchParams.set('azs', no);
  const qr = qrcode(0, 'M');
  qr.addData(url.href);
  qr.make();
  document.getElementById('qr').innerHTML = qr.createSvgTag({ cellSize: 7, margin: 0, scalable: false });
  document.getElementById('url').textContent = url.href;
  const name = document.getElementById('stationName');
  name.textContent = 'АЗС №' + no;
  name.classList.toggle('hidden', !no);
}
input.addEventListener('input', draw);
draw();
document.getElementById('printBtn').addEventListener('click', () => print());
