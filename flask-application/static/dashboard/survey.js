(() => {
    const analysis = document.getElementById('analysis-progress');
    if (analysis) {
        const bar = document.getElementById('analysis-progress-bar');
        const label = document.getElementById('analysis-progress-label');
        const note = document.getElementById('analysis-progress-note');
        async function refreshProgress() {
            if (!document.hidden) {
                try {
                    const response = await fetch(analysis.dataset.progressUrl, {cache: 'no-store', signal: AbortSignal.timeout(10000)});
                    if (!response.ok) throw new Error('Progress unavailable');
                    const result = await response.json();
                    if (['completed', 'failed'].includes(result.status)) { location.reload(); return; }
                    const processed = result.completed + result.failed;
                    bar.max = result.total || 1;
                    bar.value = processed;
                    const percent = result.total ? Math.floor(processed / result.total * 100) : 0;
                    label.textContent = `${result.status === 'pending' ? 'Waiting for analysis' : 'Analysing images'} — ${processed} of ${result.total} processed (${percent}%)`;
                    note.textContent = result.failed ? `${result.failed} images could not be analysed. You can retry them when processing finishes.` : 'Results will appear automatically when analysis finishes.';
                } catch (_) {
                    note.textContent = 'Reconnecting to check progress. You can leave this page; saved images will continue processing.';
                }
            }
            setTimeout(refreshProgress, 3000);
        }
        setTimeout(refreshProgress, 1000);
    }
    const form = document.getElementById('survey-form');
    if (!form) return;
    const stages = [...form.querySelectorAll('.survey-stage')];
    const steps = [...form.querySelectorAll('.survey-steps li')];
    const input = document.getElementById('survey-images');
    const error = document.getElementById('batch-error');
    const list = document.getElementById('image-selection');
    const count = document.getElementById('batch-count');
    const maxImages = Number(form.dataset.maxImages);
    const maxImageBytes = Number(form.dataset.maxImageBytes);
    const maxTotalBytes = Number(form.dataset.maxTotalBytes);
    let files = [];
    let stage = 0;
    let objectUrls = [];
    let submitting = false;
    const metadata = new WeakMap();
    const siteSelect = document.getElementById('site_id');
    const exportMetadata = document.getElementById('download-metadata');
    function downloadJson(name, payload) {
        const url = URL.createObjectURL(new Blob([JSON.stringify(payload, null, 2)], {type: 'application/json'}));
        const link = document.createElement('a'); link.href = url; link.download = name; link.click();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
    }
    function metadataRecords() {
        return files.map(file => ({filename: file.name, sizeBytes: file.size, mimeType: file.type,
            fileModifiedTime: new Date(file.lastModified).toISOString(), ...metadata.get(file)}));
    }
    function updateMetadataProgress() {
        const ready = files.filter(file => metadata.has(file)).length;
        const gps = files.filter(file => metadata.get(file)?.summary?.latitude != null && metadata.get(file)?.summary?.longitude != null).length;
        const failed = files.filter(file => metadata.get(file)?.status === 'error').length;
        document.getElementById('metadata-progress').textContent = files.length ? `${ready} of ${files.length} images read · ${gps} with GPS${failed ? ` · ${failed} could not be read` : ''}` : 'Choose images to read their available metadata.';
        exportMetadata.disabled = !files.length || ready < files.length;
        document.getElementById('download-survey-draft').disabled = ready < files.length;
    }
    function appendMetadata(file, description) {
        const container = document.createElement('div'); container.className = 'image-metadata';
        container.textContent = 'Reading metadata…'; description.append(container);
        window.SurveyMetadata.read(file).then(result => {
            metadata.set(file, result); updateMetadataProgress(); container.replaceChildren();
            if (result.status === 'error') { container.textContent = result.message; return; }
            const summary = result.summary;
            const highlights = [];
            if (summary.width && summary.height) highlights.push(`${summary.width} × ${summary.height}`);
            if (summary.cameraMake || summary.cameraModel) highlights.push([summary.cameraMake, summary.cameraModel].filter(Boolean).join(' '));
            if (summary.captureTime) highlights.push(`Captured ${summary.captureTime}${summary.captureTimeOffset ? ` ${summary.captureTimeOffset}` : /(?:Z|[+-]\d{2}:?\d{2})$/.test(String(summary.captureTime)) ? '' : ' (timezone not recorded)'}`);
            if (summary.latitude != null && summary.longitude != null) highlights.push(`GPS ${summary.latitude.toFixed(6)}, ${summary.longitude.toFixed(6)}`);
            const text = document.createElement('p'); text.textContent = highlights.join(' · ') || 'No capture time, camera or GPS tags found.'; container.append(text);
            const details = document.createElement('details');
            const heading = document.createElement('summary'); heading.textContent = 'View extracted metadata';
            const pre = document.createElement('pre'); pre.textContent = JSON.stringify(result, null, 2);
            details.append(heading, pre); container.append(details);
        });
    }
    exportMetadata.addEventListener('click', () => downloadJson('image-metadata.json', {version: 1, images: metadataRecords()}));
    document.getElementById('download-survey-draft').addEventListener('click', () => {
        if (!validateDetails()) return;
        downloadJson('survey-draft.json', {version: 1, status: 'draft',
            site: window.SiteDrafts.selected() || {id: siteSelect.value, name: siteSelect.selectedOptions[0].textContent},
            survey: {name: form.elements.survey_name.value.trim(), date: form.elements.survey_date.value, notes: form.elements.notes.value},
            images: metadataRecords()});
    });
    // Keep the native form fully usable if the browser cannot manage a file list.
    if (typeof DataTransfer === 'undefined') return;
    form.querySelectorAll('.js-only').forEach(node => { node.hidden = false; });
    form.noValidate = true;
    const mb = size => `${(size / 1048576).toFixed(1)} MB`;
    const showError = message => { error.textContent = message; error.hidden = !message; };
    function showStage(index, focus = true) {
        stage = index;
        stages.forEach((node, i) => { node.hidden = i !== index; });
        steps.forEach((node, i) => {
            if (i === index) node.setAttribute('aria-current', 'step');
            else node.removeAttribute('aria-current');
        });
        if (focus) stages[index].querySelector('h2').focus();
    }
    function renderFiles() {
        objectUrls.forEach(url => URL.revokeObjectURL(url));
        objectUrls = [];
        const transfer = new DataTransfer();
        files.forEach(file => transfer.items.add(file));
        input.files = transfer.files;
        list.replaceChildren();
        count.textContent = files.length ? `${files.length} images selected · ${mb(files.reduce((total, file) => total + file.size, 0))}` : 'No images selected';
        files.forEach((file, i) => {
            const item = document.createElement('li');
            if (/\.(jpe?g|png)$/i.test(file.name)) {
                const img = document.createElement('img');
                img.src = URL.createObjectURL(file);
                objectUrls.push(img.src);
                img.alt = '';
                img.loading = 'lazy';
                item.append(img);
            } else {
                const icon = document.createElement('span');
                icon.className = 'file-type'; icon.textContent = 'TIFF'; item.append(icon);
            }
            const description = document.createElement('div');
            const name = document.createElement('strong'); name.textContent = file.name;
            const size = document.createElement('small'); size.textContent = mb(file.size);
            description.append(name, size);
            appendMetadata(file, description);
            const remove = document.createElement('button');
            remove.type = 'button'; remove.className = 'btn small'; remove.textContent = 'Remove';
            remove.setAttribute('aria-label', `Remove ${file.name}`);
            remove.addEventListener('click', () => { files.splice(i, 1); renderFiles(); showError(''); });
            item.append(description, remove); list.append(item);
        });
        updateMetadataProgress();
    }
    function addFiles(incoming) {
        const combined = [...files];
        for (const file of incoming) {
            if (!combined.some(existing => existing.name === file.name && existing.size === file.size && existing.lastModified === file.lastModified)) combined.push(file);
        }
        let message = '';
        if (combined.length > maxImages) message = `Choose up to ${maxImages} images per survey.`;
        else if (combined.some(file => !/\.(jpe?g|png|tiff?)$/i.test(file.name))) message = 'Choose JPG, PNG or TIFF images only.';
        else if (combined.some(file => file.size === 0 || file.size > maxImageBytes)) message = `Each image must contain data and be no larger than ${mb(maxImageBytes)}.`;
        else if (combined.reduce((total, file) => total + file.size, 0) >= maxTotalBytes - 1048576) message = 'The batch is too large. Choose fewer images.';
        if (!message) files = combined;
        renderFiles(); showError(message);
    }
    input.addEventListener('change', () => addFiles([...input.files]));
    document.getElementById('clear-images').addEventListener('click', () => { files = []; renderFiles(); showError(''); });
    const drop = document.getElementById('image-drop-zone');
    ['dragenter', 'dragover'].forEach(event => drop.addEventListener(event, e => { e.preventDefault(); drop.classList.add('drag-over'); }));
    drop.addEventListener('dragleave', () => drop.classList.remove('drag-over'));
    drop.addEventListener('drop', e => { e.preventDefault(); drop.classList.remove('drag-over'); addFiles([...e.dataTransfer.files]); });
    function validateDetails() {
        if (window.SiteDrafts.isEditing()) {
            showStage(0);
            const message = document.getElementById('new-site-error'); message.textContent = 'Use this site draft or cancel it before continuing.'; message.hidden = false;
            return false;
        }
        for (const field of stages[0].querySelectorAll('input, select, textarea')) {
            if (!field.checkValidity()) { showStage(0); field.reportValidity(); return false; }
        }
        return true;
    }
    function review() {
        const panel = document.getElementById('survey-review'); panel.replaceChildren();
        const selected = document.getElementById('site_id');
        const draft = window.SiteDrafts.selected();
        document.getElementById('save-survey').hidden = false;
        document.getElementById('existing-site-save-note').hidden = false;
        document.getElementById('draft-review-notice').hidden = !draft;
        const values = [['Site', selected.selectedOptions[0].textContent], ['Survey', form.elements.survey_name.value],
            ['Date', form.elements.survey_date.value], ['Images', `${files.length} · ${mb(files.reduce((total, file) => total + file.size, 0))}`],
            ['Notes', form.elements.notes.value || 'No field notes']];
        if (draft && draft.latitude != null && draft.longitude != null) values.push(['Site coordinates', `${draft.latitude}, ${draft.longitude}`]);
        values.forEach(([label, value]) => { const dt = document.createElement('dt'); dt.textContent = label; const dd = document.createElement('dd'); dd.textContent = value; panel.append(dt, dd); });
    }
    form.querySelectorAll('[data-next]').forEach(button => button.addEventListener('click', () => {
        if (stage === 0 && !validateDetails()) return;
        if (stage === 1 && !files.length) { showError('Select at least one drone image.'); return; }
        if (stage === 1) review();
        showStage(stage + 1);
    }));
    form.querySelectorAll('[data-back]').forEach(button => button.addEventListener('click', () => showStage(stage - 1)));
    form.addEventListener('submit', event => {
        if (submitting) { event.preventDefault(); return; }
        if (stage < 2) { event.preventDefault(); stages[stage].querySelector('[data-next]').click(); return; }
        if (!validateDetails()) { event.preventDefault(); return; }
        if (!files.length) { event.preventDefault(); showStage(1); showError('Select at least one drone image.'); return; }
        event.preventDefault();
        document.getElementById('new-site-payload').value = window.SiteDrafts.selected() ? JSON.stringify(window.SiteDrafts.selected()) : '';
        const payload = new FormData(form);
        submitting = true;
        const controls = [...form.elements].map(control => [control, control.disabled]);
        controls.forEach(([control]) => { control.disabled = true; });
        const panel = document.getElementById('upload-progress');
        const bar = document.getElementById('upload-progress-bar');
        const label = document.getElementById('upload-progress-label');
        const uploadError = document.getElementById('upload-error');
        panel.hidden = false;
        uploadError.hidden = true;
        bar.value = 0;
        label.textContent = 'Uploading images — 0%';
        const request = new XMLHttpRequest();
        request.open('POST', form.action);
        function failed(message) {
            submitting = false;
            controls.forEach(([control, disabled]) => { control.disabled = disabled; });
            panel.hidden = true;
            uploadError.textContent = message;
            uploadError.hidden = false;
        }
        request.upload.addEventListener('progress', progress => {
            if (progress.lengthComputable) {
                const percent = Math.floor(progress.loaded / progress.total * 100);
                bar.value = percent;
                label.textContent = `Uploading images — ${percent}%`;
            }
        });
        request.upload.addEventListener('load', () => {
            bar.removeAttribute('value');
            label.textContent = 'Saving images and survey…';
        });
        request.addEventListener('load', () => {
            const destination = new URL(request.responseURL || form.action);
            if (request.status === 200 && destination.origin === location.origin && /^\/surveys\/\d+$/.test(destination.pathname)) {
                submitting = false;
                location.assign(destination.href);
                return;
            }
            const response = new DOMParser().parseFromString(request.responseText, 'text/html');
            // Refresh expired form tokens while preserving the user's selected files.
            for (const name of ['csrf_token', 'submission_token']) {
                const replacement = response.querySelector(`input[name="${name}"]`);
                if (replacement) form.elements[name].value = replacement.value;
            }
            const messages = [...response.querySelectorAll('.flash.error li')].map(item => item.textContent);
            failed(messages.join(' ') || 'We could not confirm that the survey was saved. Your images are still selected; please try again.');
        });
        request.addEventListener('error', () => failed('Connection lost. Your images are still selected; please try again.'));
        request.addEventListener('abort', () => failed('Upload stopped. Please try again.'));
        request.send(payload);
    });
    window.addEventListener('beforeunload', event => {
        if (submitting) { event.preventDefault(); event.returnValue = ''; }
    });
    const storageNote = document.getElementById('image-storage-note');
    const existingStorageNote = storageNote.textContent;
    siteSelect.addEventListener('change', () => {
        storageNote.textContent = existingStorageNote;
        renderFiles(); if (stage === 2) review();
    });
    window.addEventListener('pageshow', event => { if (event.persisted) window.location.reload(); });
    showStage(0, false);
})();
