// Global State Management
    let busy = false;
    let lastIdx = 0;
    let logTimeout = null;
    let pollTimeout = null;
    let activeRunId = 0;     
    let serverRunId = null;   
    let activeButton = null;

    function setActiveButtonDisabled(disabled) {
        document.querySelectorAll('.trigger-btn').forEach(b => {
            b.disabled = disabled;
            b.style.opacity = disabled ? '0.5' : '1';
            b.style.pointerEvents = disabled ? 'none' : 'auto';
        });
    }

    function trigger(url, btn) {
        if (busy) return;
        busy = true;
        activeButton = btn;
        setActiveButtonDisabled(true);

        activeRunId++;
        const currentRunId = activeRunId;

        if (logTimeout) clearTimeout(logTimeout);
        if (pollTimeout) clearTimeout(pollTimeout);

        const term = document.getElementById('term');

        fetch(url)
            .then(r => r.json())
            .then(data => {
                if (currentRunId !== activeRunId) return;

                if (data.status !== 'started') {
                    busy = false;
                    setActiveButtonDisabled(false);
                    activeButton = null;
                    term.innerHTML += '<div class="log-line error">> ' +
                        (data.message || 'Could not start task.') + '</div>';
                    term.scrollTop = term.scrollHeight;
                    return;
                }

                // Only clear the terminal
                term.innerHTML = '<div class="log-line">> Starting task...</div>';
                lastIdx = 0;
                serverRunId = data.run_id || null;
                document.getElementById('bar').style.width = '5%';
                document.getElementById('percent').innerText = '5%';

                poll(currentRunId, serverRunId);
                logs(currentRunId, serverRunId);
            })
            .catch(() => {
                if (currentRunId !== activeRunId) return;
                busy = false;
                setActiveButtonDisabled(false);
                activeButton = null;
                term.innerHTML += '<div class="log-line error">> Failed to initiate task.</div>';
            });
    }

    function handleForeignRun(term) {
        busy = false;
        setActiveButtonDisabled(false);
        activeButton = null;
        term.innerHTML += '<div class="log-line error">> Another session started a new task for this account — no longer tracking this run here.</div>';
        term.scrollTop = term.scrollHeight;
    }

    function poll(runId, runToken) {
        if (runId !== activeRunId) return;
        fetch(window.QV.urls.progress + '?run=' + encodeURIComponent(runToken || ''))
            .then(r => r.json())
            .then(data => {
                if (runId !== activeRunId) return;
                if (data.stale) {
                    handleForeignRun(document.getElementById('term'));
                    return;
                }
                document.getElementById('bar').style.width = data.percent + '%';
                document.getElementById('percent').innerText = data.percent + '%';

                if (data.percent < 100 && data.status !== 'error') {
                    pollTimeout = setTimeout(() => poll(runId, runToken), 800);
                } else {
                    busy = false;
                    setActiveButtonDisabled(false);
                    activeButton = null;
                }
            })
            .catch(() => {
                if (runId !== activeRunId) return;
                busy = false;
                setActiveButtonDisabled(false);
                activeButton = null;
            });
    }

    function logs(runId, runToken) {
        if (runId !== activeRunId) return;
        fetch(window.QV.urls.logsChunk + '?last=' + lastIdx + '&run=' + encodeURIComponent(runToken || ''))
            .then(r => r.json())
            .then(data => {
                if (runId !== activeRunId) return;
                const term = document.getElementById('term');
                if (data.stale) {
                    handleForeignRun(term);
                    return;
                }
                if (data.logs.length) {
                    lastIdx = data.last_index;
                    data.logs.forEach(log => {
                        // Structured {ts, level, msg}; string fallback survives version skew
                        const entry = (typeof log === 'object' && log !== null)
                            ? log
                            : { ts: null, level: 'info', msg: log };
                        const div = document.createElement('div');
                        let cls = 'log-line';
                        if (entry.level === 'error') cls += ' error';
                        else if (entry.level === 'warning') cls += ' warn';
                        else if (entry.msg.includes('Found')) cls += ' highlight';
                        const t = entry.ts ? new Date(entry.ts * 1000).toLocaleTimeString() : '';
                        div.className = cls;
                        div.innerText = (t ? '[' + t + '] ' : '') + '> ' + entry.msg;
                        term.appendChild(div);
                    });
                    term.scrollTop = term.scrollHeight;
                }

                if (busy || data.logs.length > 0) {
                    logTimeout = setTimeout(() => logs(runId, runToken), 1000);
                }
            });
    }

    function saveFilters() {
        const saveBtn = document.querySelector('.btn-save');
        saveBtn.disabled = true;
        saveBtn.innerText = 'SAVING...';

        const pagesInput = document.getElementById('scan_pages');
        const pagesClamped = Math.min(8, Math.max(1, parseInt(pagesInput.value, 10) || 6));
        pagesInput.value = pagesClamped;

        const filterData = {
            min_vtmr: document.getElementById('min_vtmr').value,
            max_vtmr: document.getElementById('max_vtmr').value,
            min_largecap_vtmr: document.getElementById('min_largecap_vtmr').value,
            scan_pages: pagesClamped,
            min_f_vtmr: document.getElementById('min_f_vtmr').value,
            max_f_vtmr: document.getElementById('max_f_vtmr').value,
            min_s_mc: document.getElementById('min_s_mc').value.trim(),
            max_s_mc: document.getElementById('max_s_mc').value.trim(),
            min_f_mc: document.getElementById('min_f_mc').value.trim(),
            max_f_mc: document.getElementById('max_f_mc').value.trim()
        };

        fetch('/save-filters', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': window.QV.csrf
                },
                body: JSON.stringify(filterData)
            })
            .then(r => r.json())
            .then(data => {
                if (data.status === 'success') {
                    toggleModal('filterModal');
                    const term = document.getElementById('term');
                    term.innerHTML += '<div class="log-line highlight">> Custom filters applied and saved.</div>';
                    term.scrollTop = term.scrollHeight;
                }
            })
            .finally(() => {
                saveBtn.disabled = false;
                saveBtn.innerText = 'APPLY';
            });
    }

    function resetFilters(modalId) {
        if (!confirm("Revert to system defaults?")) return;

        fetch('/reset-filters', {
                method: 'POST',
                headers: { 'X-CSRFToken': window.QV.csrf }
            })
            .then(r => r.json())
            .then(data => {
                if (data.status === 'success') {
                    document.getElementById('min_vtmr').value = '0.5';
                    document.getElementById('max_vtmr').value = '199.0';
                    document.getElementById('min_largecap_vtmr').value = '0.5';
                    document.getElementById('scan_pages').value = '6';
                    document.getElementById('min_f_vtmr').value = '0.5';
                    document.getElementById('max_f_vtmr').value = '399.0';
                    document.getElementById('min_s_mc').value = '';
                    document.getElementById('max_s_mc').value = '';
                    document.getElementById('min_f_mc').value = '';
                    document.getElementById('max_f_mc').value = '';

                    toggleModal(modalId || 'filterModal');

                    const term = document.getElementById('term');
                    term.innerHTML += '<div class="log-line highlight">> Filters reset to defaults.</div>';
                    term.scrollTop = term.scrollHeight;
                }
            });
    }

    function toggleModal(id) {
        const modal = document.getElementById(id);
        if (modal) modal.classList.toggle('is-active');
    }

    function handleOverlayClick(e, id) {
        if (e.target.id === id) toggleModal(id);
    }