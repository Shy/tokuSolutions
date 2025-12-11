// GitHub integration module
import { GITHUB_CONFIG, UI_TIMINGS } from './config.js';
import { state, EditSession } from './state.js';
import { ErrorHandler } from './errors.js';

// Submit edits via GitHub PR
export async function submitToGitHub(currentManual) {
    if (state.editedBlocks.size === 0) {
        ErrorHandler.user('No edits to save');
        return;
    }

    const BRANCH_NAME = `edit-${currentManual.meta.name}-${Date.now()}`;
    const FILE_PATH = `manuals/${currentManual.meta.name}/translations.json`;

    try {
        let token = localStorage.getItem('github_token');

        if (!token) {
            // Save state for after auth
            localStorage.setItem('pending_edit', JSON.stringify({
                manual: currentManual,
                blocks: Array.from(state.editedBlocks)
            }));

            await authenticateWithGitHub(GITHUB_CONFIG.CLIENT_ID);
            return;
        }

        const octokit = new Octokit.Octokit({ auth: token });
        const [owner, repo] = GITHUB_CONFIG.REPO.split('/');

        alert('Creating fork and submitting pull request...\nThis may take a moment.');

        const { data: user } = await octokit.rest.users.getAuthenticated();
        const forkOwner = user.login;

        // Fork repository
        try {
            await octokit.rest.repos.createFork({ owner, repo });
            await new Promise(resolve => setTimeout(resolve, UI_TIMINGS.FORK_WAIT_TIME));
        } catch (error) {
            // Fork may already exist - continue with workflow
        }

        // Get default branch
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

        // Update file
        const jsonContent = JSON.stringify(currentManual, null, 2);
        // Use btoa with proper UTF-8 encoding (handles Unicode and large files)
        const base64Content = btoa(unescape(encodeURIComponent(jsonContent)));

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
                  `- Edited ${state.editedBlocks.size} text block(s)\n\n` +
                  `---\n` +
                  `*Submitted via [TokuSolutions](https://toku.solutions) inline editor*`
        });

        alert(
            `Pull request created successfully!\n\n` +
            `PR #${pr.number}: ${pr.html_url}\n\n` +
            `Thank you for contributing!`
        );

        state.editedBlocks.clear();
        EditSession.markClean();
        return true;

    } catch (error) {
        ErrorHandler.github(error, 'Creating pull request');
        return false;
    }
}

// Authenticate with GitHub using Device Flow
async function authenticateWithGitHub(clientId) {
    try {
        const params = new URLSearchParams({
            client_id: clientId,
            scope: 'public_repo'
        });

        const deviceResponse = await fetch('https://github.com/login/device/code', {
            method: 'POST',
            headers: { 'Accept': 'application/json' },
            body: params
        });

        const deviceData = await deviceResponse.json();

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

        window.open(deviceData.verification_uri, '_blank');

        // Poll for access token
        const interval = deviceData.interval * 1000;
        const maxAttempts = Math.floor(deviceData.expires_in / deviceData.interval);

        for (let attempt = 0; attempt < maxAttempts; attempt++) {
            await new Promise(resolve => setTimeout(resolve, interval));

            const tokenParams = new URLSearchParams({
                client_id: clientId,
                device_code: deviceData.device_code,
                grant_type: 'urn:ietf:params:oauth:grant-type:device_code'
            });

            const tokenResponse = await fetch('https://github.com/login/oauth/access_token', {
                method: 'POST',
                headers: { 'Accept': 'application/json' },
                body: tokenParams
            });

            const tokenData = await tokenResponse.json();

            if (tokenData.access_token) {
                localStorage.setItem('github_token', tokenData.access_token);
                alert('GitHub authorization successful! Now submitting your edits...');

                // Resume pending edit
                const pendingEdit = localStorage.getItem('pending_edit');
                if (pendingEdit) {
                    const data = JSON.parse(pendingEdit);
                    localStorage.removeItem('pending_edit');
                    await submitToGitHub(data.manual);
                }
                return;
            }

            if (tokenData.error === 'authorization_pending') continue;
            if (tokenData.error === 'slow_down') {
                await new Promise(resolve => setTimeout(resolve, interval));
                continue;
            }

            throw new Error(tokenData.error_description || tokenData.error);
        }

        throw new Error('Authorization timeout - please try again');

    } catch (error) {
        ErrorHandler.github(error, 'GitHub authentication');
    }
}

// Download JSON file
export function downloadJSON(currentManual) {
    if (state.editedBlocks.size === 0) {
        ErrorHandler.user('No edits to save');
        return;
    }

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

    EditSession.markClean();

    alert(`Downloaded ${currentManual.meta.name}-edited.json with ${state.editedBlocks.size} edited blocks`);
}
