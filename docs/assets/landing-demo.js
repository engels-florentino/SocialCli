/* A local, fictional demonstration. No network writes or account access. */
(() => {
  const video = document.getElementById('mascot-video');
  const scenes = [...document.querySelectorAll('[data-scene]')];
  const motion = document.getElementById('demo-motion');
  const controls = document.querySelector('.demo-controls');
  if (!video || !scenes.length || !motion || !controls) return;
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  let paused = reduced.matches;
  let scene = 0;
  let timer;
  let videoLoaded = false;
  const titles = ['Weekly insights example', 'Reply draft example', 'Approval example. No real comment was posted.'];
  function render(next, announce = false) {
    scene = next;
    scenes.forEach((panel, index) => { panel.hidden = index !== scene; });
    controls.querySelectorAll('[data-goto]').forEach(button => {
      button.setAttribute('aria-pressed', String(Number(button.dataset.goto) === scene));
    });
    if (announce) document.getElementById('demo-announcement').textContent = titles[scene];
  }
  function synchronize() {
    clearTimeout(timer);
    document.body.classList.toggle('motion-paused', paused || document.hidden);
    motion.textContent = paused ? 'Play animation' : 'Pause animation';
    if (paused || document.hidden) { video.pause(); return; }
    if (!videoLoaded) {
      const source = video.querySelector('source');
      source.src = source.dataset.src;
      video.load();
      videoLoaded = true;
    }
    video.play().catch(() => { /* The poster remains available if autoplay is blocked. */ });
    // Stop at the preview. Approval only happens after a deliberate demo click.
    if (scene === 0) timer = setTimeout(() => { render(1); synchronize(); }, 18000);
  }
  motion.hidden = false;
  controls.hidden = false;
  document.querySelectorAll('.chat-suggestion, .demo-approve').forEach(button => { button.hidden = false; });
  document.querySelectorAll('[data-goto]').forEach(button => {
    button.addEventListener('click', () => { paused = true; render(Number(button.dataset.goto), true); synchronize(); });
  });
  document.getElementById('demo-approve').addEventListener('click', () => {
    paused = true; render(2, true); synchronize();
  });
  motion.addEventListener('click', () => {
    paused = !paused;
    if (!paused && scene !== 0) render(0);
    synchronize();
  });
  reduced.addEventListener('change', event => { paused = event.matches; synchronize(); });
  document.addEventListener('visibilitychange', synchronize);
  video.addEventListener('error', () => { video.pause(); });
  synchronize();
})();
