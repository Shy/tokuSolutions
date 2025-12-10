"""Optimized HTML templates for tokuSolutions."""


def generate_manual_viewer_html(title: str, source_url: str = "") -> str:
    """Generate minimal HTML that loads translations.json dynamically."""

    source_link = (
        f'<a href="{source_url}" class="btn" target="_blank">📄 Original</a>'
        if source_url
        else ""
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    <link rel="stylesheet" href="../styles.css">
    <style>
        /* Manual viewer specific styles */
        .container {{
            display: flex;
            height: calc(100vh - 60px);
        }}
        .page-panel {{
            flex: 1;
            overflow-y: auto;
            padding: 1.5rem;
            background: #eae8e0;
        }}
        .page {{
            position: relative;
            margin-bottom: 1.5rem;
            background: #fff;
            border-radius: 4px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
        }}
        .page img {{
            width: 100%;
            display: block;
            border-radius: 4px;
        }}
        .overlay {{
            position: absolute;
            background: rgba(255,255,255,0.85);
            padding: 2px;
            font-size: 10px;
            line-height: 1.15;
            color: #000;
            cursor: pointer;
            transition: all 0.15s;
            border: 1px solid transparent;
            overflow: hidden;
            white-space: pre-wrap;
            word-break: break-word;
        }}
        .overlay:hover, .overlay.highlight {{
            background: rgba(255,248,220,0.98);
            border-color: #d69e2e;
            z-index: 20;
            box-shadow: 0 0 0 2px #d69e2e;
        }}
        .page-label {{
            position: absolute;
            bottom: 8px;
            right: 8px;
            background: rgba(0,0,0,0.6);
            color: #fff;
            padding: 3px 10px;
            border-radius: 3px;
            font-size: 0.8rem;
        }}
        .text-panel {{
            width: 380px;
            min-width: 320px;
            background: #fff;
            border-left: 1px solid #ddd;
            display: flex;
            flex-direction: column;
            overflow-y: auto;
        }}
        .text-panel-header {{
            padding: 0.75rem 1rem;
            border-bottom: 1px solid #eee;
            background: #fafafa;
        }}
        .text-panel-header h2 {{
            font-size: 0.9rem;
            font-weight: 600;
            color: #555;
        }}
        .text-item {{
            padding: 0.75rem 1rem;
            border-bottom: 1px solid #f0f0f0;
            cursor: pointer;
            transition: background 0.15s;
        }}
        .text-item:hover {{
            background: #f9f9f9;
        }}
        .text-item.highlight {{
            background: #fff8dc;
            border-left: 3px solid #d69e2e;
        }}
        .text-original {{
            font-size: 0.85rem;
            color: #333;
            margin-bottom: 0.25rem;
        }}
        .text-translation {{
            font-size: 0.8rem;
            color: #666;
        }}
    </style>
</head>
<body>
    <header>
        <h1>{title}</h1>
        <div class="controls">
            <a href="../index.html" class="btn">← Back</a>
            {source_link}
            <label>
                <input type="checkbox" id="toggleOverlays" checked>
                Show overlays
            </label>
            <label>
                <input type="checkbox" id="toggleTranslation" checked>
                Show translation
            </label>
        </div>
    </header>

    <div class="container">
        <div class="page-panel" id="pagePanel"></div>
        <div class="text-panel">
            <div class="text-panel-header">
                <h2>Translations</h2>
            </div>
            <div id="textList"></div>
        </div>
    </div>

    <script>
        let translationsData = null;
        let showOverlays = true;
        let showTranslations = true;

        // Load translations.json
        fetch('translations.json')
            .then(res => res.json())
            .then(data => {{
                translationsData = data;
                renderPages();
                renderTextList();
            }})
            .catch(err => {{
                console.error('Failed to load translations:', err);
                document.getElementById('pagePanel').innerHTML = '<p style="padding: 2rem; color: red;">Failed to load translations.json</p>';
            }});

        // Toggle overlays
        document.getElementById('toggleOverlays').addEventListener('change', (e) => {{
            showOverlays = e.target.checked;
            document.querySelectorAll('.overlay').forEach(el => {{
                el.style.display = showOverlays ? 'block' : 'none';
            }});
        }});

        // Toggle translation text
        document.getElementById('toggleTranslation').addEventListener('change', (e) => {{
            showTranslations = e.target.checked;
            document.querySelectorAll('.text-translation').forEach(el => {{
                el.style.display = showTranslations ? 'block' : 'none';
            }});
        }});

        function renderPages() {{
            const pagePanel = document.getElementById('pagePanel');
            const pages = translationsData.pages;

            pages.forEach((page, idx) => {{
                const pageDiv = document.createElement('div');
                pageDiv.className = 'page';
                pageDiv.id = `page-${{idx}}`;

                const img = document.createElement('img');
                img.src = page.image;
                img.alt = `Page ${{idx + 1}}`;
                img.onload = function() {{
                    // Render overlays after image loads to get correct dimensions
                    renderOverlays(pageDiv, page, this.clientWidth, this.clientHeight);
                }};

                const label = document.createElement('div');
                label.className = 'page-label';
                label.textContent = `Page ${{idx + 1}}`;

                pageDiv.appendChild(img);
                pageDiv.appendChild(label);
                pagePanel.appendChild(pageDiv);
            }});
        }}

        function renderOverlays(pageDiv, page, imgWidth, imgHeight) {{
            page.blocks.forEach((block, blockIdx) => {{
                const overlay = document.createElement('div');
                overlay.className = 'overlay';
                overlay.textContent = showTranslations ? block.translation : block.text;
                overlay.dataset.blockId = blockIdx;

                // Convert normalized coordinates (0-1) to pixels
                const left = block.bbox[0] * imgWidth;
                const top = block.bbox[1] * imgHeight;
                const width = block.bbox[2] * imgWidth;
                const height = block.bbox[3] * imgHeight;

                overlay.style.left = left + 'px';
                overlay.style.top = top + 'px';
                overlay.style.width = width + 'px';
                overlay.style.minHeight = height + 'px';

                if (!showOverlays) {{
                    overlay.style.display = 'none';
                }}

                // Click to highlight
                overlay.addEventListener('click', () => {{
                    highlightBlock(blockIdx);
                }});

                pageDiv.appendChild(overlay);
            }});
        }}

        function renderTextList() {{
            const textList = document.getElementById('textList');
            const pages = translationsData.pages;

            pages.forEach((page, pageIdx) => {{
                page.blocks.forEach((block, blockIdx) => {{
                    const item = document.createElement('div');
                    item.className = 'text-item';
                    item.dataset.blockId = blockIdx;
                    item.dataset.pageId = pageIdx;

                    const original = document.createElement('div');
                    original.className = 'text-original';
                    original.textContent = block.text;

                    const translation = document.createElement('div');
                    translation.className = 'text-translation';
                    translation.textContent = block.translation;
                    if (!showTranslations) {{
                        translation.style.display = 'none';
                    }}

                    item.appendChild(original);
                    item.appendChild(translation);

                    item.addEventListener('click', () => {{
                        highlightBlock(blockIdx);
                        // Scroll to page
                        document.getElementById(`page-${{pageIdx}}`).scrollIntoView({{ behavior: 'smooth', block: 'center' }});
                    }});

                    textList.appendChild(item);
                }});
            }});
        }}

        function highlightBlock(blockId) {{
            // Remove previous highlights
            document.querySelectorAll('.highlight').forEach(el => el.classList.remove('highlight'));

            // Highlight in both panels
            document.querySelectorAll(`[data-block-id="${{blockId}}"]`).forEach(el => {{
                el.classList.add('highlight');
            }});
        }}
    </script>
</body>
</html>"""


def generate_main_index_html(manuals: list[dict]) -> str:
    """Generate main index.html with search."""
    import json

    manuals_json = json.dumps(manuals)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Translated Toy Manuals</title>
    <link rel="stylesheet" href="styles.css">
</head>
<body>
    <div class="container">
        <header>
            <h1>Translated Toy Manuals</h1>
            <p class="subtitle">Japanese instruction manuals translated to English using Document AI</p>
            <div class="controls">
                <div class="search-box">
                    <input type="text" id="searchInput" placeholder="Search manuals by name..." />
                </div>
                <div class="stats">
                    <span id="statsDisplay">{len(manuals)} manuals</span>
                </div>
            </div>
        </header>

        <div class="grid" id="manualsGrid"></div>

        <div class="no-results hidden" id="noResults">
            No manuals found matching your search.
        </div>

        <footer>
            <p>Generated by Document AI Translation System</p>
        </footer>
    </div>

    <script>
        const manuals = {manuals_json};
        const grid = document.getElementById('manualsGrid');
        const searchInput = document.getElementById('searchInput');
        const statsDisplay = document.getElementById('statsDisplay');
        const noResults = document.getElementById('noResults');

        function renderManuals(filteredManuals) {{
            grid.innerHTML = '';

            if (filteredManuals.length === 0) {{
                grid.classList.add('hidden');
                noResults.classList.remove('hidden');
                statsDisplay.textContent = '0 manuals';
                return;
            }}

            grid.classList.remove('hidden');
            noResults.classList.add('hidden');

            filteredManuals.forEach(manual => {{
                const card = document.createElement('div');
                card.className = 'card';

                const sourceLink = manual.source_url
                    ? `<a href="${{manual.source_url}}" target="_blank">📄 Original</a>`
                    : '';

                card.innerHTML = `
                    <a href="viewer.html?manual=${{encodeURIComponent(manual.name)}}" class="card-link">
                        <img src="${{manual.name}}/${{manual.thumbnail}}" alt="${{manual.source}}" class="thumbnail" loading="lazy">
                        <div class="card-content">
                            <div class="card-title">${{manual.source}}</div>
                            <div class="card-meta">
                                <span class="badge">${{manual.pages}} pages</span>
                                <span class="badge">${{manual.blocks}} blocks</span>
                            </div>
                            <div class="card-actions">
                                ${{sourceLink}}
                            </div>
                        </div>
                    </a>
                `;

                grid.appendChild(card);
            }});

            statsDisplay.textContent = `${{filteredManuals.length}} manual${{filteredManuals.length !== 1 ? 's' : ''}}`;
        }}

        function filterManuals() {{
            const query = searchInput.value.toLowerCase();
            const filtered = manuals.filter(m =>
                m.name.toLowerCase().includes(query) ||
                m.source.toLowerCase().includes(query)
            );
            renderManuals(filtered);
        }}

        searchInput.addEventListener('input', filterManuals);

        // Initial render
        renderManuals(manuals);
    </script>
</body>
</html>"""
