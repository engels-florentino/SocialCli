/* Human-facing installation handoff. No installation or account actions here. */
(() => {
  const prompt = document.getElementById('install-prompt');
  const button = document.getElementById('copy-install-prompt');
  const status = document.getElementById('copy-install-status');
  if (!prompt || !button || !status) return;
  let reset;
  button.hidden = false;
  button.addEventListener('click', async () => {
    clearTimeout(reset);
    button.removeAttribute('data-copied');
    try {
      await navigator.clipboard.writeText(prompt.value);
      button.dataset.copied = 'true';
      button.setAttribute('aria-label', 'Copied');
      button.title = 'Copied';
      status.textContent = 'Copied. Paste into your AI assistant.';
      reset = setTimeout(() => {
        button.removeAttribute('data-copied');
        button.setAttribute('aria-label', 'Copy installation prompt');
        button.title = 'Copy installation prompt';
        status.textContent = '';
      }, 4000);
    } catch {
      prompt.focus();
      prompt.select();
      button.setAttribute('aria-label', 'Copy installation prompt');
      button.title = 'Copy installation prompt';
      status.textContent = 'Select Copy from your device’s menu, then paste into your AI assistant.';
    }
  });
})();
