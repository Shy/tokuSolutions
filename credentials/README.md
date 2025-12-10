# Credentials

Google Cloud service account credentials for Document AI and Translation API.

## Setup

1. Create a service account in Google Cloud Console
2. Enable Document AI and Translation API
3. Download the JSON key file
4. Save as `service-account.json` in this directory
5. Update `.env` with: `CREDENTIALS_PATH=credentials/service-account.json`

## Security

- All files in this directory are git-ignored except this README
- Never commit credential files
- Rotate credentials regularly
