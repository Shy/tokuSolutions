// GitHub integration module
import { GITHUB_CONFIG } from './config.js?v=18';
import { state, EditSession } from './state.js?v=18';
import { ErrorHandler } from './errors.js?v=18';
import { serializeManual } from './manual-data.js?v=18';

// Submit edits via GitHub PR (Hybrid Approach: Copy JSON + Open GitHub Editor)
export async function submitToGitHub(currentManual) {
  if (!state.editSession?.dirty) {
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
  const jsonContent = serializeManual(currentManual);

  // Show instructions modal
  showSubmitInstructions(jsonContent, owner, repo, FILE_PATH, manualName);
}

// Show submit instructions modal
function showSubmitInstructions(jsonContent, owner, repo, filePath, manualName) {
  const backdrop = document.createElement('div');
  backdrop.className = 'submit-dialog-backdrop';
  const modal = document.createElement('div');
  modal.className = 'submit-dialog';
  modal.innerHTML = `
    <h2>Submit Changes</h2>
    <p>
      Changes are submitted through
      <a href="https://docs.github.com/en/pull-requests/collaborating-with-pull-requests/proposing-changes-to-your-work-with-pull-requests/about-pull-requests" target="_blank" rel="noreferrer">GitHub Pull Requests</a>.
    </p>
    <div class="submit-step">
      <strong>Step 1: Copy your changes</strong>
      <button id="copyJsonBtn" class="btn btn-success" type="button">Copy JSON</button>
      <span id="copyStatus" class="submit-copy-status hidden">Copied!</span>
    </div>
    <div class="submit-step">
      <strong>Step 2: Open the file editor and paste</strong>
      <button id="openGithubBtn" class="btn" type="button">Open in GitHub</button>
    </div>
    <div class="dialog-actions">
      <button id="closeModalBtn" class="btn btn-secondary" type="button">Close</button>
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
      copyStatus.classList.remove('hidden');
      copyBtn.textContent = 'Copied!';
      setTimeout(() => {
        copyBtn.textContent = 'Copy JSON';
        copyStatus.classList.add('hidden');
      }, 2000);
    } catch (err) {
      // Fallback for older browsers
      const textarea = document.createElement('textarea');
      textarea.value = jsonContent;
      textarea.className = 'clipboard-fallback';
      document.body.appendChild(textarea);
      textarea.select();
      document.execCommand('copy');
      document.body.removeChild(textarea);

      copyStatus.classList.remove('hidden');
      copyBtn.textContent = 'Copied!';
      setTimeout(() => {
        copyBtn.textContent = 'Copy JSON';
        copyStatus.classList.add('hidden');
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
  if (!state.editSession?.dirty) {
    ErrorHandler.user('No edits to save');
    return;
  }

  const manualName = state.currentManualName || 'manual';

  const jsonContent = serializeManual(currentManual);
  const blob = new Blob([jsonContent], { type: 'application/json' });
  const url = URL.createObjectURL(blob);

  const a = document.createElement('a');
  a.href = url;
  a.download = `${manualName}-translations.json`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);

  state.editSession.markClean();
  alert('JSON file downloaded successfully!\n\nYou can now submit this file to GitHub manually.');
}
