// Анимации лендинга: въезд «уравнения» в шапке, машинка на дороге-прогрессе, конфетти на финише.

const reduceMotion = matchMedia('(prefers-reduced-motion: reduce)').matches;

// Один заход при загрузке: стаканы, френч-доги и машинка собираются в уравнение.
if (window.gsap && !reduceMotion) {
  const tl = gsap.timeline({ defaults: { ease: 'power3.out' } });
  tl.from('.hero .reveal', { y: 24, opacity: 0, duration: .6, stagger: .08 })
    .from('.equation .piece:not(.car)', { y: 40, opacity: 0, scale: .6, duration: .5, stagger: .09, ease: 'back.out(2.2)' }, '-=.35')
    .from('#heroCar', { x: -320, opacity: 0, duration: .9, ease: 'power4.out' }, '-=.15')
    .to('#heroCar', { rotate: -3, y: -4, duration: .12, yoyo: true, repeat: 1, transformOrigin: '20% 100%' });
}

const Race = (() => {
  const racer = document.getElementById('racer');
  const steps = [...document.querySelectorAll('#milestones li')];
  let last = -1;

  function place(k, finished) {
    // 0..3 шага до флага, на финише машинка проезжает за него.
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

  function finish() {
    place(3, true);
    if (window.confetti && !reduceMotion) {
      const colors = ['#E4002B', '#FFC629', '#FFFFFF', '#2E3136'];
      confetti({ particleCount: 120, spread: 75, origin: { y: .3 }, colors, disableForReducedMotion: true });
      setTimeout(() => confetti({ particleCount: 60, angle: 120, spread: 60, origin: { x: 1, y: .4 }, colors }), 250);
    }
    if (window.gsap && !reduceMotion) {
      gsap.from('#successCard .finish-car', { x: -260, duration: .8, ease: 'power4.out' });
      gsap.from('#successCard .plate', { scale: .7, opacity: 0, duration: .5, delay: .3, ease: 'back.out(2)' });
    }
  }

  function reset() { last = -1; place(0, false); }

  ['input', 'change'].forEach(ev => document.getElementById('form').addEventListener(ev, update));
  place(0, false);
  return { update, finish, reset };
})();
