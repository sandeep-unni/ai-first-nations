/* A single worker limits memory use when selecting large drone batches. */
(() => {
    const scriptUrl = document.currentScript.src;
    const workerUrl = new URL('metadata-worker.js', scriptUrl);
    const cache = new WeakMap();
    let queue = Promise.resolve();
    function extract(file) {
        return new Promise(resolve => {
            let worker;
            let timer;
            const finish = result => { clearTimeout(timer); worker?.terminate(); resolve(result); };
            try {
                worker = new Worker(workerUrl);
                timer = setTimeout(() => finish({status: 'error', message: 'Metadata reading timed out. The image can still be uploaded.', summary: {}, tags: {}}), 15000);
                worker.onmessage = event => finish(event.data);
                worker.onerror = () => finish({status: 'error', message: 'Metadata preview is unavailable in this browser.', summary: {}, tags: {}});
                worker.postMessage({file});
            } catch (error) {
                finish({status: 'error', message: 'Metadata preview is unavailable in this browser.', summary: {}, tags: {}});
            }
        });
    }
    window.SurveyMetadata = {
        read(file) {
            if (!cache.has(file)) {
                const task = queue.then(() => extract(file));
                queue = task.catch(() => {});
                cache.set(file, task);
            }
            return cache.get(file);
        },
    };
})();
