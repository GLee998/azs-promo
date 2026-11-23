// Русский и кыргызский язык формы. Русские тексты статичной разметки берутся из самой страницы,
// здесь только перевод на кыргызский и строки, которые подставляет form.js.

const LANG_KEY = 'karavan_lang';

const KY = {
  title: 'Coca‑Cola караваны: чекти каттоо',
  demo: 'Сыноо версиясы',
  h1: 'Coca‑Cola караваны менен Жаңы жылдык керемет',
  capToy: 'Оюнчук караван',
  capTrip: 'Саякат мүмкүнчүлүгү',
  lead: '2 френч-дог + 2 бөтөлкө Coca‑Cola 0,5 л = оюнчук караван жана саякатка чыгуу мүмкүнчүлүгү!',
  cta: 'Чекти каттоо',
  equation: '2 френч-дог плюс 2 бөтөлкө Coca‑Cola 0,5 литр барабар оюнчук караван жана саякатка чыгуу мүмкүнчүлүгү',
  regTitle: 'Чекти каттоо',
  regSub: 'Оюнчукту алганга чейин толтуруңуз, андан кийин экранды кассирге көрсөтүңүз.',
  msPhone: 'Телефон',
  msReceipt: 'Чек',
  msConsent: 'Макулдук',
  msKassa: 'Касса',
  phoneTitle: 'Телефон номери',
  phoneHint: 'Бир телефон номери акцияга бир гана жолу катыша алат.',
  photoTitle: 'Чектин сүрөтү',
  photoAlt: 'Чектин сүрөтү',
  photoHint: 'Толугу менен, чектин ылдыйкы жагындагы QR-код көрүнгөндөй',
  consentTitle: 'Макулдук',
  consent: '<a href="rules.html" target="_blank">Акциянын эрежелерин</a> кабыл алам жана <a href="privacy.html" target="_blank">жеке маалыматтарды иштетүү саясатына</a> ылайык жеке маалыматтарымды иштетүүгө макулдук берем.',
  wTitle: 'Өтүнмө кабыл алынды',
  wText: 'Бул экранды кассирге көрсөтүңүз: ал чекти текшерип, оюнчук караванды берет.',
  codeLabel: 'Өтүнмөнүн коду',
  showCashier: 'Кассирге көрсөтүү',
  cvTitle: 'Кассир үчүн өтүнмөнүн коду',
  cvHint: 'Кассир өтүнмөнү ушул код боюнча таап, чекти текшерет.',
  cvClose: 'Жабуу',
  entryLabel: 'Катышуучунун номери',
  plate: 'Өтүнмөнүн коду',
  wWait: 'Кассир берилгенин белгилешин күтүп жатабыз',
  iTitle: 'Оюнчук караван сиздики!',
  iText: 'Сиз жаңы жылдык караван менен <span class="prizes">8</span> саякаттын утуш оюнуна катышасыз. Катышуучунун номерин сактап коюңуз: ал ушул бетте калат.',
  cTitle: 'Өтүнмө жокко чыгарылды',
  again: 'Чекти кайра каттоо',
  howTitle: '«Coca‑Cola караваны» акциясына кантип катышуу керек',
  how1b: 'Комбо сатып алыңыз',
  how1: '«Coca‑Cola караваны» комбосу: 2 френч-дог жана 2 бөтөлкө Coca‑Cola 0,5 л.',
  how2b: 'Чекти каттаңыз',
  how2: 'Кассадагы QR-кодду сканерлеп, чекти телефон номериңиз менен каттаңыз. Бир чекти бир гана жолу каттоого болот.',
  how3b: 'Coca‑Cola оюнчук караванын алыңыз',
  how3: 'Оюнчук караван бүт акциянын ичинде бир телефон номерине бир гана жолу берилет.',
  how4b: 'Утуш оюнуна катышыңыз',
  how4: 'Бир телефон номери жаңы жылдык караван менен саякатты утуп алууга бир мүмкүнчүлүк берет. Бардыгы болуп <span class="prizes">8</span> саякат ойнотулат, ар бир жеңүүчүгө бирден.',
  footRules: 'Акциянын эрежелери',
  footPrivacy: 'Жеке маалыматтарды иштетүү саясаты',
};

// Строки, которые подставляет form.js.
const STRINGS = {
  ru: {
    station: 'АЗС №{n}',
    processing: 'Обрабатываем фото…',
    replacePhoto: 'Заменить фото',
    takePhoto: 'Сфотографировать чек',
    qrOk: 'QR-код чека распознан: ФД № {fd}, сумма {sum} сом',
    qrNo: 'QR-код не распознан. Можно отправить так: кассир сверит чек вручную.',
    fileBad: 'Не удалось открыть файл. Попробуйте другое фото.',
    phoneErr: 'Введите номер в формате +996 XXX XXX XXX',
    photoErr: 'Загрузите фото чека',
    consentErr: 'Нужно принять правила акции и согласие на обработку данных',
    checking: 'Отправляем заявку…',
    submit: 'Зарегистрировать чек',
  },
  ky: {
    station: 'АЗС №{n}',
    processing: 'Сүрөт иштетилүүдө…',
    replacePhoto: 'Сүрөттү алмаштыруу',
    takePhoto: 'Чекти сүрөткө тартуу',
    qrOk: 'Чектин QR-коду таанылды: ФД № {fd}, суммасы {sum} сом',
    qrNo: 'QR-код таанылган жок. Ушундай жөнөтсөңүз болот: кассир чекти кол менен текшерет.',
    fileBad: 'Файлды ачуу мүмкүн болгон жок. Башка сүрөттү колдонуп көрүңүз.',
    phoneErr: 'Номерди +996 XXX XXX XXX форматында киргизиңиз',
    photoErr: 'Чектин сүрөтүн жүктөңүз',
    consentErr: 'Акциянын эрежелерин жана маалыматтарды иштетүүгө макулдукту кабыл алуу керек',
    checking: 'Өтүнмө жөнөтүлүүдө…',
    submit: 'Чекти каттоо',
  },
};

// Ответы сервера приходят на русском: переводим известные сообщения.
const SERVER_KY = [
  [/^Этот чек уже зарегистрирован\. Один чек/, () => 'Бул чек мурда катталган. Бир чекти бир гана жолу каттоого болот.'],
  [/^Этот номер уже зарегистрирован, заявка (.+?) ждёт/, m => `Бул номер мурда катталган, ${m[1]} өтүнмөсү оюнчукту берүүнү күтүүдө. Аны кассирге көрсөтүңүз.`],
  [/^Этот номер уже получил/, () => 'Бул номерге оюнчук караван мурда берилген. Акциянын шарттары боюнча оюнчук бир номерге бир гана жолу берилет.'],
  [/^Не удалось зарегистрировать чек/, () => 'Чекти каттоо мүмкүн болгон жок. Кайра аракет кылыңыз.'],
  [/^Введите номер в формате/, () => STRINGS.ky.phoneErr],
  [/^Нужно согласие/, () => STRINGS.ky.consentErr],
  [/^Фото слишком большое/, () => 'Сүрөт өтө чоң (10 МБдан ашык).'],
  [/^Файл не похож на фото/, () => 'Файл сүрөткө окшобойт. Чектин сүрөтүн жүктөңүз.'],
  [/^Слишком большое изображение/, () => 'Сүрөт өтө чоң. Чекти кайра сүрөткө тартыңыз.'],
  [/^Нет связи с сервером/, () => 'Сервер менен байланыш жок. Интернетти текшерип, кайра аракет кылыңыз.'],
  [/^Слишком много попыток/, () => 'Аракет өтө көп. Бир нече мүнөттөн кийин кайталаңыз.'],
  [/^Сервер перегружен/, () => 'Сервер ашыкча жүктөлгөн. Бир мүнөттөн кийин кайра аракет кылыңыз.'],
  [/^Ошибка сервера \((\d+)\)/, m => `Сервер катасы (${m[1]})`],
  [/^(Машинку|Игрушку) не забрали вовремя/, () => 'Оюнчук өз убагында алынган жок.'],
  [/^Отменена на кассе/, () => 'Кассада жокко чыгарылды.'],
];

const I18n = {
  lang: 'ru',
  ru: {},

  t(key, vars = {}) {
    const s = STRINGS[this.lang][key] ?? STRINGS.ru[key] ?? key;
    return s.replace(/\{(\w+)\}/g, (_, k) => vars[k] ?? '');
  },

  // Перевод ответа сервера; незнакомое сообщение (например, причина отмены от кассира) оставляем как есть.
  server(msg) {
    if (this.lang !== 'ky' || !msg) return msg;
    for (const [re, fn] of SERVER_KY) {
      const m = msg.match(re);
      if (m) return fn(m);
    }
    return msg;
  },

  apply(lang) {
    this.lang = lang === 'ky' ? 'ky' : 'ru';
    document.documentElement.lang = this.lang;
    const dict = this.lang === 'ky' ? KY : this.ru;
    document.title = dict.title;
    document.querySelectorAll('[data-i18n]').forEach(el => { el.textContent = dict[el.dataset.i18n]; });
    document.querySelectorAll('[data-i18n-html]').forEach(el => { el.innerHTML = dict[el.dataset.i18nHtml]; });
    document.querySelectorAll('[data-i18n-aria]').forEach(el => { el.setAttribute('aria-label', dict[el.dataset.i18nAria]); });
    document.querySelectorAll('[data-i18n-alt]').forEach(el => { el.alt = dict[el.dataset.i18nAlt]; });
    document.querySelectorAll('.lang-switch button').forEach(b => b.setAttribute('aria-pressed', b.dataset.lang === this.lang));
    try { localStorage.setItem(LANG_KEY, this.lang); } catch {}
    document.dispatchEvent(new CustomEvent('langchange'));
  },

  init() {
    // Русский оригинал запоминаем до первого переключения.
    this.ru.title = document.title;
    document.querySelectorAll('[data-i18n]').forEach(el => { this.ru[el.dataset.i18n] = el.textContent; });
    document.querySelectorAll('[data-i18n-html]').forEach(el => { this.ru[el.dataset.i18nHtml] = el.innerHTML; });
    document.querySelectorAll('[data-i18n-aria]').forEach(el => { this.ru[el.dataset.i18nAria] = el.getAttribute('aria-label'); });
    document.querySelectorAll('[data-i18n-alt]').forEach(el => { this.ru[el.dataset.i18nAlt] = el.alt; });

    document.querySelectorAll('.lang-switch button').forEach(b =>
      b.addEventListener('click', () => this.apply(b.dataset.lang)));

    let saved = new URLSearchParams(location.search).get('lang');
    if (!saved) try { saved = localStorage.getItem(LANG_KEY); } catch {}
    if (saved === 'ky') this.apply('ky');
  },
};

I18n.init();
