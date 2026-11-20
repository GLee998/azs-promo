// Номер телефона клиента: кыргызский формат +996 XXX XXX XXX.
// Отдельный файл, чтобы проверять его тестами в Node (phone.test.js).

(function (root) {
  // Полный номер для отправки на сервер или null. Принимаем и местную запись 0XXX XXX XXX.
  function normalizePhone(raw) {
    let d = raw.replace(/\D/g, '');
    if (d.startsWith('9960')) d = '996' + d.slice(4);
    if (d.length === 10 && d[0] === '0') d = d.slice(1);
    // 9 цифр без «+» — местный номер без кода страны. С «+» это неполный номер «+996 XXX XXX»,
    // приписав код, получили бы другой, но правдоподобный номер.
    if (d.length === 9 && !raw.trim().startsWith('+')) d = '996' + d;
    return /^996\d{9}$/.test(d) ? '+' + d : null;
  }

  // Показываем номер группами по мере ввода: +996 700 123 456.
  // Поле само подставляет «+996 ». Если после него вставить номер с кодом страны,
  // второй «996» убираем, иначе получился бы другой, но правдоподобный номер.
  function formatPhone(raw) {
    let d = raw.replace(/\D/g, '');
    if (d.startsWith('996')) d = d.slice(3);                  // код страны: подставлен полем или введён
    if (d.length > 9 && d.startsWith('996')) d = d.slice(3);  // после «+996 » вставили номер ещё раз с кодом
    // Коды операторов не начинаются с 0: это местная запись 0XXX, ноль убираем.
    d = d.replace(/^0+/, '').slice(0, 9);
    const parts = [d.slice(0, 3), d.slice(3, 6), d.slice(6, 9)].filter(Boolean);
    return '+996 ' + parts.join(' ');
  }

  const Phone = { normalizePhone, formatPhone };
  if (typeof module !== 'undefined' && module.exports) module.exports = Phone;
  else root.Phone = Phone;
})(typeof window !== 'undefined' ? window : globalThis);
