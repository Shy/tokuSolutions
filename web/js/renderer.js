// DOM rendering module
import { state, DOM } from './state.js';
import { sanitizeText } from './utils.js';
import { ImageLoader } from './image-loader.js';
import { ErrorHandler } from './errors.js';

// Render manual list
export function renderManualList(filteredManuals) {
    const grid = DOM.manualsGrid;
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

        // Get tags for this manual
        const manualTags = manual.tags || [];
        const tagsHTML = manualTags.map(tagId => {
            const tag = state.tagsData?.tags[tagId];
            if (!tag) return '';
            return `<span class="tag" style="background-color: ${tag.color}">${tag.name}</span>`;
        }).join('');

        const sourceLink = manual.source_url
            ? `<a href="${manual.source_url}" target="_blank" onclick="event.stopPropagation()">📄 Original</a>`
            : '';

        card.innerHTML = `
            <div class="card-link">
                <img src="../manuals/${sanitizeText(manual.thumbnail)}" alt="${sanitizeText(displayName)}" class="thumbnail" loading="lazy">
                <div class="card-content">
                    <div class="card-title">${sanitizeText(displayName)}</div>
                    ${tagsHTML ? `<div class="card-tags">${tagsHTML}</div>` : ''}
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
                // Import navigate dynamically to avoid circular dependency
                import('./navigation.js').then(({ navigate }) => {
                    navigate('viewer', manual.name);
                });
            }
        });

        grid.appendChild(card);
    });

    statsDisplay.textContent = `${filteredManuals.length} manual${filteredManuals.length !== 1 ? 's' : ''}`;
}

// Render tag filters
export function renderTagFilters() {
    if (!state.tagsData) return;

    const searchSection = document.querySelector('.search-section');
    const tagFilters = document.createElement('div');
    tagFilters.className = 'tag-filters';
    tagFilters.id = 'tagFilters';

    Object.entries(state.tagsData.tags).forEach(([tagId, tag]) => {
        const tagButton = document.createElement('button');
        tagButton.className = 'tag-filter';
        tagButton.textContent = tag.name;
        tagButton.style.setProperty('--tag-color', tag.color);
        tagButton.onclick = () => {
            // Import toggleTagFilter dynamically
            import('./navigation.js').then(({ toggleTagFilter }) => {
                toggleTagFilter(tagId);
            });
        };
        tagFilters.appendChild(tagButton);
    });

    searchSection.appendChild(tagFilters);
}

// Render pages
export function renderPages(manualName, renderToken) {
    // Check if render was cancelled
    if (renderToken !== undefined && renderToken !== state.currentRenderToken) {
        return;
    }

    const pagePanel = document.getElementById('pagePanel');
    pagePanel.innerHTML = '';

    // Use DocumentFragment for batch DOM insertion
    const fragment = document.createDocumentFragment();

    state.currentManual.pages.forEach((page, pageIdx) => {
        const pageDiv = document.createElement('div');
        pageDiv.className = 'page';
        pageDiv.id = `page-${pageIdx}`;

        const img = document.createElement('img');
        // Use lazy loading for images beyond the first 2 pages
        if (pageIdx < 2) {
            img.src = `../manuals/${manualName}/${page.image}`;
        } else {
            img.dataset.src = `../manuals/${manualName}/${page.image}`;
            img.src = 'data:image/svg+xml,%3Csvg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"%3E%3C/svg%3E';
        }
        img.alt = `Page ${pageIdx + 1}`;
        img.onload = function() {
            renderOverlays(pageDiv, page, pageIdx);
        };

        // Setup lazy loading
        if (pageIdx >= 2) {
            ImageLoader.observe(img);
        }

        const label = document.createElement('div');
        label.className = 'page-label';
        label.textContent = `Page ${pageIdx + 1}`;

        pageDiv.appendChild(img);
        pageDiv.appendChild(label);
        fragment.appendChild(pageDiv);
    });

    pagePanel.appendChild(fragment);
}

// Render overlays
export function renderOverlays(pageDiv, page, pageIdx) {
    const fragment = document.createDocumentFragment();

    page.blocks.forEach((block, blockIdx) => {
        const overlay = document.createElement('div');
        overlay.className = 'overlay';
        overlay.textContent = state.showTranslations ? block.translation : block.text;
        overlay.dataset.pageId = pageIdx;
        overlay.dataset.blockId = blockIdx;

        // bbox is [x, y, w, h] normalized (0-1), convert to percentage
        overlay.style.left = (block.bbox[0] * 100) + '%';
        overlay.style.top = (block.bbox[1] * 100) + '%';
        overlay.style.width = (block.bbox[2] * 100) + '%';
        overlay.style.height = (block.bbox[3] * 100) + '%';

        if (!state.showOverlays) {
            overlay.style.display = 'none';
        }

        fragment.appendChild(overlay);
    });

    pageDiv.appendChild(fragment);
}

// Render text list
export function renderTextList() {
    DOM.textList.innerHTML = '';

    const fragment = document.createDocumentFragment();

    state.currentManual.pages.forEach((page, pageIdx) => {
        page.blocks.forEach((block, blockIdx) => {
            const item = document.createElement('div');
            item.className = 'text-item';
            item.dataset.pageId = pageIdx;
            item.dataset.blockId = blockIdx;

            // Create header with original text and delete button
            const header = document.createElement('div');
            header.className = 'text-item-header';

            const original = document.createElement('div');
            original.className = 'text-original';
            original.textContent = block.text;

            const deleteBtn = document.createElement('button');
            deleteBtn.className = 'delete-btn hidden';
            deleteBtn.textContent = '🗑️';
            deleteBtn.title = 'Delete this text block';
            deleteBtn.onclick = (e) => {
                e.stopPropagation();
                if (confirm('Delete this text block? This will remove it from the manual.')) {
                    import('./editor.js').then(({ deleteTextBlock }) => {
                        deleteTextBlock(pageIdx, blockIdx);
                    });
                }
            };

            header.appendChild(original);
            header.appendChild(deleteBtn);

            const translation = document.createElement('div');
            translation.className = 'text-translation';
            translation.textContent = block.translation;
            if (!state.showTranslations) {
                translation.style.display = 'none';
            }

            // Add inline bbox editor
            const bboxEditor = createBboxEditor(block, pageIdx, blockIdx);

            item.appendChild(header);
            item.appendChild(translation);
            item.appendChild(bboxEditor);

            // Event listeners
            item.addEventListener('click', (e) => {
                if (!state.editMode) {
                    highlightBlock(pageIdx, blockIdx);
                    document.getElementById(`page-${pageIdx}`).scrollIntoView({ behavior: 'smooth', block: 'center' });
                }
                // In edit mode, don't scroll - let user focus on editing
            });

            item.addEventListener('mouseenter', () => {
                if (!state.editMode) {
                    highlightBlock(pageIdx, blockIdx);
                }
            });

            item.addEventListener('mouseleave', () => {
                if (!state.editMode) {
                    clearHighlights();
                }
            });

            fragment.appendChild(item);
        });
    });

    DOM.textList.appendChild(fragment);
}

// Create bbox editor
function createBboxEditor(block, pageIdx, blockIdx) {
    const bboxEditor = document.createElement('div');
    bboxEditor.className = 'bbox-editor';
    bboxEditor.innerHTML = `
        <div class="bbox-editor-grid">
            <div class="bbox-field">
                <label>X</label>
                <input type="number" class="bbox-x" step="0.01" min="0" max="1" value="${block.bbox[0].toFixed(2)}">
            </div>
            <div class="bbox-field">
                <label>Y</label>
                <input type="number" class="bbox-y" step="0.01" min="0" max="1" value="${block.bbox[1].toFixed(2)}">
            </div>
            <div class="bbox-field">
                <label>Width</label>
                <input type="number" class="bbox-w" step="0.01" min="0" max="1" value="${block.bbox[2].toFixed(2)}">
            </div>
            <div class="bbox-field">
                <label>Height</label>
                <input type="number" class="bbox-h" step="0.01" min="0" max="1" value="${block.bbox[3].toFixed(2)}">
            </div>
        </div>
    `;

    // Import editor functions dynamically
    bboxEditor.querySelectorAll('input').forEach(input => {
        input.addEventListener('focus', (e) => {
            e.stopPropagation();
            import('./editor.js').then(({ openBboxEditor }) => {
                openBboxEditor(pageIdx, blockIdx, bboxEditor);
            });
        });

        input.addEventListener('input', (e) => {
            e.stopPropagation();
            import('./editor.js').then(({ updateBboxLiveDebounced }) => {
                updateBboxLiveDebounced(pageIdx, blockIdx, bboxEditor);
            });
        });
    });

    bboxEditor.addEventListener('click', (e) => {
        e.stopPropagation();
        import('./editor.js').then(({ openBboxEditor }) => {
            openBboxEditor(pageIdx, blockIdx, bboxEditor);
        });
    });

    return bboxEditor;
}

// Highlight block
export function highlightBlock(pageIdx, blockIdx) {
    if (state.editMode) return;

    document.querySelectorAll('.highlight').forEach(el => el.classList.remove('highlight'));

    document.querySelectorAll(`[data-page-id="${pageIdx}"][data-block-id="${blockIdx}"]`).forEach(el => {
        el.classList.add('highlight');

        if (el.classList.contains('text-item')) {
            el.scrollIntoView({ behavior: 'smooth', block: 'center' });
        }
    });
}

// Clear highlights
export function clearHighlights() {
    if (state.editMode) return;
    document.querySelectorAll('.highlight').forEach(el => el.classList.remove('highlight'));
}

// Load manual
export async function loadManual(manualName) {
    const renderToken = ++state.currentRenderToken;

    // Reset edit mode state
    state.editMode = false;
    state.editedBlocks.clear();
    DOM.editModeBtn.textContent = '✏️ Edit Mode';
    DOM.editModeBtn.style.background = '';
    hideEditButtons();

    try {
        const response = await fetch(`../manuals/${manualName}/translations.json`);
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }

        const data = await response.json();

        if (renderToken !== state.currentRenderToken) {
            // Render cancelled - user navigated away
            return;
        }

        if (!data.meta || !Array.isArray(data.pages)) {
            throw new Error('Invalid translations.json structure - missing meta or pages');
        }

        state.currentManual = data;

        document.title = state.currentManual.meta.source;
        document.getElementById('manualTitle').textContent = state.currentManual.meta.source;

        const sourceLink = document.getElementById('sourceLink');
        if (state.currentManual.meta.source_url) {
            sourceLink.href = state.currentManual.meta.source_url;
            sourceLink.style.display = 'inline-flex';
        } else {
            sourceLink.style.display = 'none';
        }

        renderPages(manualName, renderToken);
        renderTextList();
    } catch (err) {
        ErrorHandler.network(err, `../manuals/${manualName}/translations.json`);
        document.getElementById('pagePanel').innerHTML = `<p style="padding: 2rem; color: red;">Failed to load: ${err.message}</p>`;
    }
}

// Helper functions
export function showEditButtons() {
    DOM.downloadEditsBtn.classList.remove('hidden');
    DOM.submitGitHubBtn.classList.remove('hidden');
}

export function hideEditButtons() {
    DOM.downloadEditsBtn.classList.add('hidden');
    DOM.submitGitHubBtn.classList.add('hidden');
}
