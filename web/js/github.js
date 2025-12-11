// GitHub integration module
import { GITHUB_CONFIG } from './config.js';
import { state, EditSession } from './state.js';
import { ErrorHandler } from './errors.js';

// Submit edits via GitHub PR (Hybrid Approach: Copy JSON + Open GitHub Editor)
export async function submitToGitHub(currentManual) {
    if (state.editedBlocks.size === 0) {
        ErrorHandler.user('No edits to save');
        return;
    }

    const manualName = state.currentManualName;
    if (!manualName) {
        ErrorHandler.user('Could not determine manual name');
        return;
    }

    const FILE_PATH = `manuals/${manualName}/translations.json`;
    const [owner, repo] = GITHUB_CONFIG.REPO.split('/');

    // Generate JSON content
    const jsonContent = JSON.stringify(currentManual, null, 2);

    // Show instructions modal
    showSubmitInstructions(jsonContent, owner, repo, FILE_PATH, manualName);
}

// Show submit instructions modal
function showSubmitInstructions(jsonContent, owner, repo, filePath, manualName) {
    // Create modal backdrop
    const backdrop = document.createElement('div');
    backdrop.style.cssText = `
        position: fixed;
        top: 0;
        left: 0;
        right: 0;
        bottom: 0;
        background: rgba(0, 0, 0, 0.7);
        display: flex;
        align-items: center;
        justify-content: center;
        z-index: 10000;
    `;

    // Create modal
    const modal = document.createElement('div');
    modal.style.cssText = `
        background: white;
        padding: 2rem;
        border-radius: 8px;
        max-width: 600px;
        width: 90%;
        box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
    `;

    // Create content
    modal.innerHTML = `
        <h2 style="margin-top: 0; color: #2d3748;">Submit to GitHub</h2>

        <p style="color: #4a5568; line-height: 1.6;">
            Follow these steps to submit your translation edits:
        </p>

        <div style="background: #f7fafc; padding: 1rem; border-radius: 4px; margin: 1rem 0;">
            <strong style="color: #2d3748;">Step 1: Copy JSON to clipboard</strong>
            <button id="copyJsonBtn" style="
                display: block;
                margin-top: 0.5rem;
                padding: 0.5rem 1rem;
                background: #48bb78;
                color: white;
                border: none;
                border-radius: 4px;
                cursor: pointer;
                font-size: 0.9rem;
            ">📋 Copy JSON</button>
            <span id="copyStatus" style="color: #48bb78; margin-left: 0.5rem; display: none;">✓ Copied!</span>
        </div>

        <div style="background: #f7fafc; padding: 1rem; border-radius: 4px; margin: 1rem 0;">
            <strong style="color: #2d3748;">Step 2: Open GitHub editor</strong>
            <button id="openGithubBtn" style="
                display: block;
                margin-top: 0.5rem;
                padding: 0.5rem 1rem;
                background: #4299e1;
                color: white;
                border: none;
                border-radius: 4px;
                cursor: pointer;
                font-size: 0.9rem;
            ">🔗 Open GitHub</button>
        </div>

        <div style="background: #edf2f7; padding: 1rem; border-radius: 4px; margin: 1rem 0;">
            <strong style="color: #2d3748;">In GitHub:</strong>
            <ol style="margin: 0.5rem 0 0 1.5rem; color: #4a5568; line-height: 1.8;">
                <li>Paste the JSON (Ctrl+V or Cmd+V)</li>
                <li>Scroll down and enter branch name: <code style="background: #cbd5e0; padding: 0.1rem 0.3rem; border-radius: 2px;">edit-${manualName}</code></li>
                <li>Click "Propose changes"</li>
                <li>Click "Create pull request"</li>
            </ol>
        </div>

        <div style="display: flex; gap: 1rem; margin-top: 1.5rem;">
            <button id="closeModalBtn" style="
                flex: 1;
                padding: 0.75rem;
                background: #e2e8f0;
                color: #2d3748;
                border: none;
                border-radius: 4px;
                cursor: pointer;
                font-size: 0.9rem;
            ">Close</button>
        </div>
    `;

    backdrop.appendChild(modal);
    document.body.appendChild(backdrop);

    // Copy JSON button
    const copyBtn = modal.querySelector('#copyJsonBtn');
    const copyStatus = modal.querySelector('#copyStatus');
    copyBtn.addEventListener('click', async () => {
        try {
            await navigator.clipboard.writeText(jsonContent);
            copyStatus.style.display = 'inline';
            copyBtn.textContent = '✓ Copied!';
            setTimeout(() => {
                copyBtn.textContent = '📋 Copy JSON';
                copyStatus.style.display = 'none';
            }, 2000);
        } catch (err) {
            // Fallback for older browsers
            const textarea = document.createElement('textarea');
            textarea.value = jsonContent;
            textarea.style.position = 'fixed';
            textarea.style.opacity = '0';
            document.body.appendChild(textarea);
            textarea.select();
            document.execCommand('copy');
            document.body.removeChild(textarea);

            copyStatus.style.display = 'inline';
            copyBtn.textContent = '✓ Copied!';
            setTimeout(() => {
                copyBtn.textContent = '📋 Copy JSON';
                copyStatus.style.display = 'none';
            }, 2000);
        }
    });

    // Open GitHub button
    const openBtn = modal.querySelector('#openGithubBtn');
    openBtn.addEventListener('click', () => {
        const githubUrl = `https://github.com/${owner}/${repo}/edit/main/${filePath}`;
        window.open(githubUrl, '_blank');
    });

    // Close modal
    const closeBtn = modal.querySelector('#closeModalBtn');
    closeBtn.addEventListener('click', () => {
        document.body.removeChild(backdrop);
    });

    // Close on backdrop click
    backdrop.addEventListener('click', (e) => {
        if (e.target === backdrop) {
            document.body.removeChild(backdrop);
        }
    });
}

// Download JSON file (alternative to GitHub submission)
export function downloadJSON(currentManual) {
    if (state.editedBlocks.size === 0) {
        ErrorHandler.user('No edits to save');
        return;
    }

    const manualName = state.currentManualName || 'manual';

    const jsonContent = JSON.stringify(currentManual, null, 2);
    const blob = new Blob([jsonContent], { type: 'application/json' });
    const url = URL.createObjectURL(blob);

    const a = document.createElement('a');
    a.href = url;
    a.download = `${manualName}-translations.json`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);

    EditSession.markClean();
    alert('JSON file downloaded successfully!\n\nYou can now submit this file to GitHub manually.');
}
