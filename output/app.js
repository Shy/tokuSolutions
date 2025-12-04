// Single Page Application for tokuSolutions

let manuals = [];
let currentManual = null;
let showOverlays = true;
let showTranslations = true;

// Router
function navigate(view, manual = null) {
    const listView = document.getElementById('listView');
    const viewerView = document.getElementById('viewerView');

    if (view === 'list') {
        listView.classList.remove('hidden');
        viewerView.classList.add('hidden');
        window.history.pushState({ view: 'list' }, '', '#');
        document.title = 'Translated Toy Manuals';
    } else if (view === 'viewer' && manual) {
        listView.classList.add('hidden');
        viewerView.classList.remove('hidden');
        window.history.pushState({ view: 'viewer', manual }, '', `#manual=${encodeURIComponent(manual)}`);
        loadManual(manual);
    }
}

// Load manual list from manifest.json
async function loadManualList() {
    const loadingIndicator = document.getElementById('loadingIndicator');

    try {
        // Load manifest.json - single fast request
        const response = await fetch('manifest.json');
        if (!response.ok) {
            throw new Error('Failed to load manifest.json');
        }

        const data = await response.json();
        manuals = data.manuals || [];
        renderManualList(manuals);
    } catch (err) {
        console.error('Failed to load manual list:', err);
        document.getElementById('manualsGrid').innerHTML = '<p style="padding: 2rem; color: red;">Failed to load manuals.</p>';
    } finally {
        // Always hide loading indicator
        if (loadingIndicator) {
            loadingIndicator.classList.add('hidden');
        }
    }
}

// Render manual list
function renderManualList(filteredManuals) {
    const grid = document.getElementById('manualsGrid');
    const statsDisplay = document.getElementById('statsDisplay');
    const noResults = document.getElementById('noResults');

    grid.innerHTML = '';

    if (filteredManuals.length === 0) {
        grid.classList.add('hidden');
        noResults.classList.remove('hidden');
        statsDisplay.textContent = '0 manuals';
        return;
    }

    grid.classList.remove('hidden');
    noResults.classList.add('hidden');

    filteredManuals.forEach(manual => {
        const card = document.createElement('div');
        card.className = 'card';

        const displayName = manual.name.replace(/-/g, ' ');

        const sourceLink = manual.source_url
            ? `<a href="${manual.source_url}" target="_blank" onclick="event.stopPropagation()">📄 Original</a>`
            : '';

        card.innerHTML = `
            <div class="card-link">
                <img src="${manual.thumbnail}" alt="${displayName}" class="thumbnail" loading="lazy">
                <div class="card-content">
                    <div class="card-title">${displayName}</div>
                    <div class="card-meta">
                        <span class="badge">${manual.pages} pages</span>
                        <span class="badge">${manual.blocks} blocks</span>
                    </div>
                    ${sourceLink ? `<div class="card-actions">${sourceLink}</div>` : ''}
                </div>
            </div>
        `;

        card.addEventListener('click', (e) => {
            if (!e.target.closest('a')) {
                navigate('viewer', manual.name);
            }
        });

        grid.appendChild(card);
    });

    statsDisplay.textContent = `${filteredManuals.length} manual${filteredManuals.length !== 1 ? 's' : ''}`;
}

// Search filter
function filterManuals(query) {
    const lowerQuery = query.toLowerCase();
    return manuals.filter(manual =>
        manual.source.toLowerCase().includes(lowerQuery) ||
        manual.name.toLowerCase().includes(lowerQuery)
    );
}

// Load manual for viewing
async function loadManual(manualName) {
    try {
        const response = await fetch(`${manualName}/translations.json`);
        if (!response.ok) throw new Error(`Failed to load ${manualName}/translations.json`);

        currentManual = await response.json();

        // Update UI
        document.title = currentManual.meta.source;
        document.getElementById('manualTitle').textContent = currentManual.meta.source;

        const sourceLink = document.getElementById('sourceLink');
        if (currentManual.meta.source_url) {
            sourceLink.href = currentManual.meta.source_url;
            sourceLink.style.display = 'inline-flex';
        } else {
            sourceLink.style.display = 'none';
        }

        renderPages(manualName);
        renderTextList();

        // Set max page number for jump input
        document.getElementById('pageJump').max = currentManual.meta.pages;
    } catch (err) {
        console.error('Failed to load manual:', err);
        document.getElementById('pagePanel').innerHTML = `<p style="padding: 2rem; color: red;">Failed to load: ${err.message}</p>`;
    }
}

// Render pages
function renderPages(manualName) {
    const pagePanel = document.getElementById('pagePanel');
    pagePanel.innerHTML = '';

    currentManual.pages.forEach((page, pageIdx) => {
        const pageDiv = document.createElement('div');
        pageDiv.className = 'page';
        pageDiv.id = `page-${pageIdx}`;

        const img = document.createElement('img');
        img.src = `${manualName}/${page.image}`;
        img.alt = `Page ${pageIdx + 1}`;
        img.onload = function() {
            renderOverlays(pageDiv, page, pageIdx);
        };

        const label = document.createElement('div');
        label.className = 'page-label';
        label.textContent = `Page ${pageIdx + 1}`;

        pageDiv.appendChild(img);
        pageDiv.appendChild(label);
        pagePanel.appendChild(pageDiv);
    });
}

// Render overlays using percentage positioning (scales automatically)
function renderOverlays(pageDiv, page, pageIdx) {
    page.blocks.forEach((block, blockIdx) => {
        const overlay = document.createElement('div');
        overlay.className = 'overlay';
        overlay.textContent = showTranslations ? block.translation : block.text;
        overlay.dataset.pageId = pageIdx;
        overlay.dataset.blockId = blockIdx;

        // bbox is [x, y, w, h] normalized (0-1), convert to percentage
        overlay.style.left = (block.bbox[0] * 100) + '%';
        overlay.style.top = (block.bbox[1] * 100) + '%';
        overlay.style.width = (block.bbox[2] * 100) + '%';
        overlay.style.height = (block.bbox[3] * 100) + '%';

        if (!showOverlays) {
            overlay.style.display = 'none';
        }

        overlay.addEventListener('click', () => {
            highlightBlock(pageIdx, blockIdx);
        });

        overlay.addEventListener('mouseenter', () => {
            highlightBlock(pageIdx, blockIdx);
        });

        overlay.addEventListener('mouseleave', () => {
            clearHighlights();
        });

        pageDiv.appendChild(overlay);
    });
}

// Render text list
function renderTextList() {
    const textList = document.getElementById('textList');
    textList.innerHTML = '';

    currentManual.pages.forEach((page, pageIdx) => {
        page.blocks.forEach((block, blockIdx) => {
            const item = document.createElement('div');
            item.className = 'text-item';
            item.dataset.pageId = pageIdx;
            item.dataset.blockId = blockIdx;

            const original = document.createElement('div');
            original.className = 'text-original';
            original.textContent = block.text;

            const translation = document.createElement('div');
            translation.className = 'text-translation';
            translation.textContent = block.translation;
            if (!showTranslations) {
                translation.style.display = 'none';
            }

            item.appendChild(original);
            item.appendChild(translation);

            item.addEventListener('click', () => {
                highlightBlock(pageIdx, blockIdx);
                document.getElementById(`page-${pageIdx}`).scrollIntoView({ behavior: 'smooth', block: 'center' });
            });

            item.addEventListener('mouseenter', () => {
                highlightBlock(pageIdx, blockIdx);
                document.getElementById(`page-${pageIdx}`).scrollIntoView({ behavior: 'smooth', block: 'center' });
            });

            item.addEventListener('mouseleave', () => {
                clearHighlights();
            });

            textList.appendChild(item);
        });
    });
}

// Highlight block
function highlightBlock(pageIdx, blockIdx) {
    // Remove previous highlights
    document.querySelectorAll('.highlight').forEach(el => el.classList.remove('highlight'));

    // Highlight all matching elements (overlay + text item)
    document.querySelectorAll(`[data-page-id="${pageIdx}"][data-block-id="${blockIdx}"]`).forEach(el => {
        el.classList.add('highlight');

        // Scroll text item into view if it's in the text panel
        if (el.classList.contains('text-item')) {
            el.scrollIntoView({ behavior: 'smooth', block: 'center' });
        }
    });
}

// Clear all highlights
function clearHighlights() {
    document.querySelectorAll('.highlight').forEach(el => el.classList.remove('highlight'));
}

// Page jump function
function jumpToPage(pageNum) {
    const pageDiv = document.getElementById(`page-${pageNum - 1}`);
    if (pageDiv) {
        pageDiv.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
}

// Load preferences from localStorage
function loadPreferences() {
    const savedOverlays = localStorage.getItem('showOverlays');
    if (savedOverlays !== null) {
        showOverlays = savedOverlays === 'true';
        document.getElementById('toggleOverlays').checked = showOverlays;
    }

    const savedTranslations = localStorage.getItem('showTranslations');
    if (savedTranslations !== null) {
        showTranslations = savedTranslations === 'true';
        document.getElementById('toggleTranslation').checked = showTranslations;
    }
}

// Event listeners
document.addEventListener('DOMContentLoaded', () => {
    loadPreferences();
    // Search
    document.getElementById('searchInput').addEventListener('input', (e) => {
        const filtered = filterManuals(e.target.value);
        renderManualList(filtered);
    });

    // Back button
    document.getElementById('backBtn').addEventListener('click', () => {
        navigate('list');
    });

    // Page jump
    const pageJumpInput = document.getElementById('pageJump');
    document.getElementById('pageJumpBtn').addEventListener('click', () => {
        const pageNum = parseInt(pageJumpInput.value);
        if (pageNum) jumpToPage(pageNum);
    });
    pageJumpInput.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') {
            const pageNum = parseInt(pageJumpInput.value);
            if (pageNum) jumpToPage(pageNum);
        }
    });

    // Toggles
    document.getElementById('toggleOverlays').addEventListener('change', (e) => {
        showOverlays = e.target.checked;
        localStorage.setItem('showOverlays', showOverlays);
        document.querySelectorAll('.overlay').forEach(el => {
            el.style.display = showOverlays ? 'block' : 'none';
        });
    });

    document.getElementById('toggleTranslation').addEventListener('change', (e) => {
        showTranslations = e.target.checked;
        localStorage.setItem('showTranslations', showTranslations);
        document.querySelectorAll('.text-translation').forEach(el => {
            el.style.display = showTranslations ? 'block' : 'none';
        });
        // Update overlays
        document.querySelectorAll('.overlay').forEach(el => {
            const pageIdx = parseInt(el.dataset.pageId);
            const blockIdx = parseInt(el.dataset.blockId);
            const block = currentManual.pages[pageIdx].blocks[blockIdx];
            el.textContent = showTranslations ? block.translation : block.text;
        });
    });

    // Handle browser back/forward
    window.addEventListener('popstate', (event) => {
        if (event.state) {
            if (event.state.view === 'list') {
                navigate('list');
            } else if (event.state.view === 'viewer' && event.state.manual) {
                navigate('viewer', event.state.manual);
            }
        } else {
            navigate('list');
        }
    });

    // Initial route
    const hash = window.location.hash;
    if (hash.startsWith('#manual=')) {
        const manual = decodeURIComponent(hash.replace('#manual=', ''));
        loadManualList().then(() => navigate('viewer', manual));
    } else {
        loadManualList().then(() => navigate('list'));
    }
});
