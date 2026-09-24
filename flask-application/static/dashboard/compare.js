// Tooltips for the Compare charts: hover or keyboard focus shows the value.
// Every value is also printed in the page's table and timeline.
document.querySelectorAll('.compare-grid .card').forEach((card) => {
    const tip = card.querySelector('.viz-tip') || (() => {
        const el = document.createElement('div');
        el.className = 'viz-tip';
        el.setAttribute('role', 'status');
        el.hidden = true;
        el.append(document.createElement('strong'), document.createElement('span'));
        card.style.position = 'relative';
        card.append(el);
        return el;
    })();
    const crosshair = card.querySelector('.crosshair');
    const svg = card.querySelector('svg');

    function show(mark, event) {
        tip.querySelector('strong').textContent = mark.dataset.tipValue;
        tip.querySelector('span').textContent = mark.dataset.tipLabel;
        tip.hidden = false;
        // Position against the element the tooltip is laid out in.
        const box = (tip.offsetParent || card).getBoundingClientRect();
        const target = (mark.querySelector('circle') || mark).getBoundingClientRect();
        const x = event && event.clientX ? event.clientX : target.left + target.width / 2;
        tip.style.left = `${Math.min(Math.max(x - box.left, 80), box.width - 80)}px`;
        tip.style.top = `${target.top - box.top - 8}px`;
        card.querySelectorAll('.is-active').forEach((el) => el.classList.remove('is-active'));
        mark.classList.add('is-active');
        if (crosshair && mark.dataset.x) {
            crosshair.setAttribute('x1', mark.dataset.x);
            crosshair.setAttribute('x2', mark.dataset.x);
            crosshair.hidden = false;
        }
    }
    function hide() {
        tip.hidden = true;
        if (crosshair) crosshair.hidden = true;
        card.querySelectorAll('.is-active').forEach((el) => el.classList.remove('is-active'));
    }

    const marks = [...card.querySelectorAll('[data-tip-value]')];
    marks.forEach((mark) => {
        mark.addEventListener('pointerenter', (event) => show(mark, event));
        mark.addEventListener('focus', () => show(mark));
        mark.addEventListener('blur', hide);
    });
    // On the line chart the crosshair snaps to the nearest survey date.
    if (svg && crosshair) {
        svg.addEventListener('pointermove', (event) => {
            const point = svg.createSVGPoint();
            point.x = event.clientX;
            point.y = event.clientY;
            const x = point.matrixTransform(svg.getScreenCTM().inverse()).x;
            const nearest = marks.reduce((best, mark) =>
                Math.abs(mark.dataset.x - x) < Math.abs(best.dataset.x - x) ? mark : best);
            show(nearest, event);
        });
        svg.addEventListener('pointerleave', hide);
    } else {
        card.addEventListener('pointerleave', hide);
    }
});
