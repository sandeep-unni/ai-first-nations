// Filters the site cards by search text and state; without JS every card shows.
const search = document.getElementById('site-search');
const chips = document.querySelectorAll('.site-chip');
const cards = document.querySelectorAll('.site-card');
const noMatch = document.getElementById('site-no-match');
let state = '';

function applyFilter() {
    const query = (search ? search.value : '').trim().toLowerCase();
    let shown = 0;
    cards.forEach((card) => {
        const visible = (!state || card.dataset.state === state) && card.dataset.search.includes(query);
        card.hidden = !visible;
        if (visible) shown += 1;
    });
    if (noMatch) noMatch.hidden = shown > 0 || cards.length === 0;
}

if (search) search.addEventListener('input', applyFilter);
chips.forEach((chip) => chip.addEventListener('click', () => {
    state = chip.dataset.state;
    chips.forEach((other) => other.setAttribute('aria-pressed', String(other === chip)));
    applyFilter();
}));
