// Single Page Application for tokuSolutions

let manuals = [];
let currentManual = null;
let showOverlays = true;
let showTranslations = true;
let editMode = false;
let editedBlocks = new Set(); // Track edited blocks as "pageIdx-blockIdx"
let currentBboxEdit = null; // Track which block is being edited

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
    // Reset edit mode state
    editMode = false;
    editedBlocks.clear();
    document.getElementById('editModeBtn').textContent = '✏️ Edit Mode';
    document.getElementById('editModeBtn').style.background = '';
    document.getElementById('saveEditsBtn').classList.add('hidden');

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

        overlay.addEventListener('dblclick', () => {
            if (editMode) {
                // Find the corresponding text item and focus its bbox editor
                const textItem = document.querySelector(`.text-item[data-page-id="${pageIdx}"][data-block-id="${blockIdx}"]`);
                if (textItem) {
                    const bboxEditor = textItem.querySelector('.bbox-editor');
                    const firstInput = bboxEditor.querySelector('input');
                    if (firstInput) {
                        // Scroll text item into view
                        textItem.scrollIntoView({ behavior: 'smooth', block: 'center' });
                        // Focus the first input (this will trigger openBboxEditor)
                        setTimeout(() => firstInput.focus(), 300);
                    }
                }
            }
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

// Open a bbox editor (closes all others, highlights the overlay)
function openBboxEditor(pageIdx, blockIdx, bboxEditor) {
    // Close all other bbox editors
    document.querySelectorAll('.bbox-editor').forEach(editor => {
        if (editor !== bboxEditor) {
            editor.classList.remove('active');
        }
    });

    // Remove highlight from all overlays
    document.querySelectorAll('.overlay.bbox-editing').forEach(el => {
        el.classList.remove('bbox-editing');
    });

    // Mark this editor as active
    bboxEditor.classList.add('active');

    // Highlight the overlay being edited
    const overlay = document.querySelector(`.overlay[data-page-id="${pageIdx}"][data-block-id="${blockIdx}"]`);
    if (overlay) {
        overlay.classList.add('bbox-editing');
    }
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

            // Create header with original text
            const header = document.createElement('div');
            header.className = 'text-item-header';

            const original = document.createElement('div');
            original.className = 'text-original';
            original.textContent = block.text;

            header.appendChild(original);

            const translation = document.createElement('div');
            translation.className = 'text-translation';
            translation.textContent = block.translation;
            if (!showTranslations) {
                translation.style.display = 'none';
            }

            // Add inline bbox editor
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

            // Handle bbox editor interactions
            bboxEditor.querySelectorAll('input').forEach(input => {
                // Open this editor and close others when focused
                input.addEventListener('focus', (e) => {
                    e.stopPropagation();
                    openBboxEditor(pageIdx, blockIdx, bboxEditor);
                });

                // Live update bbox on input
                input.addEventListener('input', (e) => {
                    e.stopPropagation();
                    updateBboxLive(pageIdx, blockIdx, bboxEditor);
                });
            });

            // Also open on click anywhere in the editor
            bboxEditor.addEventListener('click', (e) => {
                e.stopPropagation();
                openBboxEditor(pageIdx, blockIdx, bboxEditor);
            });

            item.appendChild(header);
            item.appendChild(translation);
            item.appendChild(bboxEditor);

            item.addEventListener('click', () => {
                if (!editMode) {
                    highlightBlock(pageIdx, blockIdx);
                    document.getElementById(`page-${pageIdx}`).scrollIntoView({ behavior: 'smooth', block: 'center' });
                }
            });

            item.addEventListener('mouseenter', () => {
                if (!editMode) {
                    highlightBlock(pageIdx, blockIdx);
                    document.getElementById(`page-${pageIdx}`).scrollIntoView({ behavior: 'smooth', block: 'center' });
                }
            });

            item.addEventListener('mouseleave', () => {
                if (!editMode) {
                    clearHighlights();
                }
            });

            textList.appendChild(item);
        });
    });
}

// Highlight block
function highlightBlock(pageIdx, blockIdx) {
    // Don't highlight in edit mode
    if (editMode) return;

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
    // Don't clear highlights in edit mode (keep edited items yellow)
    if (editMode) return;

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

// Toggle edit mode
function toggleEditMode() {
    editMode = !editMode;
    const editModeBtn = document.getElementById('editModeBtn');
    const saveEditsBtn = document.getElementById('saveEditsBtn');

    if (editMode) {
        editModeBtn.textContent = '✓ Edit Mode';
        editModeBtn.style.background = '#48bb78';

        // Make all translation elements editable
        document.querySelectorAll('.text-translation').forEach(el => {
            el.contentEditable = 'plaintext-only';
            el.style.cursor = 'text';
            el.style.border = '1px dashed #cbd5e0';
            el.style.padding = '2px';

            // Listen for changes
            el.addEventListener('input', handleTranslationEdit);
            el.addEventListener('keydown', handleEditKeydown);
            el.addEventListener('paste', handlePaste);
        });

        // Show all bbox editors
        document.querySelectorAll('.bbox-editor').forEach(editor => {
            editor.style.display = 'block';
        });
    } else {
        editModeBtn.textContent = '✏️ Edit Mode';
        editModeBtn.style.background = '';

        // Make all translation elements non-editable
        document.querySelectorAll('.text-translation').forEach(el => {
            el.contentEditable = 'false';
            el.style.cursor = '';
            el.style.border = '';
            el.style.padding = '';
            el.removeEventListener('input', handleTranslationEdit);
            el.removeEventListener('keydown', handleEditKeydown);
            el.removeEventListener('paste', handlePaste);
        });

        // Hide all bbox editors and remove highlights
        document.querySelectorAll('.bbox-editor').forEach(editor => {
            editor.style.display = 'none';
            editor.classList.remove('active');
        });
        document.querySelectorAll('.overlay.bbox-editing').forEach(el => {
            el.classList.remove('bbox-editing');
        });
    }

    // Show/hide save button if there are edits
    if (editedBlocks.size > 0) {
        saveEditsBtn.classList.remove('hidden');
    } else {
        saveEditsBtn.classList.add('hidden');
    }
}

// Handle keyboard in edit mode
function handleEditKeydown(e) {
    // Enter without Shift = blur/save the field
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        e.target.blur();
    }
    // Shift+Enter = allow new line (default behavior)
}

// Handle paste to strip formatting
function handlePaste(e) {
    e.preventDefault();
    const text = (e.clipboardData || window.clipboardData).getData('text/plain');
    document.execCommand('insertText', false, text);
}

// Handle translation edit
function handleTranslationEdit(e) {
    const textItem = e.target.closest('.text-item');
    const pageIdx = parseInt(textItem.dataset.pageId);
    const blockIdx = parseInt(textItem.dataset.blockId);
    const blockKey = `${pageIdx}-${blockIdx}`;

    // Mark as edited
    editedBlocks.add(blockKey);
    textItem.classList.add('edited');
    textItem.style.background = '#fff8dc';

    // Update the actual data
    const newTranslation = e.target.textContent;
    currentManual.pages[pageIdx].blocks[blockIdx].translation = newTranslation;

    // Update overlay text and highlight it
    const overlay = document.querySelector(`.overlay[data-page-id="${pageIdx}"][data-block-id="${blockIdx}"]`);
    if (overlay) {
        if (showTranslations) {
            overlay.textContent = newTranslation;
        }

        // Highlight the overlay being edited
        document.querySelectorAll('.overlay.bbox-editing').forEach(el => {
            el.classList.remove('bbox-editing');
        });
        overlay.classList.add('bbox-editing');

        // Also deactivate any active bbox editors
        document.querySelectorAll('.bbox-editor.active').forEach(editor => {
            editor.classList.remove('active');
        });
    }

    // Show save button
    document.getElementById('saveEditsBtn').classList.remove('hidden');
}

// Live update bbox from inline editor
function updateBboxLive(pageIdx, blockIdx, bboxEditor) {
    const blockKey = `${pageIdx}-${blockIdx}`;

    // Get new values from inputs
    const x = parseFloat(bboxEditor.querySelector('.bbox-x').value);
    const y = parseFloat(bboxEditor.querySelector('.bbox-y').value);
    const w = parseFloat(bboxEditor.querySelector('.bbox-w').value);
    const h = parseFloat(bboxEditor.querySelector('.bbox-h').value);

    // Validate
    if (isNaN(x) || isNaN(y) || isNaN(w) || isNaN(h)) {
        return; // Skip invalid values
    }

    // Clamp values to 0-1 range
    const clampedX = Math.max(0, Math.min(1, x));
    const clampedY = Math.max(0, Math.min(1, y));
    const clampedW = Math.max(0, Math.min(1, w));
    const clampedH = Math.max(0, Math.min(1, h));

    // Update the data
    currentManual.pages[pageIdx].blocks[blockIdx].bbox = [clampedX, clampedY, clampedW, clampedH];

    // Mark as edited
    editedBlocks.add(blockKey);

    // Update overlay position in real-time
    const overlay = document.querySelector(`.overlay[data-page-id="${pageIdx}"][data-block-id="${blockIdx}"]`);
    if (overlay) {
        overlay.style.left = (clampedX * 100) + '%';
        overlay.style.top = (clampedY * 100) + '%';
        overlay.style.width = (clampedW * 100) + '%';
        overlay.style.height = (clampedH * 100) + '%';
    }

    // Show save button
    document.getElementById('saveEditsBtn').classList.remove('hidden');
}

// Download edited translations as JSON (fallback option)
function downloadEdits() {
    if (editedBlocks.size === 0) {
        alert('No edits to save');
        return;
    }

    // Offer choice: GitHub PR or download JSON
    const useGitHub = confirm(
        'Would you like to submit your edits via GitHub?\n\n' +
        'Click OK to create a pull request (requires GitHub account)\n' +
        'Click Cancel to download the JSON file instead'
    );

    if (useGitHub) {
        submitToGitHub();
    } else {
        downloadJSON();
    }
}

// Download JSON file (original behavior)
function downloadJSON() {
    const exportData = JSON.stringify(currentManual, null, 2);

    const blob = new Blob([exportData], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${currentManual.meta.name}-edited.json`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);

    alert(`Downloaded ${currentManual.meta.name}-edited.json with ${editedBlocks.size} edited blocks`);
}

// Submit edits via GitHub PR
async function submitToGitHub() {
    const GITHUB_REPO = 'shy/tokuSolutions'; // Update with your repo
    const BRANCH_NAME = `edit-${currentManual.meta.name}-${Date.now()}`;
    const FILE_PATH = `output/${currentManual.meta.name}/translations.json`;

    try {
        // Check if user is authenticated
        let token = localStorage.getItem('github_token');

        if (!token) {
            // Use GitHub Device Flow (no backend needed!)
            const clientId = 'Ov23livUs1jiLOnUsIUN';

            // Save state for after auth
            localStorage.setItem('pending_edit', JSON.stringify({
                manual: currentManual,
                blocks: Array.from(editedBlocks)
            }));

            await authenticateWithGitHub(clientId);
            return;
        }

        // Create Octokit instance
        const octokit = new Octokit.Octokit({ auth: token });
        const [owner, repo] = GITHUB_REPO.split('/');

        alert('Creating fork and submitting pull request...\nThis may take a moment.');

        // Get authenticated user
        const { data: user } = await octokit.rest.users.getAuthenticated();
        const forkOwner = user.login;

        // Fork repository (will use existing fork if already exists)
        try {
            await octokit.rest.repos.createFork({
                owner,
                repo
            });
            // Wait a moment for fork to initialize
            await new Promise(resolve => setTimeout(resolve, 2000));
        } catch (error) {
            // Fork may already exist, continue
            console.log('Fork may already exist:', error.message);
        }

        // Get the default branch SHA from the fork
        const { data: forkRepo } = await octokit.rest.repos.get({
            owner: forkOwner,
            repo
        });
        const defaultBranch = forkRepo.default_branch;

        const { data: refData } = await octokit.rest.git.getRef({
            owner: forkOwner,
            repo,
            ref: `heads/${defaultBranch}`
        });

        // Create new branch
        await octokit.rest.git.createRef({
            owner: forkOwner,
            repo,
            ref: `refs/heads/${BRANCH_NAME}`,
            sha: refData.object.sha
        });

        // Get current file SHA
        const { data: fileData } = await octokit.rest.repos.getContent({
            owner: forkOwner,
            repo,
            path: FILE_PATH,
            ref: BRANCH_NAME
        });

        // Update file with new content
        const jsonContent = JSON.stringify(currentManual, null, 2);
        const encoder = new TextEncoder();
        const utf8Bytes = encoder.encode(jsonContent);
        const base64Content = btoa(String.fromCharCode(...utf8Bytes));

        await octokit.rest.repos.createOrUpdateFileContents({
            owner: forkOwner,
            repo,
            path: FILE_PATH,
            message: `Update translations for ${currentManual.meta.source}`,
            content: base64Content,
            sha: fileData.sha,
            branch: BRANCH_NAME
        });

        // Create pull request
        const { data: pr } = await octokit.rest.pulls.create({
            owner,
            repo,
            title: `Translation edits for ${currentManual.meta.source}`,
            head: `${forkOwner}:${BRANCH_NAME}`,
            base: 'main',
            body: `Community translation edits for **${currentManual.meta.source}**\n\n` +
                  `### Changes\n` +
                  `- Edited ${editedBlocks.size} text block(s)\n\n` +
                  `---\n` +
                  `*Submitted via [TokuSolutions](https://toku.solutions) inline editor*`
        });

        alert(
            `Pull request created successfully!\n\n` +
            `PR #${pr.number}: ${pr.html_url}\n\n` +
            `Thank you for contributing!`
        );

        // Clear edited blocks
        editedBlocks.clear();
        document.getElementById('saveEditsBtn').classList.add('hidden');

    } catch (error) {
        console.error('GitHub submission error:', error);
        alert(
            `Failed to submit to GitHub: ${error.message}\n\n` +
            `Would you like to download the JSON instead?`
        );
        if (confirm('Download JSON file?')) {
            downloadJSON();
        }
    }
}

// Authenticate with GitHub using Device Flow (no backend needed)
async function authenticateWithGitHub(clientId) {
    try {
        // Step 1: Request device code
        const deviceResponse = await fetch('https://github.com/login/device/code', {
            method: 'POST',
            headers: {
                'Accept': 'application/json',
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                client_id: clientId,
                scope: 'public_repo'
            })
        });

        const deviceData = await deviceResponse.json();

        // Show user the code and open GitHub authorization
        const userConfirmed = confirm(
            `GitHub Authorization Required\n\n` +
            `1. Click OK to open GitHub\n` +
            `2. Enter this code: ${deviceData.user_code}\n` +
            `3. Authorize the app\n\n` +
            `The code will expire in ${Math.floor(deviceData.expires_in / 60)} minutes.`
        );

        if (!userConfirmed) {
            alert('Authorization cancelled. You can still download the JSON file instead.');
            return;
        }

        // Open GitHub authorization page
        window.open(deviceData.verification_uri, '_blank');

        // Step 2: Poll for access token
        const interval = deviceData.interval * 1000;
        const maxAttempts = Math.floor(deviceData.expires_in / deviceData.interval);

        for (let attempt = 0; attempt < maxAttempts; attempt++) {
            await new Promise(resolve => setTimeout(resolve, interval));

            const tokenResponse = await fetch('https://github.com/login/oauth/access_token', {
                method: 'POST',
                headers: {
                    'Accept': 'application/json',
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    client_id: clientId,
                    device_code: deviceData.device_code,
                    grant_type: 'urn:ietf:params:oauth:grant-type:device_code'
                })
            });

            const tokenData = await tokenResponse.json();

            if (tokenData.access_token) {
                // Success!
                localStorage.setItem('github_token', tokenData.access_token);

                alert('GitHub authorization successful! Now submitting your edits...');

                // Resume pending edit
                const pendingEdit = localStorage.getItem('pending_edit');
                if (pendingEdit) {
                    const data = JSON.parse(pendingEdit);
                    currentManual = data.manual;
                    editedBlocks = new Set(data.blocks);
                    localStorage.removeItem('pending_edit');

                    // Re-trigger submission
                    await submitToGitHub();
                }
                return;
            }

            if (tokenData.error === 'authorization_pending') {
                // Still waiting for user to authorize
                continue;
            }

            if (tokenData.error === 'slow_down') {
                // Increase polling interval
                await new Promise(resolve => setTimeout(resolve, interval));
                continue;
            }

            // Other error
            throw new Error(tokenData.error_description || tokenData.error);
        }

        throw new Error('Authorization timeout - please try again');

    } catch (error) {
        console.error('GitHub auth error:', error);
        alert(
            `GitHub authorization failed: ${error.message}\n\n` +
            `You can still download the JSON file and submit manually.`
        );
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

    // Edit mode toggle
    document.getElementById('editModeBtn').addEventListener('click', toggleEditMode);

    // Save edits button
    document.getElementById('saveEditsBtn').addEventListener('click', downloadEdits);

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
