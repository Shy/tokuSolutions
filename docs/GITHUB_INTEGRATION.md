# GitHub Integration Setup

The TokuSolutions editor supports community contributions via GitHub pull requests. Users can submit their translation edits directly through the web interface.

## How It Works (GitHub Device Flow)

**The integration is fully configured and ready to use!** No backend required.

When users click "Download Edits" → "Submit via GitHub":

1. **First time only**: GitHub shows them a device code
2. They enter the code at github.com/login/device
3. They authorize the TokuSolutions app
4. The system automatically creates a fork, branch, and pull request
5. Future edits reuse the saved token (no code needed)

This uses GitHub's Device Flow OAuth, which works perfectly with static sites like GitHub Pages.

## Full OAuth Setup (Recommended for Production)

For a better user experience without exposing tokens:

### 1. Create GitHub OAuth App

1. Go to GitHub Settings → Developer settings → [OAuth Apps](https://github.com/settings/developers)
2. Click "New OAuth App"
3. Fill in:
   - **Application name**: TokuSolutions Editor
   - **Homepage URL**: `https://toku.solutions`
   - **Authorization callback URL**: `https://toku.solutions`
4. Save and note your **Client ID** and **Client Secret**

### 2. Update Client ID

Edit `output/app.js` and replace:
```javascript
const clientId = 'YOUR_GITHUB_OAUTH_CLIENT_ID';
```

With your actual Client ID.

### 3. Set Up OAuth Proxy

Since GitHub OAuth requires a client secret (which cannot be exposed in frontend code), you need a backend proxy to exchange the authorization code for an access token.

#### Option A: Netlify Functions

Create `.netlify/functions/github-oauth.js`:

```javascript
exports.handler = async (event) => {
  const { code } = JSON.parse(event.body);

  const response = await fetch('https://github.com/login/oauth/access_token', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Accept': 'application/json'
    },
    body: JSON.stringify({
      client_id: process.env.GITHUB_CLIENT_ID,
      client_secret: process.env.GITHUB_CLIENT_SECRET,
      code: code
    })
  });

  const data = await response.json();

  return {
    statusCode: 200,
    body: JSON.stringify({ access_token: data.access_token })
  };
};
```

Set environment variables in Netlify dashboard:
- `GITHUB_CLIENT_ID`
- `GITHUB_CLIENT_SECRET`

#### Option B: GitHub Actions + Cloudflare Workers

(See separate guide if needed)

#### Option C: Simple Backend

Any backend that can:
1. Accept POST with `{ code: "..." }`
2. Exchange code with GitHub
3. Return `{ access_token: "..." }`

### 4. Update OAuth Handler

Modify `handleGitHubCallback()` in `app.js` to call your proxy:

```javascript
if (code && state === 'edit_mode') {
  const response = await fetch('/.netlify/functions/github-oauth', {
    method: 'POST',
    body: JSON.stringify({ code })
  });

  const { access_token } = await response.json();
  localStorage.setItem('github_token', access_token);

  // Resume pending edit...
}
```

## How It Works

1. User makes edits in the inline editor
2. Clicks "Download Edits" → chooses "Submit via GitHub"
3. If not authenticated:
   - Redirects to GitHub OAuth
   - Returns to site with auth code
   - Exchanges code for token
4. With token:
   - Forks the repository (if not already forked)
   - Creates a new branch with timestamp
   - Updates `translations.json` in the fork
   - Creates a pull request to the main repo
5. You review and merge PRs as needed

## Repository Configuration

Update the repository name in `app.js`:

```javascript
const GITHUB_REPO = 'YOUR_USERNAME/YOUR_REPO'; // e.g., 'shy/tokuSolutions'
```

## Testing

1. Make some edits in a manual
2. Click "Download Edits"
3. Choose "Submit via GitHub"
4. Enter your token or complete OAuth flow
5. Check that a PR is created in your repository

## Troubleshooting

### "Failed to create fork"
- Check token has `public_repo` scope
- Verify repository name is correct
- User may have already forked (this is OK, will use existing fork)

### "Failed to create branch"
- Fork may need time to initialize (wait 30 seconds and retry)
- Check that main branch exists in fork

### "Failed to create pull request"
- Verify base repository name is correct
- Check that branch exists in fork
- User may have already created PR from this branch

## Security Notes

- **Never commit GitHub client secrets** to the repository
- Use environment variables for all secrets
- Personal access tokens are stored in localStorage (client-side only)
- Consider adding token expiration and refresh logic
- Rate limiting: GitHub API has limits, monitor usage

## Alternative: Manual PR Workflow

If you don't want to set up OAuth, users can still:
1. Download the JSON file
2. Fork the repository manually
3. Upload the file via GitHub web interface
4. Create a PR manually

This is the fallback when clicking "Cancel" on the GitHub submission dialog.
