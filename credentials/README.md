# Google credentials

The translation worker needs a Google Cloud service account permitted to use
Document AI and Cloud Translation in the configured project.

1. Enable Document AI and Cloud Translation and create an OCR processor in the
   region you will use.
2. Create/download the service-account JSON key and save it here as
   `service-account.json`.
3. Copy the root `.env.example` to `.env`. Set `ProjectID`, `ProcessorID`, and
   `Location` to the matching project, processor and Document AI region.
4. Set `CREDENTIALS_PATH=credentials/service-account.json` and run commands from
   the repository root. An absolute credential path also works.

Translation LLM requests use `us-central1`; the Document AI `Location` setting
must match the OCR processor. OpenAI research/cleanup uses the separate optional
`OPENAI_API_KEY`, not this Google key.

Only this README is allowed through the credentials-directory Git ignore rule.
Keep the key and `.env` local. Website builds, unit tests and GitHub Pages do not
need production credentials.
