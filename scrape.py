#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["requests", "beautifulsoup4"]
# ///
"""
Scrape leah-and-chris.wedsites.com into a self-contained static site.
Run with: uv run scrape.py
"""

import re
import sys
from pathlib import Path
from urllib.parse import unquote, urljoin

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://leah-and-chris.wedsites.com"
OUT_DIR = Path(__file__).parent
STATIC = OUT_DIR / "static"
CSS_DIR = STATIC / "css"
JS_DIR = STATIC / "js"
IMG_DIR = STATIC / "images"
FONT_DIR = STATIC / "fonts"
VENDOR_DIR = STATIC / "vendor"

SESSION = requests.Session()
SESSION.headers.update(
    {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
    }
)

VIMEO_HOSTS = {"player.vimeo.com"}
EXTERNAL_KEEP = {
    "fonts.googleapis.com",
    "fonts.gstatic.com",
    "docs.google.com",
    "calendar.google.com",
    "www.google.com",
    "wedsites.com",
    "www.brides.com",
}


def download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        print(f"  [skip] {dest.relative_to(OUT_DIR)}")
        return
    print(f"  [GET]  {url}")
    resp = SESSION.get(url, timeout=60)
    resp.raise_for_status()
    dest.write_bytes(resp.content)
    print(f"         → {dest.relative_to(OUT_DIR)} ({len(resp.content):,} bytes)")


def download_text(url: str) -> str:
    print(f"  [GET]  {url}")
    resp = SESSION.get(url, timeout=60)
    resp.raise_for_status()
    return resp.text


def resolve(url: str) -> str:
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("/"):
        return BASE_URL + url
    if url.startswith("http"):
        return url
    return urljoin(BASE_URL + "/", url)


def safe_filename(url: str) -> str:
    path = unquote(url.split("?")[0].split("#")[0])
    return Path(path).name


def decode_cf_email(encoded: str) -> str:
    key = int(encoded[:2], 16)
    return "".join(chr(int(encoded[i : i + 2], 16) ^ key) for i in range(2, len(encoded), 2))


# ---------------------------------------------------------------------------
# 1. Download HTML
# ---------------------------------------------------------------------------
print("\n=== Downloading HTML ===")
html_text = download_text(BASE_URL + "/")

soup = BeautifulSoup(html_text, "html.parser")

# ---------------------------------------------------------------------------
# 2. Download & rewrite main CSS
# ---------------------------------------------------------------------------
print("\n=== Processing CSS ===")
css_link = soup.find("link", rel="stylesheet", href=re.compile(r"/assets/site-"))
assert css_link, "Could not find main CSS link"
css_url = resolve(css_link["href"])
css_text = download_text(css_url)

# Map of original url() paths → (local_dir, clean_filename)
CSS_ASSET_MAP: dict[str, tuple[Path, str]] = {}

def process_css_url(match: re.Match) -> str:
    raw = match.group(1).strip("'\"")

    # Keep data URIs
    if raw.startswith("data:"):
        return match.group(0)

    # Skip country flag SVGs (hundreds, unused on this page)
    if "/flags/" in raw:
        return match.group(0)

    full_url = resolve(raw)
    fname = safe_filename(raw)

    # Route to appropriate directory
    if "wedsites-" in fname or fname.startswith("wedsites"):
        ext = fname.rsplit(".", 1)[-1] if "." in fname else ""
        clean = f"wedsites.{ext}"
        dest_dir = FONT_DIR
        rel_prefix = "../fonts"
    elif "lightbox" in raw:
        clean = fname.split("-")[0] + "." + fname.rsplit(".", 1)[-1]
        dest_dir = VENDOR_DIR
        rel_prefix = "../vendor"
    elif "chosen-sprite" in raw:
        if "@2x" in raw:
            clean = "chosen-sprite@2x.png"
        else:
            clean = "chosen-sprite.png"
        dest_dir = VENDOR_DIR
        rel_prefix = "../vendor"
    else:
        clean = fname
        dest_dir = VENDOR_DIR
        rel_prefix = "../vendor"

    dest = dest_dir / clean
    if not dest.exists():
        try:
            download(full_url, dest)
        except Exception as e:
            print(f"  [WARN] Failed to download {full_url}: {e}")
            return match.group(0)

    return f"url({rel_prefix}/{clean})"


css_text = re.sub(r"url\(([^)]+)\)", process_css_url, css_text)

# ---------------------------------------------------------------------------
# 3. Download Google Fonts & generate local @font-face
# ---------------------------------------------------------------------------
print("\n=== Processing Google Fonts ===")

GOOGLE_FONT_URLS = [
    "https://fonts.googleapis.com/css2?family=Josefin+Sans:wght@600&display=swap",
    "https://fonts.googleapis.com/css2?family=Libre+Baskerville&display=swap",
    "https://fonts.googleapis.com/css2?family=Vidaloka&display=swap",
    "https://fonts.googleapis.com/css2?family=Libre+Baskerville:wght@600&display=swap",
]

font_face_css = ""
for gf_url in GOOGLE_FONT_URLS:
    gf_css = download_text(gf_url)

    def rewrite_google_font_url(match: re.Match) -> str:
        font_url = match.group(1).strip("'\"")
        fname = safe_filename(font_url)
        # Make filename more descriptive
        dest = FONT_DIR / fname
        if not dest.exists():
            try:
                download(font_url, dest)
            except Exception as e:
                print(f"  [WARN] Failed to download font {font_url}: {e}")
                return match.group(0)
        return f"url(../fonts/{fname})"

    gf_css = re.sub(r"url\(([^)]+)\)", rewrite_google_font_url, gf_css)
    font_face_css += gf_css + "\n"

# Prepend Google Fonts @font-face to main CSS
css_text = font_face_css + "\n" + css_text

# Save CSS
CSS_DIR.mkdir(parents=True, exist_ok=True)
(CSS_DIR / "site.css").write_text(css_text, encoding="utf-8")
print(f"  [save] static/css/site.css ({len(css_text):,} chars)")

# ---------------------------------------------------------------------------
# 4. Download JS
# ---------------------------------------------------------------------------
print("\n=== Downloading JS ===")
js_script = soup.find("script", src=re.compile(r"/assets/site-"))
assert js_script, "Could not find main JS script"
download(resolve(js_script["src"]), JS_DIR / "site.js")

cf_script = soup.find("script", src=re.compile(r"email-decode"))
if cf_script:
    download(resolve(cf_script["src"]), JS_DIR / "email-decode.min.js")

# Also grab favicon
print("\n=== Downloading favicon ===")
try:
    download(resolve("/favicon.ico"), STATIC / "favicon.ico")
except Exception:
    print("  [WARN] favicon not found, skipping")

# ---------------------------------------------------------------------------
# 5. Download images
# ---------------------------------------------------------------------------
print("\n=== Downloading images ===")
CDN_IMAGE_URLS = set()

# From <img> tags
for img in soup.find_all("img", src=re.compile(r"cdn\.wedsites\.com")):
    CDN_IMAGE_URLS.add(img["src"])

# From og:image
og = soup.find("meta", property="og:image")
if og and og.get("content"):
    CDN_IMAGE_URLS.add(og["content"])

# From inline style background URLs
for el in soup.find_all(style=re.compile(r"cdn\.wedsites\.com")):
    urls = re.findall(r"url\(([^)]+)\)", el["style"])
    for u in urls:
        CDN_IMAGE_URLS.add(u.strip("'\""))

# Download all CDN images
image_filename_map: dict[str, str] = {}  # original URL → local filename
for img_url in sorted(CDN_IMAGE_URLS):
    fname = safe_filename(img_url)
    image_filename_map[img_url] = fname
    download(img_url, IMG_DIR / fname)

# ---------------------------------------------------------------------------
# 6. Rewrite HTML
# ---------------------------------------------------------------------------
print("\n=== Rewriting HTML ===")

# 6a. Add charset meta if missing (original relies on HTTP headers)
if not soup.find("meta", charset=True):
    charset_tag = soup.new_tag("meta", charset="utf-8")
    soup.head.insert(0, charset_tag)
    print("  [add] <meta charset=\"utf-8\">")

# 6b. Main CSS link
css_link["href"] = "static/css/site.css"
print("  [rewrite] CSS link → static/css/site.css")

# 6b. Main JS
js_script["src"] = "static/js/site.js"
print("  [rewrite] JS script → static/js/site.js")

# 6c. Cloudflare email-decode script
if cf_script:
    cf_script["src"] = "static/js/email-decode.min.js"
    print("  [rewrite] email-decode → static/js/email-decode.min.js")

# 6d. Favicon
fav = soup.find("link", rel="icon")
if fav:
    fav["href"] = "static/favicon.ico"
    print("  [rewrite] favicon → static/favicon.ico")

# 6e. og:image
if og and og.get("content"):
    fname = image_filename_map.get(og["content"], "")
    if fname:
        og["content"] = f"static/images/{fname}"
        print(f"  [rewrite] og:image → static/images/{fname}")

# 6f. CDN images in <img> tags
for img in soup.find_all("img", src=re.compile(r"cdn\.wedsites\.com")):
    orig = img["src"]
    fname = image_filename_map.get(orig, safe_filename(orig))
    img["src"] = f"static/images/{fname}"
    print(f"  [rewrite] img → static/images/{fname}")

# 6g. Inline style background-image URLs
for el in soup.find_all(style=re.compile(r"cdn\.wedsites\.com")):
    style = el["style"]
    for orig_url, fname in image_filename_map.items():
        style = style.replace(orig_url, f"static/images/{fname}")
    el["style"] = style
    print(f"  [rewrite] inline style background → local")

# 6h. Remove Google Fonts preconnect links (fonts are now local)
for link in soup.find_all("link", rel="preconnect"):
    href = link.get("href", "")
    if "fonts.googleapis.com" in href or "fonts.gstatic.com" in href:
        link.decompose()
        print(f"  [remove] preconnect {href}")

# 6i. Replace Google Fonts @import in inline <style> with comment
for style_tag in soup.find_all("style"):
    if style_tag.string and "@import url('https://fonts.googleapis.com" in style_tag.string:
        new_css = re.sub(
            r"@import url\('https://fonts\.googleapis\.com[^']*'\);\s*",
            "",
            style_tag.string,
        )
        new_css = "/* Google Fonts now served from static/css/site.css */\n" + new_css
        style_tag.string = new_css
        print("  [rewrite] replaced Google Fonts @import with local reference note")

# 6j. Decode Cloudflare email protection links
for a in soup.find_all("a", href=re.compile(r"/cdn-cgi/l/email-protection#")):
    encoded = a["href"].split("#", 1)[1]
    email = decode_cf_email(encoded)
    a["href"] = f"mailto:{email}"
    a.string = email
    print(f"  [decode] email → {email}")

# ---------------------------------------------------------------------------
# 7. Save HTML
# ---------------------------------------------------------------------------
print("\n=== Saving index.html ===")
html_out = str(soup)
# BeautifulSoup can mangle some things; use the original DOCTYPE
if not html_out.startswith("<!DOCTYPE"):
    html_out = "<!DOCTYPE html>\n" + html_out
(OUT_DIR / "index.html").write_text(html_out, encoding="utf-8")
print(f"  [save] index.html ({len(html_out):,} chars)")

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
print("\n=== Done! ===")
total_files = sum(1 for _ in STATIC.rglob("*") if _.is_file()) + 1  # +1 for index.html
print(f"  Total files: {total_files}")
print(f"  To preview: python3 -m http.server 8000")
print(f"  Then open:  http://localhost:8000")
