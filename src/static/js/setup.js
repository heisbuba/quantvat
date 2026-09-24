(function() {
    const form = document.getElementById('setupForm');
    const inputs = form.querySelectorAll('input[type="text"]');
    const toast = document.getElementById('saveToast');
    let debounceTimer;
    let toastTimer;
    const lastSaved = {
        cg_key: (form.querySelector('[name="cg_key"]').value || '').trim(),
        vtmr_url: (form.querySelector('[name="vtmr_url"]').value || '').trim(),
    };

    function showToast(failed) {
        const label = toast.querySelector('span');
        toast.classList.toggle('error', !!failed);
        label.textContent = failed ? 'Save failed — retry' : 'Auto-saved';
        toast.classList.add('visible');
        clearTimeout(toastTimer);
        toastTimer = setTimeout(() => {
            toast.classList.remove('visible', 'error');
            label.textContent = 'Auto-saved';
        }, failed ? 3000 : 2000);
    }

    function autoSave() {
        const cg = (form.querySelector('[name="cg_key"]').value || '').trim();
        const vtmr = (form.querySelector('[name="vtmr_url"]').value || '').trim();

        // Per-field persistence: save each field independently as it gains
        // content; an empty field is simply not sent, so a stored value can
        // never be wiped by a cleared input.
        const formData = new FormData();
        let changed = false;
        if (cg && cg !== lastSaved.cg_key) { formData.append('cg_key', cg); changed = true; }
        if (vtmr && vtmr !== lastSaved.vtmr_url) { formData.append('vtmr_url', vtmr); changed = true; }
        if (!changed) return;

        formData.append('csrf_token', form.querySelector('[name="csrf_token"]').value);
        formData.append('source', 'setup');
        formData.append('autosave', '1');

        fetch(window.QV.urls.saveConfig, {
            method: 'POST',
            body: formData,
            headers: { 'X-Requested-With': 'XMLHttpRequest' }
        })
        .then(r => r.json())
        .then(data => {
            if (data.status === 'success') {
                if (formData.has('cg_key')) lastSaved.cg_key = cg;
                if (formData.has('vtmr_url')) lastSaved.vtmr_url = vtmr;
                showToast(false);
            } else {
                showToast(true);
            }
        })
        .catch(() => showToast(true));
    }

    inputs.forEach(input => {
        input.addEventListener('blur', () => {
            clearTimeout(debounceTimer);
            autoSave();
        });
        input.addEventListener('input', () => {
            clearTimeout(debounceTimer);
            debounceTimer = setTimeout(autoSave, 1200);
        });
    });
})();