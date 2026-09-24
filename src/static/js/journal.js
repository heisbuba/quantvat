let allTrades = [];
let filteredTrades = [];
// Id-keyed lookup so row buttons never need inline-serialized trade objects
const tradesById = {};
let currentMode = 'normal';
let currentPage = 1;
const itemsPerPage = 15;
// pendingSnapshots = staged File before trade has an id; savedSnapshots = Drive file id once persisted
let pendingSnapshots = { before: null, after: null };
let savedSnapshots = { before: null, after: null };
let sortCol = 'trade_date';
let sortAsc = false;

// Set in Settings > Trading Journal Mode. 'normal' | 'meme' | 'both'.
const journalModePref = ['normal','meme','both'].includes(window.QV.journalModePref) ? window.QV.journalModePref : 'both';
const driveLinked = window.QV.driveLinked;

document.addEventListener('DOMContentLoaded', () => {
    document.getElementById('trade_date').valueAsDate = new Date();

    if (!driveLinked) return; 

    // Sets up mode UI without rendering yet
    if (journalModePref === 'both') {
        const lastMode = localStorage.getItem('journal_last_mode');
        setMode(lastMode === 'meme' ? 'meme' : 'normal', true);
    } else {
        document.getElementById('mode-switcher').style.display = 'none';
        setMode(journalModePref, true);
    }

    showTableLoading();
    loadJournalData();

    // Debounced search — full table rebuild per keystroke was wasteful
    let searchTimer;
    document.getElementById('search-input').addEventListener('input', () => {
        clearTimeout(searchTimer);
        searchTimer = setTimeout(applyFilters, 200);
    });

    // Delegated row actions — one listener for the whole table, set up once.
    document.getElementById('tradeList').addEventListener('click', e => {
        const viewBtn = e.target.closest('[data-view]');
        if (viewBtn) return toggleNotes(viewBtn.dataset.view);
        const editBtn = e.target.closest('[data-edit]');
        if (editBtn) return editTrade(tradesById[editBtn.dataset.edit]);
    });
});

function showTableLoading() {
    const tbody = document.getElementById('tradeList');
    if (tbody) {
        tbody.innerHTML = `<tr><td colspan="15" style="text-align:center; padding:30px; color:var(--text-dim);">Loading your journal from Google Drive…</td></tr>`;
    }
}

async function loadJournalData() {
    try {
        const res = await fetch(window.QV.urls.trades);
        const data = await res.json();

        if (!res.ok || data.status !== 'success') {
            throw new Error(data.message || 'Failed to load journal');
        }

        allTrades = data.trades || [];
        allTrades.forEach(t => tradesById[t.id] = t);

        const banner = document.getElementById('reconnect-banner');
        if (banner) {
            banner.style.display = data.needs_drive_reconnect ? 'flex' : 'none';
        }

        populateTemporalFilters();
        applyFilters();
    } catch (e) {
        console.error('Journal load error:', e);
        const tbody = document.getElementById('tradeList');
        if (tbody) {
            tbody.innerHTML = `<tr><td colspan="15" style="text-align:center; padding:30px; color:var(--danger);">Could not load your journal from Google Drive. <a href="#" onclick="loadJournalData(); return false;" style="color:var(--accent-blue);">Retry</a></td></tr>`;
        }
    }
}

function setMode(mode, skipRender) {
    currentMode = mode;
    document.getElementById('current-mode').value = mode;
    const isMeme = mode === 'meme';

    if (journalModePref === 'both') {
        localStorage.setItem('journal_last_mode', mode);
    }

    document.getElementById('btn-normal').classList.toggle('active', !isMeme);
    document.getElementById('btn-meme').classList.toggle('active', isMeme);

    document.querySelectorAll('.normal-only').forEach(el => el.style.display = isMeme ? 'none' : 'table-cell');
    document.querySelectorAll('div.normal-only').forEach(el => el.style.display = isMeme ? 'none' : 'block');
    document.querySelectorAll('.meme-only').forEach(el => el.style.display = isMeme ? 'block' : 'none');

    document.getElementById('lbl-ticker').innerText = isMeme ? "$Ticker" : "Coin Ticker";
    document.getElementById('th-token-label').innerText = isMeme ? "TICKER" : "TOKEN";
    document.getElementById('th-mc-label').innerText = isMeme ? "ENTRY MC" : "MKT CAP";

    if (!skipRender) applyFilters();
}

function applyFilters() {
    const q = document.getElementById('search-input').value.toLowerCase();
    const w = document.getElementById('week-filter').value;
    const m = document.getElementById('month-filter').value;

    filteredTrades = allTrades.filter(t => {
        const matchMode = t.mode === currentMode;
        const matchQ = t.ticker.toLowerCase().includes(q) || (t.strategy && t.strategy.toLowerCase().includes(q));
        const matchW = w === 'all' || t.week === w;
        const matchM = m === 'all' || t.month === m;
        return matchMode && matchQ && matchW && matchM;
    });

    updateStats(filteredTrades);
    sortData(sortCol, true);
}

function renderTable() {
    const tbody = document.getElementById('tradeList');
    tbody.innerHTML = '';

    if (filteredTrades.length === 0) {
        tbody.innerHTML = `<tr><td colspan="15" style="text-align:center; padding:30px; color:var(--text-dim);">No trades found for ${currentMode.toUpperCase()}.</td></tr>`;
        return;
    }

    const start = (currentPage - 1) * itemsPerPage;
    const pageData = filteredTrades.slice(start, start + itemsPerPage);

    pageData.forEach(trade => {
        const tr = document.createElement('tr');
        tr.dataset.rowId = trade.id;
        const pnlClass = trade.pnl && trade.pnl.includes('-') ? 'pnl-neg' : 'pnl-pos';
        const review = String(trade.rules_followed) === "true" ? '<span style="color:var(--success)">Plan</span>' : '<span style="color:var(--danger)">Mistake</span>';

        const mcVal = currentMode === 'meme' ? (trade.entry_mcap || '-') : (trade.market_cap || '-');
        const priceHtml = currentMode === 'normal' ? `<td>${escapeHtml(trade.entry_price || '-')}</td>` : '';

        tr.innerHTML = `
            <td>${escapeHtml(trade.trade_date)}</td>
            <td style="font-weight:bold; color:var(--accent-blue);">${escapeHtml(trade.ticker)}</td>
            <td>${escapeHtml(trade.strategy || '-')}</td>
            <td>${escapeHtml(mcVal)}</td>
            ${priceHtml}
            <td>${escapeHtml(trade.entry_time || '-')}</td>
            <td style="color:var(--danger)">${escapeHtml(trade.stop_loss || '-')}</td>
            <td style="color:var(--success)">${escapeHtml(trade.take_profit || '-')}</td>
            <td>${escapeHtml(trade.rrr || '-')}</td>
            <td class="${pnlClass}">${escapeHtml(trade.pnl || '0')}</td>
            <td>${escapeHtml(trade.exit_time || '-')}</td>
            <td>${review}</td>
            <td style="font-size:0.75rem;">${escapeHtml((trade.tags || []).join(', '))}</td>
            <td><button class="row-btn" data-view="${escapeHtml(trade.id)}"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg> VIEW</button></td>
            <td><button class="row-btn" data-edit="${escapeHtml(trade.id)}"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg> EDIT</button></td>
        `;
        tbody.appendChild(tr);

        const noteTr = document.createElement('tr');
        noteTr.id = `notes-${trade.id}`;
        noteTr.className = 'notes-row';
        const snapshotsHtml = buildSnapshotLinksHtml(trade);
        noteTr.innerHTML = `<td colspan="15" class="notes-content"><strong>NOTES:</strong><br>${escapeHtml(trade.notes || 'No notes.')}${snapshotsHtml}</td>`;
        tbody.appendChild(noteTr);
    });
    renderPagination();
}

function buildSnapshotLinksHtml(trade) {
    const links = [];
    if (trade.before_image_id) links.push(`<a href="/journal/image/${trade.before_image_id}" target="_blank" rel="noopener" class="snapshot-link">Before</a>`);
    if (trade.after_image_id) links.push(`<a href="/journal/image/${trade.after_image_id}" target="_blank" rel="noopener" class="snapshot-link">After</a>`);
    if (links.length === 0) return '';
    return `<div class="snapshot-links"><strong>SNAPSHOTS:</strong> ${links.join(' &nbsp;|&nbsp; ')}</div>`;
}

function refreshTradeRow(trade) {
    const fIdx = filteredTrades.findIndex(t => t.id === trade.id);
    if (fIdx > -1) filteredTrades[fIdx] = trade;

    const noteTr = document.getElementById(`notes-${trade.id}`);
    if (!noteTr) return;
    const cell = noteTr.querySelector('.notes-content');
    if (cell) {
        cell.innerHTML = `<strong>NOTES:</strong><br>${escapeHtml(trade.notes || 'No notes.')}${buildSnapshotLinksHtml(trade)}`;
    }

    const dataTr = document.querySelector(`tr[data-row-id="${trade.id}"]`);
    if (dataTr) {
        const editBtn = dataTr.querySelector('[data-edit]');
        if (editBtn) editBtn.dataset.edit = trade.id;
    }
}

// Normalizes SL/TP to a distance-from-entry, whether typed as "12%" or an absolute price
function resolveDistance(raw, entry) {
    if (!raw) return 0;
    const isPercent = raw.includes('%');
    const num = parseFloat(raw.replace(/[^0-9.\-]/g, ''));
    if (isNaN(num)) return 0;
    if (isPercent) return Math.abs(num);
    if (!entry || entry <= 0) return 0; // need a reference price to convert an absolute price into a distance
    return Math.abs((entry - num) / entry) * 100;
}

function calculateRRR() {
    const slRaw = document.getElementById('stop_loss').value.trim();
    const tpRaw = document.getElementById('take_profit').value.trim();
    if (!slRaw || !tpRaw) return;

    let risk, reward;

    if (currentMode === 'normal') {
        const entry = parseFloat(document.getElementById('entry_price').value);
        risk = resolveDistance(slRaw, entry);
        reward = resolveDistance(tpRaw, entry);
    } else {
        // Meme mode: SL/TP are percentage moves; TP also accepts "3x" style multiples
        risk = parseFloat(slRaw.replace(/[^0-9.]/g, ''));
        const tpLower = tpRaw.toLowerCase();
        reward = tpLower.includes('x')
            ? (parseFloat(tpLower.replace(/[^0-9.]/g, '')) - 1) * 100
            : parseFloat(tpRaw.replace(/[^0-9.]/g, ''));
    }

    if (risk > 0 && reward > 0) {
        document.getElementById('rrr').value = "1:" + (reward / risk).toFixed(2);
    }
}

function populateTemporalFilters() {
    const weeks = [...new Set(allTrades.map(t => t.week).filter(Boolean))].sort().reverse();
    const months = [...new Set(allTrades.map(t => t.month).filter(Boolean))].sort().reverse();
    const wSel = document.getElementById('week-filter');
    const mSel = document.getElementById('month-filter');
    wSel.length = 1;  // keep "All Weeks"; prevents duplicates on retry
    mSel.length = 1;
    weeks.forEach(w => wSel.add(new Option(w, w)));
    months.forEach(m => mSel.add(new Option(m, m)));
}

function toggleNotes(id) {
    document.querySelectorAll('.notes-row').forEach(r => r.id !== `notes-${id}` && r.classList.remove('open'));
    document.getElementById(`notes-${id}`).classList.toggle('open');
}

function toggleRuleTags() {
    const followed = document.getElementById('rulesToggle').checked;
    document.getElementById('rules_followed').value = followed;
    document.getElementById('tags-positive').classList.toggle('active', followed);
    document.getElementById('tags-negative').classList.toggle('active', !followed);
}

function toggleTag(el) { el.classList.toggle('selected'); }

// Shared so preset chips, custom chips, and reloaded tags all behave identically.
// String-based: the old innerText/innerHTML version forced a style recalc per call.
const ESC_MAP = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
function escapeHtml(str) {
    return String(str).replace(/[&<>"']/g, c => ESC_MAP[c]);
}

function getTagText(chip) {
    const span = chip.querySelector('.tag-text');
    return (span ? span.textContent : chip.textContent).trim();
}

function createTagChip(text, isPositive, selected) {
    const div = document.createElement('div');
    div.className = `tag-chip ${isPositive ? 'pos-tag' : 'neg-tag'}${selected ? ' selected' : ''}`;
    div.dataset.custom = "true";
    div.innerHTML = `<span class="tag-text">${escapeHtml(text)}</span><span class="tag-del" onclick="event.stopPropagation(); removeCustomTag(this);" title="Remove tag">&times;</span>`;
    div.querySelector('.tag-text').onclick = (e) => {
        e.stopPropagation();
        toggleTag(div);
    };
    return div;
}

function removeCustomTag(delBtn) {
    const chip = delBtn.closest('.tag-chip');
    if (chip) chip.remove();
}

function addNewTag(containerId) {
    const val = prompt("New tag:");
    if(!val || !val.trim()) return;
    const grid = document.querySelector(`#${containerId} .tag-grid`);
    const chip = createTagChip(val.trim(), containerId === 'tags-positive', true);
    grid.insertBefore(chip, grid.lastElementChild);
}

function updateStats(trades) {
    const biasEl = document.getElementById('stat-bias');
    if (!trades.length) {
        document.getElementById('stat-winrate').innerText = "0%";
        document.getElementById('stat-pnl').innerText = "0%";
        biasEl.innerText = "Neutral";
        return;
    }

    let wins = 0, totalPnl = 0, best = { val: -999, t: '--' };
    let planCount = 0, mistakeCount = 0;
    let tagFreq = {};

    trades.forEach(t => {
        const v = parseFloat(t.pnl.replace(/[^0-9.-]+/g,"")) || 0;
        if(v > 0) wins++;
        if(t.pnl.includes('%')) totalPnl += v;
        if(v > best.val) best = { val: v, t: t.ticker };

        if (String(t.rules_followed) === "true") planCount++;
        else mistakeCount++;

        (t.tags || []).forEach(tag => { tagFreq[tag] = (tagFreq[tag] || 0) + 1; });
    });

    document.getElementById('stat-winrate').innerText = Math.round((wins/trades.length)*100) + "%";
    const pnlEl = document.getElementById('stat-pnl');
    pnlEl.innerText = (totalPnl>0?"+":"") + totalPnl.toFixed(1) + "%";
    pnlEl.style.color = totalPnl>=0 ? "var(--success)" : "var(--danger)";
    document.getElementById('stat-best').innerText = best.t;

    const topTag = Object.keys(tagFreq).reduce((a, b) => tagFreq[a] > tagFreq[b] ? a : b, "");
    const execBias = planCount >= mistakeCount ? "Disciplined" : "Emotional";
    biasEl.innerText = topTag ? `${execBias} (${topTag})` : execBias;
    biasEl.style.color = planCount >= mistakeCount ? "var(--success)" : "var(--danger)";
}

function editTrade(data) {
    window.scrollTo({ top: 0, behavior: 'smooth' });
    setMode(data.mode || 'normal');
    document.getElementById('trade_id').value = data.id || '';
    document.getElementById('trade_date').value = data.trade_date || '';
    document.getElementById('ticker').value = data.ticker || '';
    document.getElementById('strategy').value = data.strategy || '';
    document.getElementById('pnl').value = data.pnl || '';
    document.getElementById('notes').value = data.notes || '';
    document.getElementById('entry_time').value = data.entry_time || '';
    document.getElementById('exit_time').value = data.exit_time || '';
    // stop_loss/take_profit were previously never restored here — fixed
    document.getElementById('stop_loss').value = data.stop_loss || '';
    document.getElementById('take_profit').value = data.take_profit || '';
    document.getElementById('rrr').value = data.rrr || '';
    if(data.mode === 'meme') {
        document.getElementById('entry_mcap').value = data.entry_mcap || '';
        // Clear normal-mode fields so stale hidden values aren't sent in the payload
        document.getElementById('entry_price').value = '';
        document.getElementById('market_cap').value = '';
    } else {
        document.getElementById('entry_price').value = data.entry_price || '';
        document.getElementById('market_cap').value = data.market_cap || '';
        document.getElementById('entry_mcap').value = '';
    }
    const followed = String(data.rules_followed) === "true";
    document.getElementById('rulesToggle').checked = followed;
    toggleRuleTags();
    document.querySelectorAll('.tag-chip').forEach(c => c.classList.remove('selected'));
    if(data.tags) {
        const grid = document.querySelector(`#${followed ? 'tags-positive' : 'tags-negative'} .tag-grid`);
        data.tags.forEach(t => {
            let found = false;
            grid.querySelectorAll('.tag-chip').forEach(c => { if(getTagText(c) === t) { c.classList.add('selected'); found = true; } });
            if(!found) {
                const chip = createTagChip(t, followed, true);
                grid.insertBefore(chip, grid.lastElementChild);
            }
        });
    }
    document.getElementById('btn-delete').style.display = 'block';
    const btn = document.getElementById('btn-submit');
    btn.innerText = "UPDATE"; btn.style.background = "var(--accent-orange)"; btn.style.color = "#000";

    resetSnapshots();
    if (data.before_image_id) {
        savedSnapshots.before = data.before_image_id;
        document.getElementById('preview-before').innerHTML = `<img src="/journal/image/${data.before_image_id}">`;
        document.getElementById('remove-before').style.display = 'block';
    }
    if (data.after_image_id) {
        savedSnapshots.after = data.after_image_id;
        document.getElementById('preview-after').innerHTML = `<img src="/journal/image/${data.after_image_id}">`;
        document.getElementById('remove-after').style.display = 'block';
    }
}

function handleSnapshotSelect(slot, input) {
    const file = input.files[0];
    if (!file) return;
    if (!['image/png', 'image/jpeg', 'image/webp'].includes(file.type)) {
        alert('Only PNG, JPEG, or WEBP images are supported.');
        input.value = '';
        return;
    }
    if (file.size > 10 * 1024 * 1024) {
        alert('Image too large (10MB max).');
        input.value = '';
        return;
    }

    pendingSnapshots[slot] = file;
    document.getElementById(`preview-${slot}`).innerHTML = `<img src="${URL.createObjectURL(file)}">`;
    document.getElementById(`remove-${slot}`).style.display = 'block';

    // Editing an existing trade — upload immediately instead of waiting for SAVE
    const tradeId = document.getElementById('trade_id').value;
    if (tradeId) uploadSnapshot(slot, tradeId);
}

async function uploadSnapshot(slot, tradeId) {
    const file = pendingSnapshots[slot];
    if (!file) return;
    const preview = document.getElementById(`preview-${slot}`);
    preview.classList.add('uploading');

    const fd = new FormData();
    fd.append('id', tradeId);
    fd.append('mode', currentMode);
    fd.append('slot', slot);
    fd.append('file', file);

    try {
        const res = await fetch(window.QV.urls.uploadImage, {
            method: 'POST',
            headers: { 'X-CSRFToken': window.QV.csrf },
            body: fd
        });
        const data = await res.json();
        preview.classList.remove('uploading');
        if (data.status === 'success') {
            savedSnapshots[slot] = data.image_id;
            pendingSnapshots[slot] = null;
            preview.innerHTML = `<img src="${data.url}">`;
            const idx = allTrades.findIndex(t => t.id === data.trade.id);
            if (idx > -1) allTrades[idx] = data.trade;
            tradesById[data.trade.id] = data.trade;
            refreshTradeRow(data.trade);
        } else {
            alert(data.message || 'Snapshot upload failed.');
        }
    } catch (e) {
        preview.classList.remove('uploading');
        alert('Snapshot upload failed.');
    }
}

async function removeSnapshot(slot) {
    const tradeId = document.getElementById('trade_id').value;
    const preview = document.getElementById(`preview-${slot}`);

    if (savedSnapshots[slot] && tradeId) {
        if (!confirm('Remove this snapshot from Drive? This cannot be undone.')) return;
        try {
            const res = await fetch(window.QV.urls.deleteImage, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': window.QV.csrf },
                body: JSON.stringify({ id: tradeId, mode: currentMode, slot })
            });
            const data = await res.json();
            if (data.status !== 'success') { alert(data.message || 'Remove failed.'); return; }
            const idx = allTrades.findIndex(t => t.id === data.trade.id);
            if (idx > -1) allTrades[idx] = data.trade;
            tradesById[data.trade.id] = data.trade;
            refreshTradeRow(data.trade);
        } catch (e) {
            alert('Remove failed.');
            return;
        }
    }

    savedSnapshots[slot] = null;
    pendingSnapshots[slot] = null;
    document.getElementById(`file-${slot}`).value = '';
    preview.innerHTML = `<span class="snapshot-placeholder">+ ${slot === 'before' ? 'Before' : 'After'}</span>`;
    document.getElementById(`remove-${slot}`).style.display = 'none';
}

function resetSnapshots() {
    pendingSnapshots = { before: null, after: null };
    savedSnapshots = { before: null, after: null };
    ['before', 'after'].forEach(slot => {
        document.getElementById(`file-${slot}`).value = '';
        document.getElementById(`preview-${slot}`).innerHTML = `<span class="snapshot-placeholder">+ ${slot === 'before' ? 'Before' : 'After'}</span>`;
        document.getElementById(`remove-${slot}`).style.display = 'none';
    });
}

async function saveToDrive() {
    const btn = document.getElementById('btn-submit');
    const ticker = document.getElementById('ticker').value.trim();
    const normalMC = document.getElementById('market_cap').value.trim();
    const memeMC = document.getElementById('entry_mcap').value.trim();

    const isNormalValid = (currentMode === 'normal' && normalMC !== "");
    const isMemeValid = (currentMode === 'meme' && memeMC !== "");

    if (!ticker || (!isNormalValid && !isMemeValid)) {
        alert(`Action Denied: Ticker and ${currentMode === 'meme' ? 'Entry MC' : 'Market Cap'} are required to initialize a log.`);
        return;
    }

    const followed = document.getElementById('rulesToggle').checked;
    const tags = Array.from(document.querySelectorAll(`#${followed ? 'tags-positive' : 'tags-negative'} .tag-chip.selected`)).map(el => getTagText(el));

    btn.disabled = true;
    btn.innerHTML = `<span>SYNCING...</span>`;

    const payload = Object.fromEntries(new FormData(document.getElementById('journalForm')).entries());
    payload.tags = tags;
    payload.rules_followed = followed;
    // The form has no before/after image fields, so without this an edit-save
    // on an existing trade would overwrite the Drive record and drop any
    // already-attached snapshot ids (see save_trade_v2's full-record replace).
    payload.before_image_id = savedSnapshots.before || null;
    payload.after_image_id = savedSnapshots.after || null;

    try {
        const res = await fetch(window.QV.urls.save, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': window.QV.csrf
            },
            body: JSON.stringify(payload)
        });
        const data = await res.json();
        if(data.status === 'success') {
            const idx = allTrades.findIndex(t => t.id === data.trade.id);
            if (idx > -1) allTrades[idx] = data.trade; else allTrades.unshift(data.trade);
            tradesById[data.trade.id] = data.trade;

            // Trade now has an id — flush any staged snapshots before resetForm() clears previews
            if (pendingSnapshots.before) await uploadSnapshot('before', data.trade.id);
            if (pendingSnapshots.after) await uploadSnapshot('after', data.trade.id);

            applyFilters();
            resetForm();
            btn.innerText = "SAVED!";
            setTimeout(() => {
                btn.disabled = false;
                btn.innerHTML = `<svg fill="currentColor" viewBox="0 0 24 24"><path d="M17 3H5c-1.11 0-2 .9-2 2v14c0 1.1.89 2 2 2h14c1.1 0 2-.9 2-2V7l-4-4zm-5 16c-1.66 0-3-1.34-3-3s1.34-3 3-3 3 1.34 3 3-1.34 3-3 3zm3-10H5V5h10v4z"/></svg> SAVE`;
                btn.style.background = "var(--accent-blue)";
                btn.style.color = "#000";
            }, 1500);
        }
    } catch(e) {
        alert("Sync Failed");
        btn.disabled = false;
        btn.innerHTML = `<svg fill="currentColor" viewBox="0 0 24 24"><path d="M17 3H5c-1.11 0-2 .9-2 2v14c0 1.1.89 2 2 2h14c1.1 0 2-.9 2-2V7l-4-4zm-5 16c-1.66 0-3-1.34-3-3s1.34-3 3-3 3 1.34 3 3-1.34 3-3 3zm3-10H5V5h10v4z"/></svg> SAVE`;
    }
}

async function deleteTrade() {
    const id = document.getElementById('trade_id').value;

    if (!id) {
        alert("No trade selected. Please click 'Edit' on a trade first.");
        return;
    }

    if (!confirm("Permanently delete this trade from Google Drive? This cannot be undone.")) return;

    const delBtn = document.getElementById('btn-delete');
    const originalText = delBtn.innerHTML;
    delBtn.disabled = true;
    delBtn.innerText = "SYNCING...";

    try {
        const response = await fetch(`/journal/delete/${id}?mode=${currentMode}`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': window.QV.csrf
            }
        });

        const data = await response.json();

        if (response.ok && data.status === 'success') {
            allTrades = allTrades.filter(t => String(t.id) !== String(id));
            delete tradesById[id];

            applyFilters();
            resetForm();
        } else {
            alert("Server Error: " + (data.message || "Deletion failed."));
        }
    } catch (error) {
        console.error("Critical Network Failure:", error);
        alert("Connection Error: The server rejected the request. Please check your credentials or refresh the page.");
    } finally {
        delBtn.disabled = false;
        delBtn.innerHTML = originalText;
    }
}

function resetForm() {
    document.getElementById('journalForm').reset();
    document.getElementById('trade_id').value = "";
    document.getElementById('trade_date').valueAsDate = new Date();
    document.getElementById('btn-delete').style.display = 'none';
    document.querySelectorAll('.tag-chip').forEach(c => c.classList.remove('selected'));
    document.getElementById('rulesToggle').checked = true;
    toggleRuleTags();
    resetSnapshots();
    const btn = document.getElementById('btn-submit');
    btn.innerHTML = `<svg fill="currentColor" viewBox="0 0 24 24"><path d="M17 3H5c-1.11 0-2 .9-2 2v14c0 1.1.89 2 2 2h14c1.1 0 2-.9 2-2V7l-4-4zm-5 16c-1.66 0-3-1.34-3-3s1.34-3 3-3 3 1.34 3 3-1.34 3-3 3zm3-10H5V5h10v4z"/></svg> SAVE`;
    btn.style.background = "var(--accent-blue)";
    btn.style.color = "#000";
}

function exportCSV() {
    if (!filteredTrades.length) return;
    const headers = ["Date", "Ticker", "Strategy", "MC", "Price", "Entry Time", "SL", "TP", "RRR", "PnL", "Exit Time", "Review", "Tags", "Notes"];
    const rows = filteredTrades.map(t => [t.trade_date, t.ticker, t.strategy, t.entry_mcap || t.market_cap, t.entry_price, t.entry_time, t.stop_loss, t.take_profit, t.rrr, t.pnl, t.exit_time, t.rules_followed ? "Plan" : "Mistake", `"${(t.tags||[]).join(', ')}"`, `"${(t.notes || '').replace(/"/g, '""')}"`]);
    const link = document.createElement("a");
    link.href = encodeURI("data:text/csv;charset=utf-8," + headers.join(",") + "\n" + rows.map(e => e.join(",")).join("\n"));
    link.download = `trading_journal_${currentMode}.csv`;
    link.click();
}

// Handles escaping for notes/tags to prevent CSV breakage.
function getCSVString(trades) {
    if (!trades || trades.length === 0) return "No trades data available.";

    const headers = ["Date", "Ticker", "Strat", "MC", "Price", "RRR", "PnL", "Review", "Notes"];

    const rows = trades.map(t => {
        const safeNotes = (t.notes || '').replace(/"/g, '""').replace(/\n/g, ' ');
        const safeStrat = (t.strategy || '').replace(/"/g, '""');
        const review = t.rules_followed ? "Plan" : "Mistake";
        const mc = currentMode === 'meme' ? (t.entry_mcap || '') : (t.market_cap || '');

        return [
            t.trade_date,
            t.ticker,
            `"${safeStrat}"`,
            mc,
            t.entry_price,
            t.rrr,
            t.pnl,
            review,
            `"${safeNotes}"`
        ].join(",");
    });

    return headers.join(",") + "\n" + rows.join("\n");
}

function sortData(col, maintain) {
    if (!maintain) { sortAsc = sortCol === col ? !sortAsc : true; sortCol = col; }
    filteredTrades.sort((a, b) => {
        let valA = a[col] || '', valB = b[col] || '';
        if (col === 'pnl') { valA = parseFloat(valA.replace(/[^0-9.-]+/g,"")) || 0; valB = parseFloat(valB.replace(/[^0-9.-]+/g,"")) || 0; }
        return valA < valB ? (sortAsc ? -1 : 1) : (valA > valB ? (sortAsc ? 1 : -1) : 0);
    });
    currentPage = 1; renderTable();
}

function renderPagination() {
    const div = document.getElementById('pagination'); div.innerHTML = '';
    const total = Math.ceil(filteredTrades.length / itemsPerPage);
    if(total <= 1) return;
    for(let i=1; i<=total; i++) {
        const b = document.createElement('button');
        b.className = `page-btn ${i===currentPage?'active':''}`;
        b.innerText = i; b.onclick=()=>{currentPage=i; renderTable()};
        div.appendChild(b);
    }
}

const hasAiKey = window.QV.hasAiKey;

// Lazy-loaded on first AI modal open (was render-blocking in <head> on every visit before)
let markdownLibsPromise = null;
function loadMarkdownLibs() {
    if (markdownLibsPromise) return markdownLibsPromise;
    markdownLibsPromise = new Promise((resolve, reject) => {
        let loaded = 0;
        const done = () => { if (++loaded === 2) resolve(); };
        const marked = document.createElement('script');
        marked.src = window.QV.urls.marked;
        marked.onload = done;
        marked.onerror = reject;
        const purify = document.createElement('script');
        purify.src = window.QV.urls.purify;
        purify.onload = done;
        purify.onerror = reject;
        document.head.appendChild(marked);
        document.head.appendChild(purify);
    });
    return markdownLibsPromise;
}

async function openAiAuditor() {
    if (!hasAiKey) {
        document.getElementById('aiRedirectModal').style.display = 'flex';
        return;
    }

    const modal = document.getElementById('aiModal');
    modal.style.display = 'flex';
    modal.classList.add('is-active');

    try {
        await loadMarkdownLibs();
    } catch (e) {
        console.error('Failed to load markdown libs:', e);
    }

    initAiAudit();
}

function appendBubble(text, type) {
    const chat = document.getElementById('aiChat');
    const row = document.createElement('div');
    row.className = `msg-row ${type}`;

    // Sanitize after markdown parse — trade notes are user input, so raw HTML can't be trusted
    const formattedText = type === 'ai'
        ? DOMPurify.sanitize(marked.parse(text))
        : escapeHtml(text);

    row.innerHTML = `<div class="chat-bubble ${type}">${formattedText}</div>`;
    chat.appendChild(row);
    chat.scrollTop = chat.scrollHeight;
}

async function initAiAudit() {
    const chat = document.getElementById('aiChat');
    chat.innerHTML = '';
    appendBubble("Analyzing your filtered ledger...", "ai");

    // Generate CSV on client-side to guarantee WYSIWYG context
    const csvPayload = getCSVString(filteredTrades);

    try {
        const res = await fetch("/api/ai/init_audit", {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': window.QV.csrf
            },
            body: JSON.stringify({ csv_context: csvPayload })
        });

        const data = await res.json();

        if(res.ok && data.status === 'success') {
            chat.innerHTML = '';
            appendBubble(data.response, "ai");
        } else {
            throw new Error(data.message || "Server rejected request");
        }
    } catch (e) {
        console.error(e);
        chat.innerHTML = '';
        appendBubble("Error: Connection Failed. " + (e.message === "Server rejected request" ? "Check API Key." : "Retrying..."), "ai");
    }
}

async function sendAiQuery() {
    const input = document.getElementById('aiInput');
    const query = input.value.trim();
    if (!query) return;

    appendBubble(query, "user");
    input.value = '';

    try {
        const res = await fetch("/api/ai/chat", {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': window.QV.csrf
            },
            body: JSON.stringify({ prompt: query })
        });
        const data = await res.json();

        if(res.ok) {
            appendBubble(data.response, "ai");
        } else {
            appendBubble("System Error: " + (data.message || "Could not reach brain."), "ai");
        }
    } catch (e) {
        appendBubble("Error: Network interruption.", "ai");
    }
}

function closeAiModal() {
    document.getElementById('aiModal').style.display = 'none';
    document.getElementById('aiModal').classList.remove('is-active');
}