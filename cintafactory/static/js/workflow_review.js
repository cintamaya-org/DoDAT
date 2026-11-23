// SPDX-License-Identifier: AGPL-3.0-only
function initializeWorkflowReview() {
  const root = document.querySelector('.approval-workspace');
  if (!root || root.classList.contains('review-enhanced')) return;

  const find = selector => root.querySelector(selector);
  let canvas;
  const scroll = find('.review-scroll');
  const workbench = find('.review-workbench');
  const dialog = find('.review-fullscreen');
  const search = find('.review-search input');
  const results = find('.review-results');
  const nodes = new Map();
  let layout;
  let selected;
  let zoom = 1;
  let fit = false;

  for (const panel of root.querySelectorAll('[data-step]')) {
    const {step: id, lane, roleLabel, tone} = panel.dataset;
    const after = panel.dataset.after.split(' ').filter(Boolean);
    const name = panel.querySelector('h3').textContent;
    const node = {id, lane, roleLabel, tone, after, name, panel, links: new Map()};
    nodes.set(id, node);
    const back = panel.querySelector('.review-back');
    back.hidden = false;
    back.addEventListener('click', () => node.links.get(layout).focus());
  }
  if (!nodes.size) return;
  for (const link of root.querySelectorAll('a[data-node]')) {
    const node = nodes.get(link.dataset.node);
    node.links.set(link.closest('[data-layout]').dataset.layout, link);
    link.addEventListener('click', event => {
      if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      select(node, true);
    });
  }
  const steps = [...nodes.values()];
  const fromHash = () => steps.find(node => '#' + node.panel.id === location.hash);
  const summary = find('.review-summary');
  if (summary) {
    const completed = steps.filter(node => ['completed', 'skipped'].includes(node.tone)).length;
    find('[data-count="done"]').textContent = completed + '/' + steps.length;
    for (const tone of ['ready', 'blocked']) {
      find('[data-count="' + tone + '"]').textContent = steps.filter(node => node.tone === tone).length;
    }
    const progress = find('progress');
    progress.max = steps.length;
    progress.value = completed;
    summary.hidden = false;
  }

  function applyZoom() {
    if (fit) zoom = Math.min(1, scroll.clientWidth / canvas.offsetWidth);
    canvas.style.transform = 'scale(' + zoom + ')';
    const size = canvas.parentElement;
    size.style.width = Math.ceil(canvas.offsetWidth * zoom) + 'px';
    size.style.height = Math.ceil(canvas.offsetHeight * zoom) + 'px';
    find('output').textContent = Math.round(zoom * 100) + ' %';
  }

  function showLayout() {
    layout = find('[name="review-layout"]:checked').value;
    canvas = find('.review-size[data-layout="' + layout + '"] .review-canvas');
    applyZoom();
    highlight();
    reveal();
  }

  function highlight() {
    for (const node of steps) {
      const link = node.links.get(layout);
      link.setAttribute('aria-current', String(node === selected));
      link.classList.toggle('review-neighbor', selected.after.includes(node.id) || node.after.includes(selected.id));
      node.panel.hidden = node !== selected;
    }
    for (const edge of canvas.querySelectorAll('path[data-from]')) {
      edge.classList.toggle('review-direct', edge.dataset.from === selected.id || edge.dataset.to === selected.id);
    }
  }

  function reveal() {
    const button = selected.links.get(layout);
    scroll.scrollLeft = (button.offsetLeft + button.offsetWidth / 2) * zoom - scroll.clientWidth / 2;
    scroll.scrollTop = (button.offsetTop + button.offsetHeight / 2) * zoom - scroll.clientHeight / 2;
  }

  function select(node, focusPanel = false) {
    selected = node;
    highlight();
    reveal();
    if (focusPanel) {
      history.replaceState(history.state, '', '#' + node.panel.id);
      node.panel.querySelector('h3').focus();
    }
  }

  for (const radio of root.querySelectorAll('[name="review-layout"]')) {
    radio.addEventListener('change', showLayout);
  }
  for (const control of root.querySelectorAll('[data-zoom]')) {
    control.addEventListener('click', () => {
      fit = control.dataset.zoom === 'fit';
      if (!fit) zoom = Math.max(0.25, Math.min(3, zoom + (control.dataset.zoom === 'in' ? 0.1 : -0.1)));
      applyZoom();
      reveal();
    });
  }
  function selectFromHash() {
    const node = fromHash();
    if (node) select(node, true);
  }
  window.addEventListener('hashchange', selectFromHash);
  for (const link of root.querySelectorAll('.workflow-refusal-link')) {
    link.addEventListener('click', event => {
      if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      select(nodes.get(link.hash.slice('#review-'.length)), true);
    });
  }

  function clearSearch() {
    search.value = '';
    results.replaceChildren();
    results.hidden = true;
  }
  const normalize = value => value.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLocaleLowerCase('fr');
  search.addEventListener('input', () => {
    const query = normalize(search.value.trim());
    results.replaceChildren();
    results.hidden = !query;
    if (!query) return;
    for (const node of steps) {
      if (!normalize([node.name, node.lane, node.roleLabel].join(' ')).includes(query)) continue;
      const result = document.createElement('button');
      result.type = 'button';
      result.textContent = node.name + ' · ' + node.lane;
      result.addEventListener('click', () => { clearSearch(); select(node, true); });
      results.append(result);
    }
    if (!results.childElementCount) results.textContent = 'Aucune étape trouvée.';
  });
  search.addEventListener('keydown', event => {
    if (event.key === 'Enter') results.querySelector('button')?.click();
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      results.querySelector('button')?.focus();
    }
  });
  const fullscreen = find('[data-fullscreen]');
  fullscreen.addEventListener('click', () => {
    if (dialog.open) return dialog.close();
    dialog.append(workbench);
    find('.review-search').hidden = false;
    fullscreen.textContent = 'Quitter le plein écran';
    dialog.showModal();
    applyZoom();
    reveal();
    search.focus();
  });
  dialog.addEventListener('close', () => {
    dialog.before(workbench);
    find('.review-search').hidden = true;
    clearSearch();
    fullscreen.textContent = 'Plein écran';
    applyZoom();
    reveal();
    fullscreen.focus();
  });

  selected = fromHash() || steps.find(node => node.panel.querySelector('form'))
    || steps.find(node => node.tone === 'ready' || node.tone === 'blocked') || steps[0];
  root.classList.add('review-enhanced');
  showLayout();
  const observer = new ResizeObserver(() => { if (fit) applyZoom(); });
  observer.observe(scroll);
  document.addEventListener('turbolinks:before-render', () => {
    observer.disconnect();
    window.removeEventListener('hashchange', selectFromHash);
  }, {once: true});
}

document.addEventListener('turbolinks:load', initializeWorkflowReview);
initializeWorkflowReview();
