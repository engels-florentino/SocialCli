/* A local, fictional demonstration. No network writes or account access. */
(() => {
  const video = document.getElementById('mascot-video');
  const scenes = [...document.querySelectorAll('[data-scene]')];
  const motion = document.getElementById('demo-motion');
  const controls = document.querySelector('.demo-controls');
  const chat = document.getElementById('demo-chat');
  if (!video || !scenes.length || !motion || !controls || !chat) return;
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  let paused = reduced.matches;
  let scene = 0;
  let timer;
  let pending;
  let remaining = 0;
  let started = 0;
  let videoLoaded = false;
  const titles = ['Account insights example', 'Reply draft example', 'Approval example. No real comment was posted.'];
  const typing = scenes.map(panel => {
    const bubble = document.createElement('div');
    bubble.className = 'bubble assistant-bubble typing-bubble';
    bubble.setAttribute('aria-label', 'Your assistant is typing');
    bubble.innerHTML = '<span aria-hidden="true"></span><span aria-hidden="true"></span><span aria-hidden="true"></span>';
    bubble.hidden = true;
    panel.querySelector('.assistant-bubble').before(bubble);
    return bubble;
  });
  function cancelStep() { clearTimeout(timer); timer = undefined; pending = undefined; }
  function freezeStep() {
    if (timer === undefined) return;
    clearTimeout(timer);
    timer = undefined;
    remaining = Math.max(0, remaining - (performance.now() - started));
  }
  function resumeStep() {
    if (!pending || timer !== undefined || paused || document.hidden) return;
    started = performance.now();
    timer = setTimeout(() => {
      timer = undefined;
      const next = pending;
      pending = undefined;
      next();
    }, remaining);
  }
  function after(delay, next) { cancelStep(); remaining = delay; pending = next; resumeStep(); }
  function render(next, announce = false, animate = false) {
    cancelStep();
    scene = next;
    scenes.forEach((panel, index) => {
      panel.hidden = index !== scene;
      typing[index].hidden = true;
      panel.querySelector('.assistant-bubble:not(.typing-bubble)').hidden = animate && index === scene;
      panel.querySelectorAll('.chat-suggestion').forEach(button => { button.hidden = animate && index === scene; });
      panel.classList.toggle('conversation-playing', animate && index === scene);
    });
    chat.scrollTop = 0;
    controls.querySelectorAll('[data-goto]').forEach(button => {
      button.setAttribute('aria-pressed', String(Number(button.dataset.goto) === scene));
    });
    if (announce) document.getElementById('demo-announcement').textContent = titles[scene];
    if (!animate) return;
    // A question arrives, the assistant thinks, and then its response appears.
    after(650, () => {
      typing[scene].hidden = false;
      after(scene === 0 ? 1250 : 1500, () => {
        typing[scene].hidden = true;
        const response = scenes[scene].querySelector('.assistant-bubble:not(.typing-bubble)');
        response.hidden = false;
        scenes[scene].querySelectorAll('.chat-suggestion').forEach(button => { button.hidden = false; });
        // Reading time follows the response, not a blank/static 18-second page.
        // The preview stops here: only a deliberate demo click approves a reply.
        if (scene === 0) after(6000, () => render(1, false, true));
      });
    });
  }
  function synchronize() {
    document.body.classList.toggle('motion-paused', paused || document.hidden);
    motion.textContent = paused ? 'Play animation' : 'Pause animation';
    if (paused || document.hidden) { freezeStep(); video.pause(); return; }
    if (!videoLoaded) {
      const source = video.querySelector('source');
      source.src = source.dataset.src;
      video.load();
      videoLoaded = true;
    }
    video.play().catch(() => { /* The poster remains available if autoplay is blocked. */ });
    resumeStep();
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
    if (!paused && !pending) render(0, false, true);
    synchronize();
  });
  reduced.addEventListener('change', event => {
    paused = event.matches;
    render(scene, false, !paused);
    synchronize();
  });
  document.addEventListener('visibilitychange', synchronize);
  video.addEventListener('error', () => { video.pause(); });
  render(0, false, !paused);
  synchronize();
})();
