// Анимации лендинга: въезд каравана и «уравнения» в шапке, машинка на дороге-прогрессе, конфетти при выдаче.

const reduceMotion = matchMedia('(prefers-reduced-motion: reduce)').matches;

// Один заход при загрузке: текст, затем собирается комбо, из него появляется трак, въезжает караван.
if (window.gsap && !reduceMotion) {
  const tl = gsap.timeline({ defaults: { ease: 'power3.out' } });
  tl.from('.hero .reveal', { y: 24, opacity: 0, duration: .6, stagger: .08 })
    .from('.combo .piece', { y: 20, opacity: 0, scale: .7, duration: .45, stagger: .08, ease: 'back.out(2)' }, '-=.35')
    .from('#heroCar', { x: -40, scale: .6, opacity: 0, duration: .6, ease: 'back.out(1.8)' }, '-=.1')
    .from('#truck', { x: '-120vw', duration: 1.6, ease: 'power2.out' }, '-=.6')
    .to('#truck', { rotate: -1.5, y: -3, duration: .14, yoyo: true, repeat: 1, transformOrigin: '90% 100%' });
}

const Race = (() => {
  const racer = document.getElementById('racer');
  const steps = [...document.querySelectorAll('#milestones li')];
  let last = -1;

  // k — сколько этапов пройдено (0..3); finished — машинка проехала за флаг.
  function place(k, finished) {
    const pct = finished ? 100 : (k / 3) * 78;
    racer.style.left = `calc(${pct}% - ${finished ? 98 : pct * 0.9}px + 6px)`;
    steps.forEach((li, i) => li.classList.toggle('done', finished || i < k));
    if (k !== last && last !== -1) {
      racer.classList.remove('revving');
      void racer.offsetWidth;
      racer.classList.add('revving');
    }
    last = k;
  }

  function update() {
    const done = [
      !!normalizePhone(document.getElementById('phone').value),
      !!state.photo,
      document.getElementById('consent').checked,
    ];
    place(done.filter(Boolean).length, false);
  }

  // Заявка принята: машинка стоит у кассы и ждёт выдачи.
  function arrive() { place(3, false); }

  // Кассир отметил выдачу: финиш. quiet — заявку открыли заново после выдачи, без салюта.
  function finish(quiet) {
    place(3, true);
    if (quiet) return;
    if (window.confetti && !reduceMotion) {
      const colors = ['#008ED7', '#AE4A84', '#FFFFFF', '#F07DB8'];
      confetti({ particleCount: 120, spread: 75, origin: { y: .3 }, colors, disableForReducedMotion: true });
      setTimeout(() => confetti({ particleCount: 60, angle: 120, spread: 60, origin: { x: 1, y: .4 }, colors }), 250);
    }
    if (window.gsap && !reduceMotion) {
      gsap.from('#ticket .finish-car', { x: -220, duration: .8, ease: 'power4.out' });
      gsap.from('#entryNumber', { scale: .7, opacity: 0, duration: .5, delay: .3, ease: 'back.out(2)' });
    }
  }

  function reset() { last = -1; place(0, false); }

  ['input', 'change'].forEach(ev => document.getElementById('form').addEventListener(ev, update));
  place(0, false);
  return { update, arrive, finish, reset };
})();
