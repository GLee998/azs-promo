// Запуск: node --test web/client/
const test = require('node:test');
const assert = require('node:assert');
const { formatPhone, normalizePhone } = require('./phone.js');

test('вставка номера с кодом страны после подставленного «+996 »', () => {
  assert.equal(formatPhone('+996 +996700123456'), '+996 700 123 456');
  assert.equal(formatPhone('+996 996 700 123 456'), '+996 700 123 456');
  assert.equal(formatPhone('+996 0700123456'), '+996 700 123 456');
});

test('набор по цифрам и номер с кодом оператора 996', () => {
  assert.equal(formatPhone('+996 7'), '+996 7');
  assert.equal(formatPhone('+996 996 123 456'), '+996 996 123 456');
});

test('normalizePhone', () => {
  assert.equal(normalizePhone('+996 700 123 456'), '+996700123456');
  assert.equal(normalizePhone('+996 700 123'), null);
});

test('международный префикс 00, лишние цифры и чужой код страны (I3)', () => {
  assert.equal(formatPhone('+996 00996 700 123 456'), '+996 700 123 456');
  assert.equal(formatPhone('00996 700 123 456'), '+996 700 123 456');
  assert.equal(normalizePhone('00996 700 123 456'), '+996700123456');
  // Лишняя цифра или чужой код страны: номер отклоняется, а не обрезается до правдоподобного.
  assert.equal(normalizePhone(formatPhone('+996 700 123 4567')), null);
  assert.equal(normalizePhone(formatPhone('+996 +7 701 123 45 67')), null);
  // Лишняя цифра после кода оператора 996 не схлопывает номер.
  assert.equal(formatPhone('+996 996 123 4567'), '+996 996 123 456 7');
});
