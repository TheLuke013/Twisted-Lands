import os
import sys
from datetime import datetime
from html import escape
from typing import Any, Dict, List, Optional, Set
from dotenv import load_dotenv
from notion_client import Client

load_dotenv()

NOTION_TOKEN = os.getenv("NOTION_TOKEN")
ROOT_PAGE_ID = os.getenv("PAGE_ID")
OUTPUT_DIR = "output"

def log_info(msg: str):
    print(f"[INFO {datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

def log_error(msg: str):
    print(f"[ERROR {datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

def get_notion_client() -> Client:
    if not NOTION_TOKEN:
        raise RuntimeError("NOTION_TOKEN não encontrado no arquivo .env")
    return Client(auth=NOTION_TOKEN)

def get_page_title(page: Dict[str, Any]) -> str:
    properties = page.get("properties", {})
    for prop in properties.values():
        if isinstance(prop, dict):
            if prop.get("type") == "title":
                return "".join(item.get("plain_text", "") for item in prop.get("title", []))
            if isinstance(prop.get("title"), list):
                return "".join(item.get("plain_text", "") for item in prop.get("title", []))
            if isinstance(prop.get("rich_text"), list):
                return "".join(item.get("plain_text", "") for item in prop.get("rich_text", []))
    for key in ("name", "Name", "title"):
        value = properties.get(key)
        if isinstance(value, dict):
            if value.get("type") == "rich_text":
                return "".join(item.get("plain_text", "") for item in value.get("rich_text", []))
            if isinstance(value.get("title"), list):
                return "".join(item.get("plain_text", "") for item in value.get("title", []))
    return page.get("id", "Untitled")

def extract_text(rich_text_items: Optional[List[Dict[str, Any]]]) -> str:
    if not rich_text_items:
        return ""
    return "".join(item.get("plain_text", "") for item in rich_text_items)

def format_rich_text(rich_text_items: Optional[List[Dict[str, Any]]],
                     page_id_map: Dict[str, str]) -> str:
    if not rich_text_items:
        return ""
    parts: List[str] = []
    for item in rich_text_items:
        text = item.get("text", {}).get("content", "")
        annotations = item.get("annotations", {})
        text = escape(text).replace("\n", "<br>")
        if annotations.get("code"):
            text = f"<code>{text}</code>"
        else:
            if annotations.get("bold"):
                text = f"<strong>{text}</strong>"
            if annotations.get("italic"):
                text = f"<em>{text}</em>"
            if annotations.get("strikethrough"):
                text = f"<s>{text}</s>"
            if annotations.get("underline"):
                text = f"<u>{text}</u>"
        if item.get("text", {}).get("link"):
            href = escape(item["text"]["link"].get("url", "#"))
            
            # Limpeza do href - NÃO remover todos os caracteres
            href_clean = href.lstrip('/')
            if href_clean.startswith("page/"):
                href_clean = href_clean[5:]
            
            # Remove apenas query params e anchors, mas preserva hífens
            href_clean = href_clean.split('?')[0]
            href_clean = href_clean.split('#')[0]
            # NÃO remover as barras - o ID completo já está limpo
            # href_clean = href_clean.replace('/', '')  # REMOVER ESTA LINHA
            
            # Se for um ID de página conhecido, transforma em link .html
            if href_clean in page_id_map:
                target = page_id_map[href_clean]
                text = f'<a href="{target}" target="_parent" rel="noreferrer">{text}</a>'
            else:
                # Link externo
                text = f'<a href="{href}" target="_parent" rel="noreferrer">{text}</a>'
        parts.append(text)
    return "".join(parts)

def fetch_page_blocks(notion: Client, page_id: str) -> List[Dict[str, Any]]:
    log_info(f"Buscando blocos da página: {page_id}")
    results: List[Dict[str, Any]] = []
    cursor: Optional[str] = None
    while True:
        payload: Dict[str, Any] = {"block_id": page_id, "page_size": 100}
        if cursor:
            payload["start_cursor"] = cursor
        response = notion.blocks.children.list(**payload)
        results.extend(response.get("results", []))
        if not response.get("has_more"):
            break
        cursor = response.get("next_cursor")
    log_info(f"Total de blocos encontrados para {page_id}: {len(results)}")
    return results

def collect_child_page_ids(blocks: List[Dict[str, Any]]) -> List[str]:
    page_ids: List[str] = []
    for block in blocks:
        if block.get("type") == "child_page":
            child_page = block.get("child_page", {})
            page_id = child_page.get("page_id") or child_page.get("id") or block.get("id")
            if page_id:
                page_ids.append(page_id)
            else:
                log_error(f"Child page block sem ID válido: {block.get('id')}")
    return page_ids

def render_blocks_to_html(blocks: Optional[List[Dict[str, Any]]],
                          page_id_map: Dict[str, str]) -> str:
    if not blocks:
        return "<p>Nenhum conteúdo encontrado.</p>"

    html_parts: List[str] = []
    list_items: List[str] = []
    list_type: Optional[str] = None
    in_column_list = False
    columns = []
    current_column_content = []

    def flush_list() -> None:
        nonlocal list_items, list_type
        if not list_items:
            return
        tag = "ul" if list_type == "bulleted_list_item" else "ol"
        html_parts.append(f"<{tag}>")
        for item in list_items:
            html_parts.append(f"<li>{item}</li>")
        html_parts.append(f"</{tag}>")
        list_items = []
        list_type = None

    def flush_columns():
        nonlocal in_column_list, columns, current_column_content
        if columns:
            html_parts.append('<div class="columns-container">')
            for col in columns:
                html_parts.append(f'<div class="column">{col}</div>')
            html_parts.append('</div>')
            columns = []
            current_column_content = []
            in_column_list = False

    for block in blocks:
        block_type = block.get("type")
        
        if block_type == "column_list":
            flush_list()
            flush_columns()
            in_column_list = True
            continue
        
        if in_column_list and block_type == "column":
            if current_column_content:
                columns.append("".join(current_column_content))
                current_column_content = []
            continue
        
        if in_column_list:
            if block_type in {"bulleted_list_item", "numbered_list_item"}:
                item_block = block.get(block_type, {})
                item_text = format_rich_text(item_block.get("rich_text", []), page_id_map)
                if list_type != block_type:
                    if list_items:
                        tag = "ul" if list_type == "bulleted_list_item" else "ol"
                        current_column_content.append(f"<{tag}>")
                        for item in list_items:
                            current_column_content.append(f"<li>{item}</li>")
                        current_column_content.append(f"</{tag}>")
                        list_items = []
                    list_type = block_type
                list_items.append(item_text)
                continue
            
            flush_list()
            
            if block_type == "paragraph":
                text = format_rich_text(block.get("paragraph", {}).get("rich_text", []), page_id_map)
                current_column_content.append(f"<p>{text}</p>")
            elif block_type in {"heading_1", "heading_2", "heading_3"}:
                level = int(block_type.split("_")[-1])
                text = format_rich_text(block.get(block_type, {}).get("rich_text", []), page_id_map)
                current_column_content.append(f"<h{level}>{text}</h{level}>")
            elif block_type == "image":
                image = block.get("image", {})
                src = image.get("external", {}).get("url") or image.get("file", {}).get("url") or ""
                caption = extract_text(image.get("caption", []))
                current_column_content.append(f'''
                    <div class="image-item">
                        <figure class="image-card">
                            <img src="{escape(src)}" alt="{escape(caption)}" loading="lazy" />
                            {f'<figcaption>{escape(caption)}</figcaption>' if caption else ''}
                        </figure>
                    </div>
                ''')
            else:
                text = format_rich_text(block.get(block_type, {}).get("rich_text", []), page_id_map)
                if text:
                    current_column_content.append(f"<p>{text}</p>")
            continue
        
        if block_type in {"bulleted_list_item", "numbered_list_item"}:
            item_block = block.get(block_type, {})
            item_text = format_rich_text(item_block.get("rich_text", []), page_id_map)
            if list_type != block_type:
                flush_list()
                list_type = block_type
            list_items.append(item_text)
            continue

        flush_list()

        if block_type == "paragraph":
            text = format_rich_text(block.get("paragraph", {}).get("rich_text", []), page_id_map)
            html_parts.append(f"<p>{text}</p>")
        elif block_type in {"heading_1", "heading_2", "heading_3"}:
            level = int(block_type.split("_")[-1])
            text = format_rich_text(block.get(block_type, {}).get("rich_text", []), page_id_map)
            html_parts.append(f"<h{level}>{text}</h{level}>")
        elif block_type == "quote":
            text = format_rich_text(block.get("quote", {}).get("rich_text", []), page_id_map)
            html_parts.append(f"<blockquote>{text}</blockquote>")
        elif block_type == "callout":
            text = format_rich_text(block.get("callout", {}).get("rich_text", []), page_id_map)
            html_parts.append(f"<div class=\"callout\">{text}</div>")
        elif block_type == "code":
            code_text = escape(extract_text(block.get("code", {}).get("rich_text", [])))
            html_parts.append(f"<pre><code>{code_text}</code></pre>")
        elif block_type == "divider":
            html_parts.append("<hr />")
        elif block_type == "image":
            image = block.get("image", {})
            src = image.get("external", {}).get("url") or image.get("file", {}).get("url") or ""
            caption = extract_text(image.get("caption", []))
            html_parts.append(f'''
                <div class="image-item">
                    <figure class="image-card">
                        <img src="{escape(src)}" alt="{escape(caption)}" loading="lazy" />
                        {f'<figcaption>{escape(caption)}</figcaption>' if caption else ''}
                    </figure>
                </div>
            ''')
        elif block_type == "toggle":
            text = format_rich_text(block.get("toggle", {}).get("rich_text", []), page_id_map)
            html_parts.append(f"<details><summary>{text}</summary></details>")
        elif block_type == "bookmark":
            url = block.get("bookmark", {}).get("url", "")
            html_parts.append(f'<p><a href="{escape(url)}" target="_parent" rel="noreferrer">{escape(url)}</a></p>')
        else:
            text = format_rich_text(block.get(block_type, {}).get("rich_text", []), page_id_map)
            if text:
                html_parts.append(f"<p>{text}</p>")

    flush_list()
    if in_column_list and current_column_content:
        columns.append("".join(current_column_content))
        flush_columns()
    
    return "".join(html_parts)

def collect_all_page_ids(notion: Client, root_id: str, collected: Optional[Set[str]] = None) -> Set[str]:
    if collected is None:
        collected = set()
    if root_id in collected:
        return collected
    collected.add(root_id)
    log_info(f"Coletando filhos da página {root_id}")
    blocks = fetch_page_blocks(notion, root_id)
    child_ids = collect_child_page_ids(blocks)
    for child_id in child_ids:
        collect_all_page_ids(notion, child_id, collected)
    return collected

def generate_page_html(notion: Client, page_id: str, root_id: str,
                       all_page_ids: Set[str], output_dir: str) -> None:
    log_info(f"Gerando HTML para página: {page_id}")
    
    page = notion.pages.retrieve(page_id)
    title = get_page_title(page)
    blocks = fetch_page_blocks(notion, page_id)
    
    # Mapeamento de IDs de página para caminhos HTML (suporta IDs com e sem hífens)
    page_id_map = {}
    for pid in all_page_ids:
        pid_normalized = pid.replace('-', '')  # Versão sem hífens para comparação
        if pid == root_id:
            page_id_map[pid] = "index.html"
            page_id_map[pid_normalized] = "index.html"
        else:
            page_id_map[pid] = f"{pid}.html"
            page_id_map[pid_normalized] = f"{pid}.html"
    
    # Renderiza o conteúdo (os links internos já serão convertidos aqui)
    content_html = render_blocks_to_html(blocks, page_id_map)
    
    # Template com favicon adicionado
    html_template = """<!DOCTYPE html>
<html lang="pt-BR">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{{ title }} - Twisted Lands Wiki</title>
    <link rel="shortcut icon" href="../icon.ico" type="image/x-icon" />
    <style>
  :root {
    color-scheme: dark;
    --bg: #040b12;
    --bg-soft: #0b1927;
    --panel: rgba(8, 19, 31, 0.92);
    --panel-strong: rgba(13, 29, 47, 0.98);
    --text: #f2f7ff;
    --muted: #8ea4bb;
    --accent: #71e2ff;
    --accent-2: #b08aff;
    --border: rgba(255,255,255,0.1);
    --shadow: 0 24px 50px rgba(0, 0, 0, 0.35);
  }

  * { box-sizing: border-box; }
  body {
    margin: 0;
    font-family: "Segoe UI", Inter, Roboto, Arial, sans-serif;
    background: linear-gradient(135deg, #2b1055 0%, #751111 100%);
    color: var(--text);
    min-height: 100vh;
  }

  .shell {
    max-width: 1100px;
    margin: 0 auto;
    padding: 20px;
  }

  .logo-header {
    display: flex;
    justify-content: center;
    padding: 20px 0 10px 0;
  }
  .brand-logo {
    width: 220px;
    max-width: 100%;
    display: block;
    filter: drop-shadow(0 10px 18px rgba(0,0,0,0.25));
  }

  .content {
    display: flex;
    flex-direction: column;
    gap: 22px;
  }

  .page-panel {
    background: linear-gradient(135deg, var(--panel), var(--panel-strong));
    border: 1px solid var(--border);
    border-radius: 20px;
    box-shadow: var(--shadow);
    padding: 24px 28px;
  }

  .page-panel h1, .page-panel h2, .page-panel h3, .page-panel h4 {
    color: var(--accent);
    margin-top: 0.5rem;
  }

  .page-panel p, .page-panel li, .page-panel blockquote, .page-panel figcaption {
    line-height: 1.7;
    color: var(--text);
  }

  .page-panel a {
    color: var(--accent);
    text-decoration: none;
    border-bottom: 1px solid rgba(113, 226, 255, 0.3);
    transition: border-color 0.2s;
  }

  .page-panel a:hover {
    border-bottom-color: var(--accent);
    text-decoration: none;
  }

  .image-item {
    display: inline-block;
    width: auto;
    max-width: 250px;
    margin: 10px;
    vertical-align: top;
  }

  .image-card {
    margin: 0;
    background: var(--panel-strong);
    border-radius: 12px;
    overflow: hidden;
    transition: transform 0.2s;
  }

  .image-card:hover {
    transform: translateY(-4px);
  }

  .image-card img {
    width: 100%;
    height: auto;
    max-width: 250px;
    display: block;
    image-rendering: crisp-edges;
    image-rendering: pixelated;
    -ms-interpolation-mode: nearest-neighbor;
  }

  .image-card figcaption {
    padding: 10px;
    text-align: center;
    font-size: 0.9rem;
    color: var(--muted);
  }

  .columns-container {
    display: flex;
    gap: 20px;
    margin: 20px 0;
    flex-wrap: wrap;
    justify-content: flex-start;
    align-items: flex-start;
  }

  .column {
    flex: 1;
    min-width: 200px;
  }

  .page-panel img {
    max-width: 100%;
    height: auto;
  }

  .page-panel blockquote {
    border-left: 4px solid var(--accent);
    padding-left: 14px;
    margin-left: 0;
    color: var(--muted);
  }

  .page-panel code {
    background: rgba(113, 226, 255, 0.12);
    padding: 2px 6px;
    border-radius: 6px;
    color: #bff6ff;
  }

  .page-panel ul, .page-panel ol {
    padding-left: 20px;
  }

  .page-panel hr {
    border: 0;
    border-top: 1px solid var(--border);
    margin: 24px 0;
  }

  @media (max-width: 768px) {
    .image-item {
      max-width: 200px;
    }
    .image-card img {
      max-width: 200px;
    }
    .columns-container {
      flex-direction: column;
    }
  }

  @media (max-width: 480px) {
    .image-item {
      max-width: 150px;
    }
    .image-card img {
      max-width: 150px;
    }
  }
</style>
  </head>
  <body>
    <div class="shell">
      <div class="logo-header">
        <a href="index.html">
          <img class="brand-logo" src="../header-icon.png" alt="Twisted Lands" />
        </a>
      </div>

      <main class="content">
        <section class="page-panel">
          {{ content_html|safe }}
        </section>
      </main>
    </div>
  </body>
</html>
"""
    from jinja2 import Template
    template = Template(html_template)
    final_html = template.render(title=title, content_html=content_html)
    
    if page_id == root_id:
        out_path = os.path.join(output_dir, "index.html")
    else:
        out_path = os.path.join(output_dir, f"{page_id}.html")
    
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(final_html)
    log_info(f"Página salva: {out_path}")

def main():
    if not ROOT_PAGE_ID:
        log_error("PAGE_ID não definido no .env")
        sys.exit(1)
    
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    notion = get_notion_client()
    
    log_info("Coletando todas as páginas da wiki...")
    all_ids = collect_all_page_ids(notion, ROOT_PAGE_ID)
    log_info(f"Total de páginas encontradas: {len(all_ids)}")
    
    for page_id in all_ids:
        generate_page_html(notion, page_id, ROOT_PAGE_ID, all_ids, OUTPUT_DIR)
    
    log_info(f"✅ Site estático gerado com sucesso em '{OUTPUT_DIR}/'")

if __name__ == "__main__":
    main()