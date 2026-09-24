const input = document.getElementById('token-input');

const ESC_MAP = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
function escapeHtml(str) {
    return String(str).replace(/[&<>"']/g, c => ESC_MAP[c]);
}

let diveRunId = 0;

    const results = document.getElementById('search-results');
    const matrix = document.getElementById('matrix-content');
    const skeleton = document.getElementById('skeleton-container');
    let searchTimeout = null;
    let currentCoinData = null;

    // Search Logic
    input.addEventListener('input', (e) => {
        clearTimeout(searchTimeout);
        const q = e.target.value.trim();
        
        if(q.length < 1) { 
            results.style.display = 'none'; 
            results.innerHTML = '';
            return; 
        }

        searchTimeout = setTimeout(async () => {
            try {
                const res = await fetch(`/api/search-tickers?q=${encodeURIComponent(q)}`);
                const data = await res.json();
                if(data && data.length > 0) {
                    results.innerHTML = data.map(t => `
                        <div class="res-item" data-id="${escapeHtml(t.id)}" data-symbol="${escapeHtml(t.symbol)}">
                            <img src="${escapeHtml(t.thumb)}" alt="${escapeHtml(t.symbol)}" loading="lazy">
                            <div style="flex-grow:1; display:flex; align-items:center;">
                                <span style="font-weight:bold; color:var(--text-main);">${escapeHtml(t.symbol.toUpperCase())}</span>
                                <span class="res-name">${escapeHtml(t.name)}</span>
                            </div>
                        </div>
                    `).join('');
                    results.querySelectorAll('.res-item').forEach(el => {
                        el.addEventListener('click', () => dive(el.dataset.id, el.dataset.symbol));
                    });
                    results.style.display = 'block';
                } else { 
                    results.innerHTML = '<div class="res-item" style="cursor:default; color:var(--text-dim);">No results found</div>';
                    results.style.display = 'block';
                }
            } catch (e) { 
                console.error(e); 
                results.style.display = 'none';
            }
        }, 600);
    });

    // Hide dropdown on outside click
    document.addEventListener('click', (e) => {
        if (!e.target.closest('.search-container')) {
            results.style.display = 'none';
        }
    });

    // Main Data Fetch (DIVE)
    async function dive(id, symbol) {
    const thisRun = ++diveRunId;
        results.style.display = 'none';
        input.value = symbol.toUpperCase();
        matrix.classList.remove('visible');
        skeleton.style.display = 'block';
        resetMeanReversionUI();

        try {
            const res = await fetch(`/api/dive/${encodeURIComponent(id)}`);
            const d = await res.json();
        if (thisRun !== diveRunId) return;

            if(d.status === "success") {
                currentCoinData = { id: id, ...d };
                updateWatchlistUI(d.is_watched);
                
                // TOKEN DETAILS
                document.getElementById('v-price').innerText = d.vitals.price;
                document.getElementById('v-mcap').innerText = d.vitals.mcap;
                document.getElementById('v-vol').innerText = d.vitals.vol24h;
                document.getElementById('v-supply').innerText = d.supply.total;
                
                // QUANT RATIOS
                document.getElementById('v-vtmr').innerText = d.ratios.vtmr;
                document.getElementById('v-vtpc').innerText = d.ratios.vtpc;
                document.getElementById('v-liquidity').innerText = d.ratios.liquidity;
                const depth = d.depth || {};
                document.getElementById('v-depth-up').innerText = depth.up || '--';
                document.getElementById('v-depth-down').innerText = depth.down || '--';

                const hist = d.historical || {};
                document.getElementById('v-avg-7d-vol').innerText = hist.avg_7d_daily_vol || '--';
                document.getElementById('v-avg-30d-vol').innerText = hist.avg_30d_vol || '--';
                document.getElementById('v-vwap-30d').innerText = hist.vwap_30d || '--';
                
                // PRICE CHANGE
                updateVel('v-1h', d.velocity.h1);
                updateVel('v-24h', d.velocity.h24);
                updateVel('v-7d', d.velocity.d7);
                updateVel('v-30d', d.velocity.m1);
                updateVel('v-yearly', d.velocity.y1 || d.velocity.yearly);

                // LINKS
                if(d.links) {
                    document.getElementById('link-cg').href = d.links.cg;
                    document.getElementById('link-tv').href = d.links.tv;
                }

                skeleton.style.display = 'none';
                matrix.classList.add('visible');
            } else {
                alert("Error: " + (d.message || "Failed to load data"));
                skeleton.style.display = 'none';
            }
        } catch (err) {
            console.error(err);
            alert("Network error. Please try again.");
            skeleton.style.display = 'none';
        }
    }

    function updateVel(id, val) {
        const el = document.getElementById(id);
        if(!el) return;
        el.innerText = val || "--";
        
        if (val && val.toString().startsWith('-')) {
            el.className = 'mono stats-val neg';
        } else if (val && val !== "--") {
            el.className = 'mono stats-val pos';
        } else {
            el.className = 'mono stats-val';
        }
    }

    // URL param handler
    document.addEventListener('DOMContentLoaded', () => {
        const params = new URLSearchParams(window.location.search);
        const ticker = params.get('ticker');
        
        if (ticker) {
            input.value = ticker.toUpperCase();
            input.dispatchEvent(new Event('input', { bubbles: true }));
            input.focus();
        }
    });

    function updateWatchlistUI(isWatched) {
        const btn = document.getElementById('btn-watchlist');
        const text = document.getElementById('watchlist-text');
        btn.setAttribute('data-is-watched', isWatched);

        if (isWatched) {
            btn.classList.add('active-watch');
            text.innerText = "Watched";
        } else {
            btn.classList.remove('active-watch');
            text.innerText = "Watchlist";
        }
    }

    async function toggleWatchlist() {
        if (!currentCoinData) return;
        
        const btn = document.getElementById('btn-watchlist');
        const isCurrentlyWatched = btn.getAttribute('data-is-watched') === 'true';
        const action = isCurrentlyWatched ? 'remove' : 'add';

        updateWatchlistUI(!isCurrentlyWatched);

        try {
            const res = await fetch('/api/watchlist/toggle', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': window.QV.csrf
                },
                body: JSON.stringify({
                    coin_id: currentCoinData.id,
                    symbol: currentCoinData.vitals.symbol,
                    name: currentCoinData.vitals.name,
                    price: currentCoinData.vitals.price,
                    vtmr: currentCoinData.ratios.vtmr,
                    mcap: currentCoinData.vitals.mcap,
                    chg_1y: currentCoinData.velocity.y1 || currentCoinData.velocity.yearly,
                    chg_24h: currentCoinData.velocity.h24,
                    chg_7d: currentCoinData.velocity.d7,
                    chg_30d: currentCoinData.velocity.m1,
                    action: action
                })
            });
            
            const result = await res.json();
            
            if (result.status === "success") {
                updateWatchlistUI(result.is_watched);
            } else {
                updateWatchlistUI(isCurrentlyWatched);
                console.error("Watchlist sync error:", result.message);
            }
        } catch (err) {
            updateWatchlistUI(isCurrentlyWatched);
            console.error("Watchlist toggle failed:", err);
        }
    }

    // --- Mean Reversion ---

    function resetMeanReversionUI() {
        const btn = document.getElementById('btn-calc-mr');
        const container = document.getElementById('mr-results-container');
        
        if (btn) {
            btn.disabled = false;
            btn.innerText = "CALCULATE";
        }
        if (container) {
            container.style.display = 'none';
        }
        
        const el1d = document.getElementById('v-mr-1d');
        const el4h = document.getElementById('v-mr-4h');
        const el1h = document.getElementById('v-mr-1h');

        if (el1d) el1d.innerText = '--';
        if (el4h) el4h.innerText = '--';
        if (el1h) el1h.innerText = '--';
    }

    async function calculateMeanReversion() {
        if (!currentCoinData || !currentCoinData.id) return;

        const btn = document.getElementById('btn-calc-mr');
        const container = document.getElementById('mr-results-container');

        btn.disabled = true;
        btn.innerText = "CALCULATING...";

        try {
            const res = await fetch('/api/mean-reversion', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': window.QV.csrf
                },
                body: JSON.stringify({ coin_id: currentCoinData.id })
            });

            if (!res.ok) {
                let errMsg = `Server error (HTTP ${res.status}) calculating mean reversion.`;
                try {
                    const errData = await res.json();
                    errMsg = errData.message || errMsg;
                } catch (_) {
  
                }
                alert("Error: " + errMsg);
                btn.disabled = false;
                btn.innerText = "CALCULATE";
                return;
            }

            const data = await res.json();

            if (data.status === 'success') {
                document.getElementById('v-mr-1d').innerText = data.line1d || '--';
                document.getElementById('v-mr-4h').innerText = data.line4h || '--';
                document.getElementById('v-mr-1h').innerText = data.line1h || '--';

                container.style.display = 'block';
                btn.innerText = "CALCULATED";
            } else {
                alert("Error: " + (data.message || "Failed to calculate mean reversion metrics"));
                btn.disabled = false;
                btn.innerText = "CALCULATE";
            }
        } catch (err) {
            console.error(err);
            alert("Network error processing mean reversion calculation.");
            btn.disabled = false;
            btn.innerText = "CALCULATE";
        }
    }