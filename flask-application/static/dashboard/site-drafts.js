(() => {
    const form = document.getElementById('survey-form');
    if (!form) return;
    const select = document.getElementById('site_id');
    const fields = document.getElementById('new-site-fields');
    const button = document.getElementById('new-site-button');
    const status = document.getElementById('site-draft-status');
    const error = document.getElementById('new-site-error');
    const drafts = [];
    const group = document.createElement('optgroup');
    group.label = 'New sites (saved with survey)'; select.append(group);
    function drawOptions() {
        group.replaceChildren();
        drafts.forEach(site => {
            const option = document.createElement('option'); option.value = site.id; option.textContent = `${site.name} · Draft`; group.append(option);
        });
    }
    function toggle(open) {
        fields.hidden = !open; fields.disabled = !open; button.setAttribute('aria-expanded', String(open));
        if (open) document.getElementById('new-site-name').focus();
    }
    function announce() {
        status.textContent = select.value.startsWith('draft:') ? 'New site selected. It will be saved with your survey.' : '';
    }
    button.addEventListener('click', () => toggle(fields.hidden));
    document.getElementById('cancel-new-site').addEventListener('click', () => toggle(false));
    document.getElementById('save-site-draft').addEventListener('click', () => {
        error.hidden = true;
        for (const input of fields.querySelectorAll('input, textarea')) if (!input.reportValidity()) return;
        const value = key => document.getElementById(`new-site-${key}`).value.trim();
        if (!value('name')) { error.textContent = 'Enter a site name.'; error.hidden = false; return; }
        if (Boolean(value('latitude')) !== Boolean(value('longitude'))) {
            error.textContent = 'Enter both latitude and longitude, or leave both blank.'; error.hidden = false; return;
        }
        const site = {id: `draft:${crypto.randomUUID()}`, name: value('name'), region: value('region'),
            state: value('state'), country: value('country'), description: value('description'),
            latitude: value('latitude') ? Number(value('latitude')) : null,
            longitude: value('longitude') ? Number(value('longitude')) : null};
        drafts.push(site); drawOptions(); select.value = site.id;
        toggle(false); announce();
        select.dispatchEvent(new Event('change', {bubbles: true}));
    });
    select.addEventListener('change', announce);
    const payload = document.getElementById('new-site-payload');
    let restored = null;
    try {
        restored = JSON.parse(payload.value || 'null');
        if (restored && !drafts.some(site => site.id === restored.id)) drafts.push(restored);
    } catch (_) { /* Invalid form payload is handled by server validation. */ }
    drawOptions();
    if (restored) select.value = restored.id;
    announce();
    window.SiteDrafts = {
        selected: () => drafts.find(site => site.id === select.value) || null,
        isEditing: () => !fields.hidden,
    };
})();
