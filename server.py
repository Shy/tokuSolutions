"""Flask server for PDF translation viewer."""

import os
import asyncio
from pathlib import Path

from flask import Flask, jsonify, send_from_directory, request, abort
from temporalio.client import Client

from docai_workflow import DocAITranslateWorkflow, DocAITranslateInput

app = Flask(__name__)

# Directories
OUTPUT_DIR = Path("output")
DOCUMENTS_DIR = Path("Documents")


def get_temporal_client():
    """Get a Temporal client (creates new event loop if needed)."""
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(Client.connect("localhost:7233"))


@app.route("/")
def index():
    """List all available translations."""
    translations = []
    if OUTPUT_DIR.exists():
        for item in sorted(OUTPUT_DIR.iterdir()):
            if item.is_dir() and (item / "translations.json").exists():
                translations.append(
                    {
                        "name": item.name,
                        "path": f"/view/{item.name}",
                    }
                )

    # List available PDFs to translate
    pdfs = []
    if DOCUMENTS_DIR.exists():
        for pdf in sorted(DOCUMENTS_DIR.glob("*.pdf")):
            pdfs.append(
                {
                    "name": pdf.name,
                    "translated": (
                        OUTPUT_DIR / pdf.stem / "translations.json"
                    ).exists(),
                }
            )

    return f"""<!DOCTYPE html>
<html>
<head>
    <title>PDF Translation Viewer</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background: #f5f3eb;
            padding: 2rem;
        }}
        h1 {{ margin-bottom: 1.5rem; color: #333; }}
        h2 {{ margin: 1.5rem 0 1rem; color: #555; font-size: 1.1rem; }}
        .container {{ max-width: 800px; margin: 0 auto; }}
        .list {{
            background: white;
            border-radius: 8px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
            overflow: hidden;
        }}
        .item {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 1rem 1.25rem;
            border-bottom: 1px solid #eee;
        }}
        .item:last-child {{ border-bottom: none; }}
        .item a {{
            color: #3182ce;
            text-decoration: none;
            font-weight: 500;
        }}
        .item a:hover {{ text-decoration: underline; }}
        .btn {{
            background: #4a5568;
            color: white;
            border: none;
            padding: 0.4rem 0.8rem;
            border-radius: 4px;
            cursor: pointer;
            font-size: 0.85rem;
        }}
        .btn:hover {{ background: #2d3748; }}
        .btn:disabled {{ background: #a0aec0; cursor: not-allowed; }}
        .btn-primary {{ background: #3182ce; }}
        .btn-primary:hover {{ background: #2c5282; }}
        .status {{ font-size: 0.8rem; color: #718096; }}
        .status.done {{ color: #38a169; }}
        .empty {{ padding: 2rem; text-align: center; color: #718096; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>PDF Translation Viewer</h1>

        <h2>Translated Documents</h2>
        <div class="list">
            {"".join(f'<div class="item"><a href="{t["path"]}">{t["name"]}</a></div>' for t in translations) or '<div class="empty">No translations yet</div>'}
        </div>

        <h2>Available PDFs</h2>
        <div class="list">
            {"".join(f'''<div class="item">
                <span>{p["name"]}</span>
                <span>
                    <span class="status {"done" if p["translated"] else ""}">{("Translated" if p["translated"] else "Not translated")}</span>
                    <button class="btn btn-primary" onclick="translate('{p["name"]}')" {"disabled" if p["translated"] else ""}>
                        {("View" if p["translated"] else "Translate")}
                    </button>
                </span>
            </div>''' for p in pdfs) or '<div class="empty">No PDFs found in Documents/</div>'}
        </div>
    </div>

    <script>
        async function translate(filename) {{
            const btn = event.target;
            btn.disabled = true;
            btn.textContent = 'Starting...';

            try {{
                const res = await fetch('/api/translate', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{ filename }})
                }});
                const data = await res.json();

                if (data.success) {{
                    window.location.href = data.view_url;
                }} else {{
                    alert('Translation failed: ' + data.error);
                    btn.disabled = false;
                    btn.textContent = 'Translate';
                }}
            }} catch (e) {{
                alert('Error: ' + e.message);
                btn.disabled = false;
                btn.textContent = 'Translate';
            }}
        }}
    </script>
</body>
</html>"""


@app.route("/view/<name>")
def view_translation(name):
    """Serve the translation viewer."""
    output_path = OUTPUT_DIR / name
    if not output_path.exists():
        abort(404)
    return send_from_directory(output_path, "index.html")


@app.route("/view/<name>/<path:filename>")
def serve_translation_file(name, filename):
    """Serve static files for a translation."""
    output_path = OUTPUT_DIR / name
    if not output_path.exists():
        abort(404)
    return send_from_directory(output_path, filename)


@app.route("/api/translations")
def list_translations():
    """List all translations as JSON."""
    translations = []
    if OUTPUT_DIR.exists():
        for item in sorted(OUTPUT_DIR.iterdir()):
            if item.is_dir() and (item / "translations.json").exists():
                translations.append(
                    {
                        "name": item.name,
                        "path": f"/view/{item.name}",
                    }
                )
    return jsonify(translations)


@app.route("/api/translate", methods=["POST"])
def start_translation():
    """Start a translation workflow."""
    data = request.get_json()
    filename = data.get("filename")

    if not filename:
        return jsonify({"success": False, "error": "No filename provided"}), 400

    pdf_path = DOCUMENTS_DIR / filename
    if not pdf_path.exists():
        return jsonify({"success": False, "error": f"File not found: {filename}"}), 404

    try:
        client = get_temporal_client()

        # Run the workflow
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(
            client.execute_workflow(
                DocAITranslateWorkflow.run,
                DocAITranslateInput(pdf_path=str(pdf_path)),
                id=f"translate-{pdf_path.stem}",
                task_queue="pdf-translation",
            )
        )

        if result.success:
            return jsonify(
                {
                    "success": True,
                    "view_url": f"/view/{pdf_path.stem}",
                    "output_dir": result.output_dir,
                }
            )
        else:
            return jsonify(
                {
                    "success": False,
                    "error": result.error,
                }
            )

    except Exception as e:
        return (
            jsonify(
                {
                    "success": False,
                    "error": str(e),
                }
            ),
            500,
        )


if __name__ == "__main__":
    print("Starting Flask server...")
    print("Make sure the Temporal worker is running: uv run python worker.py")
    print("\nServer: http://localhost:5001")
    app.run(host="0.0.0.0", port=5001, debug=True)
