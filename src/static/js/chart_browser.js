(() => {
    const CB = window.CB;
    const $ = (id) => document.getElementById(id);

    const els = {
        picker: $('cb-picker'),
        tabRoot: $('cb-tab-root'),
        tabShared: $('cb-tab-shared'),
        backViewer: $('cb-back-viewer'),
        up: $('cb-up'),
        pathName: $('cb-path-name'),
        openHere: $('cb-open-here'),
        folderList: $('cb-folder-list'),
        pickerStatus: $('cb-picker-status'),
        viewer: $('cb-viewer'),
        folderTitle: $('cb-folder-title'),
        sort: $('cb-sort'),
        changeFolder: $('cb-change-folder'),
        stage: $('cb-stage'),
        slide: $('cb-slide'),
        img: $('cb-img'),
        loading: $('cb-loading'),
        error: $('cb-error'),
        errorText: $('cb-error-text'),
        retry: $('cb-retry'),
        empty: $('cb-empty'),
        prev: $('cb-prev'),
        next: $('cb-next'),
        jump: $('cb-jump'),
        total: $('cb-total'),
        filename: $('cb-filename'),
        meta: $('cb-meta'),
        play: $('cb-play'),
        zoom: $('cb-zoom'),
        fullscreen: $('cb-fullscreen'),
        download: $('cb-download'),
        driveLink: $('cb-drive-link'),
        preload: $('cb-preload'),
        preloadLabel: $('cb-preload-label'),
        refresh: $('cb-refresh'),
        clear: $('cb-clear'),
        toast: $('cb-toast'),
    };

    const SLIDESHOW_MS = 4000;
    const BLOB_CACHE_MAX = 30;
    const SLIDESHOW_WAIT_MAX_MS = 60000;

    // Prefetch window adapts to the connection: on 2G/3G/Save-Data we keep it
    // small and sequential so the image being viewed gets the whole pipe.
    function netProfile() {
        const c = navigator.connection || {};
        const slow = c.saveData || /(^|-)2g$|3g/.test(c.effectiveType || '');
        return slow ? { batch: 3, concurrency: 1 } : { batch: 8, concurrency: 2 };
    }
    const MAX_SCALE = 5;
    const ZOOM_TAP_SCALE = 2.5;
    const SWIPE_MIN_PX = 60;
    const ANIM_MS = 200;

    const state = {
        tab: 'root',
        pickerFolder: null,
        folder: null,
        images: [],
        cached: new Set(),
        index: 0,
        sort: 'oldest',
        scale: 1,
        tx: 0,
        ty: 0,
        playing: false,
        timer: null,
        animating: false,
        pickSeq: 0,
        listSeq: 0,
        loadSeq: 0,
        preloadSeq: 0,
        preloading: false,
        autoRefreshed: false,
        blobCache: new Map(),
        inflight: new Map(),
        prefetchRun: 0,
        fullFor: null,
        fullUrl: null,
        toastTimer: null,
        idleTimer: null,
        revalidating: null,
    };

    const imageUrl = (id, extra) => {
        const item = state.images.find((i) => i.id === id);
        const v = item && item.modified ? `v=${encodeURIComponent(item.modified)}` : '';
        const qs = [v, extra].filter(Boolean).join('&');
        return CB.urls.image.replace('FILE_ID', encodeURIComponent(id)) + (qs ? `?${qs}` : '');
    };
    const current = () => state.images[state.index] || null;

    class ApiError extends Error {
        constructor(message, needsConnect) {
            super(message);
            this.needsConnect = !!needsConnect;
        }
    }

    async function api(url, options) {
        let res;
        try {
            res = await fetch(url, options);
        } catch (e) {
            throw new ApiError('Network error. Check your connection and try again.');
        }
        let data = null;
        try {
            data = await res.json();
        } catch (e) {
            data = null;
        }
        if (!res.ok || !data || data.status !== 'success') {
            const message = (data && data.message) || `Request failed (${res.status})`;
            throw new ApiError(message, data && data.needs_connect);
        }
        return data;
    }

    function make(tag, className, text) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined) node.textContent = text;
        return node;
    }

    function formatSize(bytes) {
        if (!bytes) return '';
        if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
        return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
    }

    function formatDate(iso) {
        if (!iso) return '';
        const d = new Date(iso);
        if (isNaN(d)) return '';
        return d.toLocaleString(undefined, { year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
    }

    function toast(message) {
        els.toast.textContent = message;
        els.toast.hidden = false;
        clearTimeout(state.toastTimer);
        state.toastTimer = setTimeout(() => { els.toast.hidden = true; }, 3500);
    }

    function setPickerStatus(message, isError) {
        els.pickerStatus.textContent = message || '';
        els.pickerStatus.classList.toggle('is-error', !!isError);
    }

    function appendReconnect(container) {
        const link = make('a', 'cb-btn cb-btn-primary', 'RECONNECT GOOGLE DRIVE');
        link.href = CB.urls.connect;
        container.appendChild(document.createElement('br'));
        container.appendChild(link);
    }

    // ---------- Instant-open cache (per tab session) ----------
    // The last folder listing and folder tree are kept in sessionStorage so
    // coming back from the dashboard paints with zero network wait; Drive is
    // re-checked in the background. sessionStorage dies with the tab, so
    // nothing lingers on shared machines.

    const LIST_PREFIX = 'cb:list:';
    const DIR_PREFIX = 'cb:dir:';
    const listKey = (folderId, sort) => `${LIST_PREFIX}${folderId}:${sort}`;

    function readLocal(key) {
        try {
            const raw = sessionStorage.getItem(key);
            return raw ? JSON.parse(raw) : null;
        } catch (e) {
            return null;
        }
    }

    function writeLocal(key, value) {
        try {
            sessionStorage.setItem(key, JSON.stringify(value));
        } catch (e) {
            // quota or disabled storage: the instant path just won't be used
        }
    }

    function pruneLists(keepKey) {
        try {
            const keys = [];
            for (let i = 0; i < sessionStorage.length; i++) {
                const k = sessionStorage.key(i);
                if (k && k.startsWith(LIST_PREFIX) && k !== keepKey) keys.push(k);
            }
            keys.slice(0, Math.max(0, keys.length - 2)).forEach((k) => sessionStorage.removeItem(k));
        } catch (e) {
            // ignore
        }
    }

    const listSignature = (images) => images.map((i) => `${i.id}:${i.modified || ''}`).join('|');

    // ---------- Folder picker ----------

    function showPicker() {
        stopSlideshow();
        els.viewer.hidden = true;
        els.picker.hidden = false;
        els.backViewer.hidden = !state.folder;
    }

    function setActiveTab(folderId) {
        if (folderId === 'root') state.tab = 'root';
        else if (folderId === 'shared') state.tab = 'shared';
        els.tabRoot.classList.toggle('is-active', state.tab === 'root');
        els.tabShared.classList.toggle('is-active', state.tab === 'shared');
    }

    function renderFolders(folders) {
        els.folderList.replaceChildren();
        folders.forEach((f) => {
            const item = make('div', 'cb-folder-item');

            const open = make('button', 'cb-folder-row');
            open.type = 'button';
            open.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"></path></svg>';
            open.appendChild(make('span', '', f.name));
            open.addEventListener('click', () => loadPicker(f.id));

            const view = make('button', 'cb-btn cb-btn-ghost', 'VIEW');
            view.type = 'button';
            view.addEventListener('click', () => openFolder(f.id));

            item.append(open, view);
            els.folderList.appendChild(item);
        });
    }

    function paintPicker(data, notice) {
        state.pickerFolder = data.folder;
        setActiveTab(data.folder.id);
        els.pathName.textContent = data.folder.name;
        els.up.disabled = !data.folder.parent;
        els.openHere.disabled = data.folder.id === 'shared';
        renderFolders(data.folders);
        setPickerStatus(notice || (data.folders.length ? '' : 'No subfolders here.'), !!notice);
    }

    async function loadPicker(parentId, notice) {
        const seq = ++state.pickSeq;
        const key = DIR_PREFIX + parentId;
        const local = readLocal(key);
        if (local && local.folder && local.folders) {
            // show the last known folders immediately, refresh underneath
            paintPicker(local, notice);
        } else {
            setPickerStatus('Loading folders…');
            els.folderList.replaceChildren();
        }
        try {
            const data = await api(`${CB.urls.folders}?parent=${encodeURIComponent(parentId)}`);
            if (seq !== state.pickSeq) return;
            writeLocal(key, { folder: data.folder, folders: data.folders });
            paintPicker(data, notice);
        } catch (e) {
            if (seq !== state.pickSeq) return;
            setPickerStatus(e.message, true);
            if (e.needsConnect) appendReconnect(els.pickerStatus);
        }
    }

    // ---------- Viewer ----------

    function showViewer() {
        els.picker.hidden = true;
        els.viewer.hidden = false;
    }

    function fetchListing(folderId, swr) {
        const q = `${CB.urls.images}?folder=${encodeURIComponent(folderId)}&sort=${encodeURIComponent(state.sort)}`;
        return api(swr ? `${q}&swr=1` : q);
    }

    function applyListing(data, keepId, persist) {
        state.preloadSeq++;
        state.prefetchRun++;
        state.preloading = false;
        state.folder = data.folder;
        state.images = data.images;
        state.cached = new Set(data.cached || []);

        let idx = 0;
        if (keepId) {
            const found = data.images.findIndex((i) => i.id === keepId);
            if (found >= 0) idx = found;
        }
        state.index = idx;
        els.folderTitle.textContent = data.folder.name;
        els.folderTitle.title = data.folder.name;
        if (data.truncated) {
            toast(state.sort === 'name'
                ? 'Showing the first 1000 images in this folder.'
                : 'Showing the 1000 most recent images in this folder.');
        }

        if (persist) {
            const key = listKey(data.folder.id, state.sort);
            writeLocal(key, { folder: data.folder, images: data.images, truncated: !!data.truncated });
            pruneLists(key);
        }
    }

    async function fetchImages(folderId, keepId, swr) {
        const seq = ++state.listSeq;
        const data = await fetchListing(folderId, swr);
        if (seq !== state.listSeq) return null;
        applyListing(data, keepId, !data.stale);
        return data;
    }

    // Re-check Drive after an instant paint. If nothing changed we leave the
    // screen alone (no flicker, no lost zoom); otherwise we swap in the new
    // list and stay on the same image.
    function revalidate(folderId) {
        const seq = state.listSeq;
        const run = (async () => {
            try {
                const data = await fetchListing(folderId, false);
                if (seq !== state.listSeq || !state.folder || state.folder.id !== data.folder.id) return;
                const key = listKey(data.folder.id, state.sort);
                if (listSignature(data.images) === listSignature(state.images)) {
                    (data.cached || []).forEach((id) => state.cached.add(id));
                    state.folder = data.folder;
                    writeLocal(key, { folder: data.folder, images: data.images, truncated: !!data.truncated });
                    updatePreloadLabel();
                    return;
                }
                applyListing(data, current() ? current().id : null, true);
                render();
            } catch (e) {
                if (e.needsConnect) toast('Google Drive access expired. Please reconnect.');
            } finally {
                if (state.revalidating === run) state.revalidating = null;
            }
        })();
        state.revalidating = run;
    }

    async function openFolder(folderId) {
        setPickerStatus('Loading images…');
        const local = readLocal(listKey(folderId, state.sort));
        try {
            if (local && local.images && local.folder) {
                ++state.listSeq;
                applyListing(local, null, false);
                state.autoRefreshed = false;
                setPickerStatus('');
                showViewer();
                render();
                revalidate(folderId);
                return;
            }
            const data = await fetchImages(folderId, null, true);
            if (!data) return;
            state.autoRefreshed = false;
            setPickerStatus('');
            showViewer();
            render();
            if (data.stale) revalidate(folderId);
        } catch (e) {
            showPicker();
            if (state.pickerFolder) {
                setPickerStatus(e.message, true);
                if (e.needsConnect) appendReconnect(els.pickerStatus);
            } else {
                loadPicker('root', `Could not open that folder: ${e.message}`);
            }
        }
    }

    async function reloadFolder(message, resetToFirst) {
        if (!state.folder) return;
        // After a sort change we go back to the first image of the new order;
        // staying on the same image made the sort look like it did nothing.
        const keepId = !resetToFirst && current() ? current().id : null;
        try {
            const data = await fetchImages(state.folder.id, keepId, false);
            if (!data) return;
            render();
            if (message) toast(message);
        } catch (e) {
            toast(e.message);
        }
    }

    function updatePreloadLabel() {
        const total = state.images.length;
        if (state.preloading) {
            els.preloadLabel.textContent = `${state.cached.size}/${total} · Stop`;
            els.preloadLabel.classList.add('is-busy');
            els.preload.classList.add('is-active');
        } else {
            els.preloadLabel.textContent = total && state.cached.size >= total ? 'All cached' : 'Preload all';
            els.preloadLabel.classList.remove('is-busy');
            els.preload.classList.remove('is-active');
        }
    }

    function render() {
        const total = state.images.length;
        const item = current();

        els.total.textContent = String(total);
        els.jump.max = String(Math.max(total, 1));
        els.jump.style.width = `${String(Math.max(total, 1)).length + 0.8}ch`;
        els.jump.value = total ? String(state.index + 1) : '0';
        els.jump.disabled = !total;
        els.prev.disabled = state.index <= 0;
        els.next.disabled = state.index >= total - 1;
        els.empty.hidden = total > 0;
        updatePreloadLabel();

        els.play.disabled = total < 2;
        els.zoom.disabled = !total;

        if (!item) {
            els.img.classList.remove('is-ready');
            els.img.removeAttribute('src');
            els.filename.textContent = '';
            els.meta.textContent = '';
            els.loading.hidden = true;
            els.error.hidden = true;
            els.download.classList.add('is-disabled');
            els.driveLink.classList.add('is-disabled');
            return;
        }

        els.filename.textContent = item.name;
        const dims = item.width && item.height ? `${item.width} × ${item.height}` : '';
        els.meta.textContent = [dims, formatSize(item.size), formatDate(item.modified)].filter(Boolean).join('  ·  ');

        els.download.href = imageUrl(item.id, 'download=1');
        els.download.classList.remove('is-disabled');
        if (item.link) {
            els.driveLink.href = item.link;
            els.driveLink.classList.remove('is-disabled');
        } else {
            els.driveLink.classList.add('is-disabled');
        }

        loadCurrent();
    }

    function cacheBlob(id, blob) {
        if (state.blobCache.has(id)) return state.blobCache.get(id);
        while (state.blobCache.size >= BLOB_CACHE_MAX && evictFarthest()) {
            // keep the images nearest the current one; drop the farthest
        }
        const url = URL.createObjectURL(blob);
        state.blobCache.set(id, url);
        return url;
    }

    function peekBlob(id) {
        return state.blobCache.get(id) || null;
    }

    // Evict by distance from the current index (not insertion order), so the
    // next images we just prefetched can never be the first to go.
    function evictFarthest() {
        const total = state.images.length;
        const shown = els.img.getAttribute('src');
        let worstId = null;
        let worstDist = -1;
        const pos = new Map(state.images.map((it, i) => [it.id, i]));
        for (const [id, url] of state.blobCache) {
            if (url === shown) continue;
            const i = pos.get(id);
            let dist = 1e9;
            if (i !== undefined) {
                const fwd = (i - state.index + total) % total;
                // going forward is far more likely than going back
                dist = Math.min(fwd, (total - fwd) * 3);
            }
            if (dist > worstDist) {
                worstDist = dist;
                worstId = id;
            }
        }
        if (worstId === null) return false;
        URL.revokeObjectURL(state.blobCache.get(worstId));
        state.blobCache.delete(worstId);
        return true;
    }

    // Downloads an image once and stores it in the blob cache *as soon as it
    // arrives*, whether or not the viewer is still waiting for it. Previously
    // a slow download that finished after the user (or slideshow) had moved on
    // was thrown away, so on 2G/3G nothing ever finished loading.
    function fetchBlob(id) {
        if (!state.inflight.has(id)) {
            state.inflight.set(id, (async () => {
                try {
                    let res = await fetch(imageUrl(id));
                    if (res.status === 404 && state.revalidating) {
                        // the server's folder index may not exist yet (it is
                        // rebuilt by the background re-list); wait and retry
                        await state.revalidating;
                        res = await fetch(imageUrl(id));
                    }
                    if (!res.ok) throw new Error(String(res.status));
                    const blob = await res.blob();
                    cacheBlob(id, blob);
                    state.cached.add(id);
                    updatePreloadLabel();
                    return blob;
                } finally {
                    state.inflight.delete(id);
                }
            })());
        }
        return state.inflight.get(id);
    }

    function clearBlobCache() {
        state.blobCache.forEach((url) => URL.revokeObjectURL(url));
        state.blobCache.clear();
    }

    async function loadCurrent() {
        const item = current();
        if (!item) return;

        const seq = ++state.loadSeq;

        if (state.fullUrl) {
            URL.revokeObjectURL(state.fullUrl);
            state.fullUrl = null;
        }
        state.fullFor = null;
        resetZoom();
        els.error.hidden = true;

        const cachedUrl = peekBlob(item.id);
        if (cachedUrl) {
            els.img.alt = item.name;
            els.img.src = cachedUrl;
            try {
                await els.img.decode();
            } catch (e) {
                // decode can reject for formats the browser lazily decodes
            }
            if (seq !== state.loadSeq) return;
            els.img.classList.add('is-ready');
            els.loading.hidden = true;
            prefetchAhead();
            slideshowReady();
            return;
        }

        els.img.classList.remove('is-ready');
        const spinnerTimer = setTimeout(() => {
            if (seq === state.loadSeq) els.loading.hidden = false;
        }, 150);

        try {
            let blob;
            try {
                blob = await fetchBlob(item.id);
            } catch (e) {
                const err = new Error(e.message);
                err.status = Number(e.message) || undefined;
                throw err;
            }
            if (seq !== state.loadSeq) return;

            els.img.alt = item.name;
            els.img.src = cacheBlob(item.id, blob);
            els.img.alt = item.name;
            try {
                await els.img.decode();
            } catch (e) {
                // decode can reject for formats the browser lazily decodes; onload path below still applies
            }
            if (seq !== state.loadSeq) return;
            els.img.classList.add('is-ready');
            prefetchAhead();
            slideshowReady();
        } catch (e) {
            if (seq !== state.loadSeq) return;
            handleLoadError(e.status);
        } finally {
            clearTimeout(spinnerTimer);
            if (seq === state.loadSeq) els.loading.hidden = true;
        }
    }

    async function handleLoadError(status) {
        if (status === 404 && !state.autoRefreshed) {
            state.autoRefreshed = true;
            await reloadFolder();
            return;
        }
        let text = 'Could not load this image.';
        if (status === 401) text = 'Google Drive access expired.';
        else if (status === 404) text = 'This image is no longer available. Try Refresh.';
        else if (status === 413) text = 'This image is too large to preview. Use Download or Open in Drive.';
        els.errorText.textContent = text;
        els.retry.hidden = status === 413;
        els.error.hidden = false;
        if (status === 401 && !els.error.querySelector('a')) appendReconnect(els.error);
    }

    async function prefetchAhead() {
        const run = ++state.prefetchRun;
        const total = state.images.length;
        if (total < 2) return;
        const { batch, concurrency } = netProfile();

        const ids = [];
        for (let step = 1; step <= batch; step++) {
            const item = state.images[(state.index + step) % total];
            if (!item) break;
            if (state.blobCache.has(item.id)) continue;
            ids.push(item.id);
        }
        if (!ids.length) return;

        let cursor = 0;
        const worker = async () => {
            while (run === state.prefetchRun && cursor < ids.length) {
                if (document.hidden) {
                    await new Promise((r) => setTimeout(r, 1000));
                    continue;
                }
                try {
                    await fetchBlob(ids[cursor++]);
                } catch (e) {
                    // prefetch is best-effort; loadCurrent handles real errors
                }
            }
        };
        await Promise.all(Array.from({ length: concurrency }, worker));
    }

    // ---------- Navigation ----------

    function animateTo(next, dir) {
        const width = els.stage.clientWidth || 1;
        if (state.animating) {
            state.index = next;
            render();
            return;
        }
        state.animating = true;
        state.index = next;
        els.slide.classList.add('is-animating');
        els.slide.style.transform = `translateX(${-dir * width}px)`;
        setTimeout(() => {
            els.slide.classList.remove('is-animating');
            els.slide.style.transform = `translateX(${dir * width}px)`;
            render();
            void els.slide.offsetWidth;
            els.slide.classList.add('is-animating');
            els.slide.style.transform = 'translateX(0)';
            setTimeout(() => {
                els.slide.classList.remove('is-animating');
                els.slide.style.transform = '';
                state.animating = false;
            }, ANIM_MS);
        }, ANIM_MS);
    }

    function go(delta, wrap) {
        const total = state.images.length;
        if (total < 2) return;
        let next = state.index + delta;
        if (wrap) next = (next + total) % total;
        else if (next < 0 || next >= total) return;
        if (next === state.index) return;
        animateTo(next, delta > 0 ? 1 : -1);
        if (state.playing) clearTimeout(state.timer);
    }

    function jumpTo(n) {
        const total = state.images.length;
        if (!total || isNaN(n)) {
            els.jump.value = String(state.index + 1);
            return;
        }
        state.index = Math.min(Math.max(n, 1), total) - 1;
        render();
    }

    // ---------- Slideshow ----------

    // The timer is a *dwell* timer: it starts when the current image is
    // actually on screen, and the slideshow only advances once the next image
    // is already in memory (or SLIDESHOW_WAIT_MAX_MS has passed). On a slow
    // link the slideshow therefore pauses on the picture instead of racing
    // through spinners.
    function startSlideshow() {
        clearTimeout(state.timer);
        state.playing = true;
        els.play.classList.add('is-active');
        if (els.img.classList.contains('is-ready')) scheduleAdvance();
    }

    function nextItem() {
        const total = state.images.length;
        return total > 1 ? state.images[(state.index + 1) % total] : null;
    }

    function scheduleAdvance() {
        clearTimeout(state.timer);
        if (!state.playing) return;
        const shownAt = Date.now();
        const tick = () => {
            if (!state.playing) return;
            const next = nextItem();
            const waited = Date.now() - shownAt - SLIDESHOW_MS;
            const ready = !next || state.blobCache.has(next.id) || waited > SLIDESHOW_WAIT_MAX_MS;
            if (state.scale === 1 && !document.hidden && ready && !state.animating) {
                go(1, true);
            } else {
                if (next && !state.blobCache.has(next.id)) fetchBlob(next.id).then(() => {}, () => {});
                state.timer = setTimeout(tick, 400);
            }
        };
        state.timer = setTimeout(tick, SLIDESHOW_MS);
    }

    function slideshowReady() {
        if (state.playing) scheduleAdvance();
    }

    function stopSlideshow() {
        clearTimeout(state.timer);
        state.timer = null;
        state.playing = false;
        els.play.classList.remove('is-active');
    }

    function toggleSlideshow() {
        if (state.playing) stopSlideshow();
        else if (state.images.length > 1) startSlideshow();
    }

    // ---------- Zoom & pan ----------

    function clampPan() {
        const maxX = Math.max(0, (els.img.clientWidth * state.scale - els.stage.clientWidth) / 2);
        const maxY = Math.max(0, (els.img.clientHeight * state.scale - els.stage.clientHeight) / 2);
        state.tx = Math.min(Math.max(state.tx, -maxX), maxX);
        state.ty = Math.min(Math.max(state.ty, -maxY), maxY);
    }

    function applyTransform() {
        if (state.scale === 1) {
            state.tx = 0;
            state.ty = 0;
            els.img.style.transform = '';
        } else {
            clampPan();
            els.img.style.transform = `translate(${state.tx}px, ${state.ty}px) scale(${state.scale})`;
        }
        els.stage.classList.toggle('is-zoomed', state.scale > 1);
        els.zoom.classList.toggle('is-active', state.scale > 1);
    }

    function resetZoom() {
        state.scale = 1;
        applyTransform();
    }

    // Views use a ~1600px preview. The first time the user zooms in we fetch
    // the original in the background and swap it in so text stays sharp.
    async function upgradeToFull() {
        const item = current();
        if (!item || state.fullFor === item.id || item.size < 250 * 1024 || item.mime === 'image/gif') return;
        state.fullFor = item.id;
        const seq = state.loadSeq;
        try {
            const res = await fetch(imageUrl(item.id, 'size=full'));
            if (!res.ok) return;
            const blob = await res.blob();
            if (seq !== state.loadSeq) return;
            const url = URL.createObjectURL(blob);
            if (state.fullUrl) URL.revokeObjectURL(state.fullUrl);
            state.fullUrl = url;
            els.img.src = url;
        } catch (e) {
            state.fullFor = null;
        }
    }

    function zoomAt(newScale, clientX, clientY) {
        const s = Math.min(Math.max(newScale, 1), MAX_SCALE);
        const rect = els.stage.getBoundingClientRect();
        const vx = clientX - (rect.left + rect.width / 2);
        const vy = clientY - (rect.top + rect.height / 2);
        const ratio = s / state.scale;
        state.tx = vx - (vx - state.tx) * ratio;
        state.ty = vy - (vy - state.ty) * ratio;
        const wasFit = state.scale === 1;
        state.scale = s;
        applyTransform();
        if (wasFit && s > 1) upgradeToFull();
    }

    function stageCenter() {
        const rect = els.stage.getBoundingClientRect();
        return { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 };
    }

    function canZoom() {
        return !!current() && els.img.classList.contains('is-ready');
    }

    function toggleZoom(clientX, clientY) {
        if (!canZoom()) return;
        if (state.scale > 1) {
            resetZoom();
        } else {
            const c = stageCenter();
            zoomAt(ZOOM_TAP_SCALE, clientX === undefined ? c.x : clientX, clientY === undefined ? c.y : clientY);
        }
    }

    // ---------- Pointer gestures ----------

    const pointers = new Map();
    let drag = null;
    let pinch = null;
    let lastTap = { time: 0, x: 0, y: 0 };

    function pointerDistance() {
        const [a, b] = Array.from(pointers.values());
        return Math.hypot(a.x - b.x, a.y - b.y);
    }

    function pointerMidpoint() {
        const [a, b] = Array.from(pointers.values());
        return { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
    }

    function onPointerDown(e) {
        if (e.target.closest('.cb-nav, .cb-counter, .cb-error')) return;
        if (e.pointerType === 'mouse' && e.button !== 0) return;
        els.stage.focus({ preventScroll: true });
        els.stage.setPointerCapture(e.pointerId);
        pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });

        if (pointers.size === 1) {
            drag = { startX: e.clientX, startY: e.clientY, lastX: e.clientX, lastY: e.clientY, mode: null, time: Date.now() };
        } else if (pointers.size === 2) {
            drag = null;
            pinch = { dist: pointerDistance(), scale0: state.scale };
        }
    }

    function onPointerMove(e) {
        if (!pointers.has(e.pointerId)) return;
        pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });

        if (pinch && pointers.size >= 2) {
            const mid = pointerMidpoint();
            const target = pinch.scale0 * (pointerDistance() / pinch.dist);
            zoomAt(target, mid.x, mid.y);
            return;
        }
        if (!drag) return;

        const dx = e.clientX - drag.startX;
        const dy = e.clientY - drag.startY;

        if (state.scale > 1) {
            state.tx += e.clientX - drag.lastX;
            state.ty += e.clientY - drag.lastY;
            drag.lastX = e.clientX;
            drag.lastY = e.clientY;
            drag.mode = 'pan';
            els.stage.classList.add('is-dragging');
            applyTransform();
            return;
        }

        if (drag.mode === null && Math.abs(dx) > 8 && Math.abs(dx) > Math.abs(dy)) drag.mode = 'swipe';
        if (drag.mode === 'swipe' && !state.animating) {
            const atStart = state.index === 0 && dx > 0;
            const atEnd = state.index === state.images.length - 1 && dx < 0;
            els.slide.style.transform = `translateX(${atStart || atEnd ? dx * 0.3 : dx}px)`;
        }
    }

    function onPointerEnd(e) {
        if (!pointers.has(e.pointerId)) return;
        pointers.delete(e.pointerId);
        els.stage.classList.remove('is-dragging');

        if (pinch) {
            if (pointers.size < 2) pinch = null;
            if (pointers.size === 0) drag = null;
            return;
        }
        if (!drag) return;

        const dx = e.clientX - drag.startX;
        const dy = e.clientY - drag.startY;
        const finished = drag;
        drag = null;

        if (finished.mode === 'swipe' && e.type === 'pointerup') {
            const dir = dx < 0 ? 1 : -1;
            const target = state.index + dir;
            const valid = target >= 0 && target < state.images.length;
            if (Math.abs(dx) >= SWIPE_MIN_PX && valid) {
                animateTo(target, dir);
                if (state.playing) clearTimeout(state.timer);
            } else {
                els.slide.classList.add('is-animating');
                els.slide.style.transform = 'translateX(0)';
                setTimeout(() => {
                    els.slide.classList.remove('is-animating');
                    els.slide.style.transform = '';
                }, ANIM_MS);
            }
            return;
        }

        const moved = Math.hypot(dx, dy);
        if (e.type === 'pointerup' && moved < 10 && Date.now() - finished.time < 300) {
            const now = Date.now();
            if (now - lastTap.time < 320 && Math.hypot(e.clientX - lastTap.x, e.clientY - lastTap.y) < 30) {
                toggleZoom(e.clientX, e.clientY);
                lastTap = { time: 0, x: 0, y: 0 };
            } else {
                lastTap = { time: now, x: e.clientX, y: e.clientY };
            }
        }
    }

    function onWheel(e) {
        if (!canZoom()) return;
        e.preventDefault();
        const factor = e.deltaY < 0 ? 1.15 : 1 / 1.15;
        zoomAt(state.scale * factor, e.clientX, e.clientY);
    }

    // ---------- Preload & cache ----------

    async function preloadAll() {
        if (state.preloading) {
            state.preloadSeq++;
            state.preloading = false;
            updatePreloadLabel();
            return;
        }
        const queue = state.images.filter((i) => !state.cached.has(i.id));
        if (!queue.length) {
            toast('Every image in this folder is already cached.');
            return;
        }
        const seq = ++state.preloadSeq;
        state.preloading = true;
        updatePreloadLabel();

        let failed = 0;
        let cursor = 0;
        const worker = async () => {
            while (seq === state.preloadSeq && cursor < queue.length) {
                const item = queue[cursor++];
                try {
                    await fetchBlob(item.id);
                } catch (e) {
                    failed++;
                }
            }
        };
        await Promise.all([worker(), worker()]);

        if (seq !== state.preloadSeq) return;
        state.preloading = false;
        updatePreloadLabel();
        toast(failed ? `Cached with ${failed} failure${failed === 1 ? '' : 's'}.` : 'All images cached.');
    }

    async function clearCache() {
        try {
            const data = await api(CB.urls.clearCache, {
                method: 'POST',
                headers: { 'X-CSRFToken': CB.csrf },
            });
            state.preloadSeq++;
            state.preloading = false;
            state.cached.clear();
            state.inflight.clear();
            clearBlobCache();
            updatePreloadLabel();
            toast(`Cleared ${data.freed_mb} MB of temporary images.`);
        } catch (e) {
            toast(e.message);
        }
    }

    // ---------- Fullscreen ----------

    const CHROME_IDLE_MS = 2800;

    function wakeChrome() {
        els.viewer.classList.remove('is-idle');
        clearTimeout(state.idleTimer);
        if (!document.fullscreenElement) return;
        state.idleTimer = setTimeout(() => els.viewer.classList.add('is-idle'), CHROME_IDLE_MS);
    }

    function toggleFullscreen() {
        if (document.fullscreenElement) {
            document.exitFullscreen();
        } else if (els.viewer.requestFullscreen) {
            els.viewer.requestFullscreen().catch(() => toast('Fullscreen is not available here.'));
        }
    }

    // ---------- Wiring ----------

    function onKeyDown(e) {
        if (els.viewer.hidden) return;
        const tag = e.target.tagName;
        if (tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA') return;

        switch (e.key) {
            case 'ArrowLeft':
                go(-1);
                break;
            case 'ArrowRight':
                go(1);
                break;
            case 'Home':
                if (state.images.length) jumpTo(1);
                break;
            case 'End':
                if (state.images.length) jumpTo(state.images.length);
                break;
            case ' ':
                if (tag === 'BUTTON' || tag === 'A') return;
                toggleSlideshow();
                break;
            case 'f':
            case 'F':
                toggleFullscreen();
                break;
            case '+':
            case '=': {
                if (!canZoom()) break;
                const c = stageCenter();
                zoomAt(state.scale * 1.4, c.x, c.y);
                break;
            }
            case '-': {
                if (!canZoom()) break;
                const c = stageCenter();
                zoomAt(state.scale / 1.4, c.x, c.y);
                break;
            }
            case 'Escape':
                if (state.scale > 1) resetZoom();
                else if (state.playing) stopSlideshow();
                break;
            default:
                return;
        }
        e.preventDefault();
    }

    function init() {
        els.tabRoot.addEventListener('click', () => { state.tab = 'root'; loadPicker('root'); });
        els.tabShared.addEventListener('click', () => { state.tab = 'shared'; loadPicker('shared'); });
        els.up.addEventListener('click', () => {
            if (state.pickerFolder && state.pickerFolder.parent) loadPicker(state.pickerFolder.parent);
        });
        els.openHere.addEventListener('click', () => {
            if (state.pickerFolder) openFolder(state.pickerFolder.id);
        });
        els.backViewer.addEventListener('click', () => {
            els.picker.hidden = true;
            els.viewer.hidden = false;
        });

        els.changeFolder.addEventListener('click', () => {
            showPicker();
            const start = state.folder && state.folder.parent ? state.folder.parent : 'root';
            loadPicker(start);
        });
        els.sort.addEventListener('change', () => {
            state.sort = els.sort.value;
            reloadFolder('', true);
        });

        els.sort.value = state.sort;

        els.prev.addEventListener('click', () => go(-1));
        els.next.addEventListener('click', () => go(1));
        els.retry.addEventListener('click', () => {
            els.error.hidden = true;
            loadCurrent();
        });

        els.jump.addEventListener('focus', () => els.jump.select());
        els.jump.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                jumpTo(parseInt(els.jump.value, 10));
                els.jump.blur();
            } else if (e.key === 'Escape') {
                els.jump.value = String(state.index + 1);
                els.jump.blur();
            }
        });
        els.jump.addEventListener('change', () => jumpTo(parseInt(els.jump.value, 10)));

        els.play.addEventListener('click', toggleSlideshow);
        els.zoom.addEventListener('click', () => toggleZoom());
        els.fullscreen.addEventListener('click', toggleFullscreen);
        els.preload.addEventListener('click', preloadAll);
        els.refresh.addEventListener('click', () => reloadFolder('Folder refreshed.'));
        els.clear.addEventListener('click', clearCache);

        if (!els.viewer.requestFullscreen) els.fullscreen.hidden = true;
        document.addEventListener('fullscreenchange', () => {
            els.fullscreen.classList.toggle('is-active', !!document.fullscreenElement);
            wakeChrome();
            applyTransform();
        });

        els.stage.addEventListener('pointerdown', onPointerDown);
        els.stage.addEventListener('pointermove', onPointerMove);
        els.stage.addEventListener('pointerup', onPointerEnd);
        els.stage.addEventListener('pointercancel', onPointerEnd);
        els.stage.addEventListener('wheel', onWheel, { passive: false });
        els.viewer.addEventListener('pointermove', wakeChrome);
        els.viewer.addEventListener('pointerdown', wakeChrome);
        document.addEventListener('keydown', (e) => { if (document.fullscreenElement) wakeChrome(); });
        document.addEventListener('keydown', onKeyDown);
        window.addEventListener('resize', applyTransform);
        window.addEventListener('beforeunload', clearBlobCache);

        const last = CB.lastFolder;
        if (last && last.id) {
            els.picker.hidden = false;
            openFolder(last.id);
        } else {
            showPicker();
            loadPicker('root');
        }
    }

    init();
})();