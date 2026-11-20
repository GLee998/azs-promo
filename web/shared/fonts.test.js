// Запуск: node --test web/client/*.test.js web/shared/*.test.js
// Шрифты лежат в проекте подмножествами. Кыргызские буквы ң ө ү (кириллица расширенная) должны входить
// в те же гарнитуры, иначе на кыргызской странице они выглядят чужим системным шрифтом.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const faces = [...fs.readFileSync(path.join(__dirname, 'vendor/fonts.css'), 'utf8').matchAll(/@font-face\s*\{([^}]*)\}/g)]
  .map(m => m[1]);

function covers(face, cp) {
  const m = face.match(/unicode-range:\s*([^;]+);/);
  return !!m && m[1].split(',').some(part => {
    const [lo, hi] = part.trim().replace(/^U\+/i, '').split('-').map(h => parseInt(h, 16));
    return cp >= lo && cp <= (hi ?? lo);
  });
}

test('Golos Text и Unbounded покрывают Ң ң Ө ө Ү ү', () => {
  for (const [family, weights] of [['Golos Text', [400, 500, 600, 700]], ['Unbounded', [500, 600, 700]]]) {
    for (const w of weights) {
      for (const cp of [0x04A2, 0x04A3, 0x04E8, 0x04E9, 0x04AE, 0x04AF]) {
        const ok = faces.some(f => f.includes(`'${family}'`) && f.includes(`font-weight: ${w};`) && covers(f, cp));
        assert.ok(ok, `${family} ${w}: U+${cp.toString(16).toUpperCase()}`);
      }
    }
  }
});
