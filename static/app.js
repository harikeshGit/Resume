(() => {
    // Apply progress bar widths from server-rendered values.
    document.querySelectorAll('.bar-fill[data-width]').forEach((el) => {
        const raw = el.getAttribute('data-width');
        const n = Number(raw);
        const clamped = Number.isFinite(n) ? Math.max(0, Math.min(100, n)) : 0;
        el.style.width = `${clamped}%`;
    });

    const input = document.getElementById('resumes');
    const help = document.getElementById('fileHelp');
    if (!input || !help) return;

    const render = () => {
        const files = Array.from(input.files || []);
        if (!files.length) {
            help.textContent = 'No files selected.';
            return;
        }

        const list = document.createElement('ul');
        for (const f of files) {
            const li = document.createElement('li');
            li.textContent = `${f.name} (${Math.max(1, Math.round(f.size / 1024))} KB)`;
            list.appendChild(li);
        }

        help.textContent = `${files.length} file(s) selected:`;
        help.appendChild(list);
    };

    input.addEventListener('change', render);
})();
