(() => {
    // Navbar back button.
    const backBtn = document.getElementById('navBack');
    if (backBtn) {
        const path = (window.location && window.location.pathname) ? window.location.pathname : '';
        if (path === '/' || path === '') {
            backBtn.style.display = 'none';
        }

        backBtn.addEventListener('click', () => {
            if (window.history && window.history.length > 1) {
                window.history.back();
                return;
            }
            window.location.href = '/';
        });
    }

    // Apply progress bar widths from server-rendered values.
    document.querySelectorAll('.bar-fill[data-width]').forEach((el) => {
        const raw = el.getAttribute('data-width');
        const n = Number(raw);
        const clamped = Number.isFinite(n) ? Math.max(0, Math.min(100, n)) : 0;

        // Start at 0% so CSS transition can animate to the target width.
        el.style.width = '0%';
        requestAnimationFrame(() => {
            el.style.width = `${clamped}%`;
        });
    });

    // Add a tiny loading state on the screening form submit.
    document.querySelectorAll('form[action="/screen"]').forEach((form) => {
        form.addEventListener('submit', () => {
            const btn = form.querySelector('button[type="submit"]');
            if (!btn) return;
            if (btn.classList.contains('is-loading')) return;
            btn.classList.add('is-loading');
            btn.disabled = true;
            btn.dataset.originalText = btn.textContent || '';
            btn.textContent = 'Ranking...';
        });
    });

    // Loading state for ATS scan submit.
    document.querySelectorAll('form[action="/ats-scan"]').forEach((form) => {
        form.addEventListener('submit', () => {
            const btn = form.querySelector('button[type="submit"]');
            if (!btn) return;
            if (btn.classList.contains('is-loading')) return;
            btn.classList.add('is-loading');
            btn.disabled = true;
            btn.dataset.originalText = btn.textContent || '';
            btn.textContent = 'Scanning...';
        });
    });

    // Staggered reveal for results rows (purely cosmetic).
    document.querySelectorAll('#results .table tbody tr').forEach((row, idx) => {
        const delay = Math.min(360, idx * 28);
        row.style.setProperty('--reveal-delay', `${delay}ms`);
    });

    const attachFileHelp = (inputId, helpId, emptyText) => {
        const input = document.getElementById(inputId);
        const help = document.getElementById(helpId);
        if (!input || !help) return;

        const render = () => {
            const files = Array.from(input.files || []);
            if (!files.length) {
                help.textContent = emptyText;
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

            // Pulse the helper box to acknowledge the update.
            help.classList.remove('is-updated');
            void help.offsetWidth;
            help.classList.add('is-updated');
        };

        input.addEventListener('change', render);
    };

    attachFileHelp('resumes', 'fileHelp', 'No files selected.');
    attachFileHelp('ats_resume', 'atsFileHelp', 'No file selected.');

    // Download ATS draft as PDF (server-generated).
    const dlPdfBtn = document.getElementById('downloadAtsDraftPdf');
    const draftTa = document.getElementById('atsDraftText');
    if (dlPdfBtn && draftTa) {
        dlPdfBtn.addEventListener('click', async () => {
            const text = draftTa.value || '';
            if (!text.trim()) return;

            const rawName = draftTa.getAttribute('data-filename') || 'resume';
            const base = String(rawName).replace(/\.pdf$/i, '').replace(/[^a-z0-9._-]+/gi, '_').slice(0, 80) || 'resume';
            const filename = `ATS_Optimized_${base}.pdf`;

            dlPdfBtn.disabled = true;
            dlPdfBtn.classList.add('is-loading');

            try {
                const form = new FormData();
                form.append('draft', text);
                form.append('filename', base);

                const resp = await fetch('/ats-draft-pdf', { method: 'POST', body: form });
                if (!resp.ok) {
                    throw new Error('PDF generation failed');
                }

                const blob = await resp.blob();
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = filename;
                document.body.appendChild(a);
                a.click();
                a.remove();
                URL.revokeObjectURL(url);
            } catch {
                // Keep UX minimal; user can retry.
                alert('Could not generate PDF. Please try again.');
            } finally {
                dlPdfBtn.disabled = false;
                dlPdfBtn.classList.remove('is-loading');
            }
        });
    }
})();
