"""
BookVoice — A personal AI book reader
Reads your books aloud in English, French, or Arabic with state-of-the-art neural voices.
"""

import os
import asyncio
import tempfile
import hashlib
import json
import re
import uuid
from pathlib import Path
from flask import Flask, request, jsonify, send_file, render_template, send_from_directory
from werkzeug.utils import secure_filename

import edge_tts

# Optional premium TTS
try:
    import openai
    HAS_OPENAI = True
except ImportError:
    HAS_OPENAI = False

# Book parsing
import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup
import fitz  # PyMuPDF

# OCR support for scanned/broken PDFs
try:
    import pytesseract
    from PIL import Image
    import io
    HAS_OCR = True
except ImportError:
    HAS_OCR = False

# Arabic diacritization (tashkeel) for better TTS pronunciation
try:
    import mishkal.tashkeel as _mishkal_tashkeel
    _vocalizer = _mishkal_tashkeel.TashkeelClass()
    HAS_TASHKEEL = True
except ImportError:
    HAS_TASHKEEL = False

app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024  # 200MB max upload

# Directories
UPLOAD_DIR = Path(tempfile.gettempdir()) / "bookvoice_uploads"
AUDIO_DIR = Path(tempfile.gettempdir()) / "bookvoice_audio"
UPLOAD_DIR.mkdir(exist_ok=True)
AUDIO_DIR.mkdir(exist_ok=True)

# ─── Voice configurations ───────────────────────────────────────────────────

VOICES = {
    "en": {
        "label": "English",
        "voices": [
            {"id": "en-US-AriaNeural", "name": "Aria (US, Female)", "gender": "female"},
            {"id": "en-US-GuyNeural", "name": "Guy (US, Male)", "gender": "male"},
            {"id": "en-GB-SoniaNeural", "name": "Sonia (UK, Female)", "gender": "female"},
            {"id": "en-GB-RyanNeural", "name": "Ryan (UK, Male)", "gender": "male"},
            {"id": "en-US-JennyNeural", "name": "Jenny (US, Female)", "gender": "female"},
            {"id": "en-AU-NatashaNeural", "name": "Natasha (AU, Female)", "gender": "female"},
        ],
    },
    "fr": {
        "label": "French",
        "voices": [
            {"id": "fr-FR-DeniseNeural", "name": "Denise (France, Female)", "gender": "female"},
            {"id": "fr-FR-HenriNeural", "name": "Henri (France, Male)", "gender": "male"},
            {"id": "fr-FR-VivienneMultilingualNeural", "name": "Vivienne (France, Female)", "gender": "female"},
            {"id": "fr-CA-SylvieNeural", "name": "Sylvie (Canada, Female)", "gender": "female"},
            {"id": "fr-CA-AntoineNeural", "name": "Antoine (Canada, Male)", "gender": "male"},
        ],
    },
    "ar": {
        "label": "Arabic",
        "voices": [
            {"id": "ar-SA-ZariyahNeural", "name": "Zariyah (Saudi, Female)", "gender": "female"},
            {"id": "ar-SA-HamedNeural", "name": "Hamed (Saudi, Male)", "gender": "male"},
            {"id": "ar-EG-SalmaNeural", "name": "Salma (Egypt, Female)", "gender": "female"},
            {"id": "ar-EG-ShakirNeural", "name": "Shakir (Egypt, Male)", "gender": "male"},
            {"id": "ar-DZ-AminaNeural", "name": "Amina (Algeria, Female)", "gender": "female"},
            {"id": "ar-DZ-IsmaelNeural", "name": "Ismael (Algeria, Male)", "gender": "male"},
            {"id": "ar-MA-MounaNeural", "name": "Mouna (Morocco, Female)", "gender": "female"},
            {"id": "ar-MA-JamalNeural", "name": "Jamal (Morocco, Male)", "gender": "male"},
        ],
    },
}

# ─── Book parsing ────────────────────────────────────────────────────────────

def parse_pdf(filepath: str) -> list[dict]:
    """Parse PDF into chapters/pages with text."""
    try:
        doc = fitz.open(filepath)
    except Exception as e:
        raise ValueError(f"Cannot open PDF: {str(e)}")

    # Handle encrypted/password-protected PDFs
    if doc.is_encrypted:
        # Try opening with empty password (some PDFs have owner-only restrictions)
        if not doc.authenticate(""):
            doc.close()
            raise ValueError(
                "This PDF is password-protected. Please remove the password first, "
                "or try a different copy of the book."
            )

    total_pages = len(doc)
    chapters = []
    current_chapter = {"title": "Chapter 1", "text": "", "page_start": 1}
    chapter_num = 1

    for page_num in range(total_pages):
        page = doc[page_num]
        text = page.get_text("text").strip()
        if not text:
            continue

        # Heuristic: detect chapter headings
        lines = text.split("\n")
        first_line = lines[0].strip() if lines else ""
        is_chapter_break = bool(
            re.match(r"^(chapter|chapitre|الفصل|باب)\s*\d*", first_line, re.IGNORECASE)
            or re.match(r"^\d+\.\s+\w", first_line)
        )

        if is_chapter_break and current_chapter["text"].strip():
            chapters.append(current_chapter)
            chapter_num += 1
            current_chapter = {
                "title": first_line[:80] or f"Chapter {chapter_num}",
                "text": text,
                "page_start": page_num + 1,
            }
        else:
            current_chapter["text"] += "\n\n" + text

    if current_chapter["text"].strip():
        chapters.append(current_chapter)

    # If no chapters detected, split by pages in groups of 5
    if len(chapters) <= 1 and total_pages > 10:
        chapters = []
        for i in range(0, total_pages, 5):
            chunk_text = ""
            for j in range(i, min(i + 5, total_pages)):
                page = doc[j]
                chunk_text += page.get_text("text") + "\n\n"
            if chunk_text.strip():
                chapters.append({
                    "title": f"Pages {i+1}–{min(i+5, total_pages)}",
                    "text": chunk_text.strip(),
                    "page_start": i + 1,
                })

    doc.close()

    # Check if extracted text is usable (not just URLs, isolated words, junk)
    all_text = " ".join(ch["text"] for ch in chapters)
    cleaned = _clean_text(all_text)
    words = cleaned.split()
    avg_word_len = len(cleaned) / len(words) if words else 0

    text_is_broken = (
        not chapters
        or all(len(ch["text"].strip()) < 10 for ch in chapters)
        or len(cleaned) < 100
        or avg_word_len < 3  # isolated single words = broken text layer
    )

    if text_is_broken:
        # Try reconstructing text from positioned blocks (fixes broken Arabic PDFs)
        reconstructed = _extract_pdf_by_blocks(filepath)
        if reconstructed:
            return reconstructed

        # Last resort: OCR
        if HAS_OCR:
            return _ocr_pdf(filepath)

        raise ValueError(
            "This PDF has a broken text layer (common with Arabic PDFs). "
            "Install OCR support for a fallback:\n"
            "  brew install tesseract tesseract-lang\n"
            "  pip3 install pytesseract Pillow"
        )

    return chapters


def _extract_pdf_by_blocks(filepath: str) -> list[dict]:
    """
    Extract text using positioned word blocks, reconstructing proper reading order.
    Fixes Arabic PDFs where get_text() returns disordered isolated words.
    """
    doc = fitz.open(filepath)
    total_pages = len(doc)
    chapters = []

    for i in range(0, total_pages, 5):
        chunk_text = ""
        for j in range(i, min(i + 5, total_pages)):
            page = doc[j]

            # Get word-level data with positions: (x0, y0, x1, y1, "word", block, line, word_no)
            words = page.get_text("words")
            if not words:
                continue

            # Group words by line (same approximate y-coordinate)
            lines = {}
            for w in words:
                x0, y0, x1, y1, word, block_no, line_no, word_no = w
                # Round y to group words on the same line (within 3px tolerance)
                y_key = round(y0 / 3) * 3
                if y_key not in lines:
                    lines[y_key] = []
                lines[y_key].append((x0, x1, word))

            # Sort lines top-to-bottom
            sorted_y = sorted(lines.keys())

            page_text_lines = []
            for y_key in sorted_y:
                line_words = lines[y_key]
                # Sort words right-to-left for Arabic (by x0 descending)
                # Detect if line is Arabic by checking for Arabic characters
                sample = " ".join(w[2] for w in line_words)
                is_arabic = bool(re.search(r"[؀-ۿݐ-ݿࢠ-ࣿ]", sample))

                if is_arabic:
                    line_words.sort(key=lambda w: w[0], reverse=True)
                else:
                    line_words.sort(key=lambda w: w[0])

                line_text = " ".join(w[2] for w in line_words)
                page_text_lines.append(line_text)

            chunk_text += "\n".join(page_text_lines) + "\n\n"

        if chunk_text.strip():
            chapters.append({
                "title": f"Pages {i+1}–{min(i+5, total_pages)}",
                "text": chunk_text.strip(),
                "page_start": i + 1,
            })

    doc.close()

    # Verify the reconstructed text is actually better
    if chapters:
        all_text = " ".join(ch["text"] for ch in chapters)
        cleaned = _clean_text(all_text)
        words = cleaned.split()
        if len(words) > 50 and len(cleaned) / len(words) > 2.5:
            return chapters

    return []  # Signal that block extraction didn't help


def _ocr_pdf(filepath: str) -> list[dict]:
    """Extract text from PDF pages using OCR (for scanned/broken PDFs)."""
    doc = fitz.open(filepath)
    total_pages = len(doc)
    chapters = []

    for i in range(0, total_pages, 5):
        chunk_text = ""
        for j in range(i, min(i + 5, total_pages)):
            page = doc[j]
            mat = fitz.Matrix(2.0, 2.0)
            pix = page.get_pixmap(matrix=mat)
            img_data = pix.tobytes("png")
            img = Image.open(io.BytesIO(img_data))

            try:
                text = pytesseract.image_to_string(img, lang="ara+eng+fra")
                chunk_text += text + "\n\n"
            except Exception:
                try:
                    text = pytesseract.image_to_string(img, lang="ara")
                    chunk_text += text + "\n\n"
                except Exception:
                    continue

        if chunk_text.strip():
            chapters.append({
                "title": f"Pages {i+1}–{min(i+5, total_pages)}",
                "text": chunk_text.strip(),
                "page_start": i + 1,
            })

    doc.close()

    if not chapters:
        raise ValueError("Could not extract text from this PDF.")

    return chapters


def parse_epub(filepath: str) -> list[dict]:
    """Parse EPUB into chapters."""
    book = epub.read_epub(filepath, options={"ignore_ncx": True})
    chapters = []
    chapter_num = 0

    for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
        soup = BeautifulSoup(item.get_content(), "html.parser")
        text = soup.get_text(separator="\n").strip()
        if not text or len(text) < 50:
            continue

        chapter_num += 1
        # Try to extract title from first heading
        heading = soup.find(["h1", "h2", "h3"])
        title = heading.get_text().strip() if heading else f"Chapter {chapter_num}"

        chapters.append({
            "title": title[:80],
            "text": text,
            "page_start": chapter_num,
        })

    return chapters


def parse_txt(filepath: str) -> list[dict]:
    """Parse plain text into chapters."""
    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()

    # Try to split by chapter markers
    chapter_pattern = re.compile(
        r"^(chapter|chapitre|الفصل|باب)\s*\d*[:\.\s]",
        re.IGNORECASE | re.MULTILINE,
    )
    splits = list(chapter_pattern.finditer(content))

    if len(splits) >= 2:
        chapters = []
        for i, match in enumerate(splits):
            start = match.start()
            end = splits[i + 1].start() if i + 1 < len(splits) else len(content)
            chunk = content[start:end].strip()
            first_line = chunk.split("\n")[0].strip()
            chapters.append({
                "title": first_line[:80],
                "text": chunk,
                "page_start": i + 1,
            })
        return chapters

    # Fall back: split into ~2000-word chunks
    words = content.split()
    chapters = []
    chunk_size = 2000
    for i in range(0, len(words), chunk_size):
        chunk = " ".join(words[i : i + chunk_size])
        idx = i // chunk_size + 1
        chapters.append({
            "title": f"Section {idx}",
            "text": chunk,
            "page_start": idx,
        })

    return chapters or [{"title": "Full Text", "text": content, "page_start": 1}]


PARSERS = {
    ".pdf": parse_pdf,
    ".epub": parse_epub,
    ".txt": parse_txt,
}

# ─── Arabic character repair ─────────────────────────────────────────────────

# Many Arabic PDFs use non-standard font encodings where the visual glyphs
# look correct (because the font is embedded) but the Unicode values are wrong.
# This map fixes the most common substitutions.
_ARABIC_CHAR_FIXES = {
    "ؾ": "ف",  # ؾ → ف (fa)
    "ٌ": None,      # ٌ — context-dependent, see below
    "ؼ": "غ",  # ؼ → غ (ghayn) if this mapping exists
    "ﭐ": "ا",  # alef variants
    "ﭑ": "ا",
}

import unicodedata

def _fix_arabic_encoding(text: str) -> str:
    """Fix broken Arabic character encodings common in PDF extraction."""
    # NFKC normalize to convert presentation forms to standard Arabic
    text = unicodedata.normalize("NFKC", text)

    # Common character substitutions in broken Arabic PDFs
    text = text.replace("ؾ", "ف")  # fa
    text = text.replace("ؼ", "غ")  # ghayn

    # ٌ (dammatan) used as base letter ي — fix in all positions
    text = re.sub(r"(?<=[؀-ۿ])ٌ(?=[؀-ۿ])", "ي", text)   # mid-word
    text = re.sub(r"(?<![؀-ۿ])ٌ(?=[؀-ۿ])", "ي", text)   # start of word
    text = re.sub(r"(?<=[؀-ۿ])ٌ(?![؀-ۿ])", "ي", text)   # end of word

    # ً (fathatan) used as ي — context-dependent
    text = re.sub(r"(?<=[؀-ۿ])ً(?=[؀-ۿ])", "ي", text)   # mid-word → always ي
    text = re.sub(r"(?<![؀-ۿ])ً(?=[؀-ۿ]{2,})", "", text) # stray before word → remove
    # End of word: ي if NOT after alef (after alef = real tanween e.g. غداً)
    text = re.sub(r"(?<=[؀-ۿ])(?<!ا)ً(?=\s|$|[^؀-ۿ])", "ي", text)

    # Normalize kashida and ligatures
    text = text.replace("ـ", "")
    text = text.replace("ﻻ", "لا")
    text = text.replace("ﻷ", "لأ")
    text = text.replace("ﻹ", "لإ")

    return text


# ─── Text cleaning ───────────────────────────────────────────────────────────

def _clean_text(text: str) -> str:
    """Clean extracted text: remove URLs, page numbers, headers, and artifacts."""
    # Fix Arabic encoding issues first
    text = _fix_arabic_encoding(text)
    # Remove URLs
    text = re.sub(r"https?://[^\s]+", "", text)
    text = re.sub(r"www\.[^\s]+", "", text)
    # Remove email addresses
    text = re.sub(r"[\w.+-]+@[\w-]+\.[\w.-]+", "", text)
    # Remove standalone numbers (page numbers, footnote refs)
    text = re.sub(r"^\s*\d+\s*$", "", text, flags=re.MULTILINE)
    # Remove repeated dashes, underscores, equals (decorative lines)
    text = re.sub(r"[_=\-]{5,}", "", text)
    # Remove common PDF artifacts like form feed, null bytes
    text = re.sub(r"[\x00\x0c]", "", text)
    # Collapse excessive whitespace
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


# ─── TTS ─────────────────────────────────────────────────────────────────────

def _chunk_text(text: str, max_chars: int = 4000) -> list[str]:
    """Split text into chunks respecting sentence boundaries."""
    sentences = re.split(r"(?<=[.!?।。؟])\s+", text)
    chunks = []
    current = ""

    for sentence in sentences:
        if len(current) + len(sentence) + 1 > max_chars:
            if current:
                chunks.append(current.strip())
            current = sentence
        else:
            current += " " + sentence

    if current.strip():
        chunks.append(current.strip())

    return chunks or [text[:max_chars]]


async def _generate_edge_tts(text: str, voice: str, rate: str, output_path: str):
    """Generate audio using edge-tts."""
    communicate = edge_tts.Communicate(text, voice, rate=rate)
    await communicate.save(output_path)


def generate_audio_edge(text: str, voice: str, speed: float = 1.0) -> str:
    """Generate audio file from text using edge-tts. Returns file path."""
    # Convert speed to rate string: 1.0 = "+0%", 1.5 = "+50%", 0.75 = "-25%"
    rate_pct = int((speed - 1.0) * 100)
    rate_str = f"{rate_pct:+d}%"

    # Create a hash-based filename for caching
    text_hash = hashlib.md5(f"{text[:200]}_{voice}_{rate_str}".encode()).hexdigest()
    output_path = str(AUDIO_DIR / f"{text_hash}.mp3")

    if os.path.exists(output_path):
        return output_path

    asyncio.run(_generate_edge_tts(text, voice, rate_str, output_path))
    return output_path


def generate_audio_openai(text: str, voice: str, speed: float, api_key: str) -> str:
    """Generate audio using OpenAI TTS API."""
    text_hash = hashlib.md5(f"{text[:200]}_openai_{voice}_{speed}".encode()).hexdigest()
    output_path = str(AUDIO_DIR / f"{text_hash}.mp3")

    if os.path.exists(output_path):
        return output_path

    client = openai.OpenAI(api_key=api_key)
    response = client.audio.speech.create(
        model="tts-1-hd",
        voice=voice,
        input=text,
        speed=speed,
    )
    response.stream_to_file(output_path)
    return output_path


# ─── In-memory book store ────────────────────────────────────────────────────

books = {}  # book_id -> {filename, chapters: [{title, text, page_start}]}

# ─── Routes ──────────────────────────────────────────────────────────────────

# ─── Email signup storage (flat-file for simplicity) ─────────────────────────
SUBSCRIBERS_FILE = Path(os.environ.get("SUBSCRIBERS_FILE", "subscribers.json"))

def _load_subscribers():
    if SUBSCRIBERS_FILE.exists():
        return json.loads(SUBSCRIBERS_FILE.read_text())
    return []

def _save_subscriber(name, email):
    subs = _load_subscribers()
    # Skip duplicates
    if any(s["email"].lower() == email.lower() for s in subs):
        return False  # already exists
    subs.append({"name": name, "email": email, "ts": str(uuid.uuid4())[:8]})
    SUBSCRIBERS_FILE.write_text(json.dumps(subs, indent=2))
    return True


# ─── Mailchimp integration (optional) ────────────────────────────────────────
MAILCHIMP_API_KEY = os.environ.get("MAILCHIMP_API_KEY", "")
MAILCHIMP_LIST_ID = os.environ.get("MAILCHIMP_LIST_ID", "")

def _add_to_mailchimp(name, email):
    """Add subscriber to Mailchimp list. Fails silently if not configured."""
    if not MAILCHIMP_API_KEY or not MAILCHIMP_LIST_ID:
        return
    try:
        import requests
        dc = MAILCHIMP_API_KEY.split("-")[-1]  # e.g. us21
        url = f"https://{dc}.api.mailchimp.com/3.0/lists/{MAILCHIMP_LIST_ID}/members"
        parts = name.split(" ", 1)
        data = {
            "email_address": email,
            "status": "subscribed",
            "merge_fields": {
                "FNAME": parts[0],
                "LNAME": parts[1] if len(parts) > 1 else "",
            },
        }
        requests.post(url, json=data, auth=("anystring", MAILCHIMP_API_KEY), timeout=10)
    except Exception:
        pass  # Don't break signup if Mailchimp is down


# When DEPLOY_MODE=1 (on Railway), "/" shows the landing page and "/app" the reader.
# Locally, "/" goes straight to the reader.
DEPLOY_MODE = os.environ.get("DEPLOY_MODE", "").strip() == "1"

@app.route("/")
def root():
    if DEPLOY_MODE:
        return render_template("landing.html")
    return render_template("index.html")

@app.route("/app")
def index():
    return render_template("index.html")


@app.route("/api/voices")
def get_voices():
    return jsonify(VOICES)


@app.route("/api/upload", methods=["POST"])
def upload_book():
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400

    file = request.files["file"]
    if not file.filename:
        return jsonify({"error": "No filename"}), 400

    ext = Path(file.filename).suffix.lower()
    if ext not in PARSERS:
        return jsonify({"error": f"Unsupported format: {ext}. Use PDF, EPUB, or TXT."}), 400

    # Save file
    book_id = str(uuid.uuid4())[:8]
    safe_name = secure_filename(file.filename)
    filepath = UPLOAD_DIR / f"{book_id}_{safe_name}"
    file.save(str(filepath))

    # Parse
    try:
        chapters = PARSERS[ext](str(filepath))
    except Exception as e:
        return jsonify({"error": f"Failed to parse book: {str(e)}"}), 500

    if not chapters:
        return jsonify({"error": "No readable text found in this file."}), 400

    books[book_id] = {
        "filename": file.filename,
        "chapters": chapters,
    }

    return jsonify({
        "book_id": book_id,
        "filename": file.filename,
        "chapters": [
            {"index": i, "title": ch["title"], "char_count": len(ch["text"])}
            for i, ch in enumerate(chapters)
        ],
        "total_chars": sum(len(ch["text"]) for ch in chapters),
    })


@app.route("/api/chapter-text/<book_id>/<int:chapter_idx>")
def get_chapter_text(book_id, chapter_idx):
    """Return the text of a chapter (for display)."""
    book = books.get(book_id)
    if not book:
        return jsonify({"error": "Book not found"}), 404
    if chapter_idx < 0 or chapter_idx >= len(book["chapters"]):
        return jsonify({"error": "Invalid chapter"}), 400
    ch = book["chapters"][chapter_idx]
    return jsonify({"title": ch["title"], "text": ch["text"]})


@app.route("/api/synthesize", methods=["POST"])
def synthesize():
    """Generate audio for a chapter or text chunk."""
    data = request.json
    book_id = data.get("book_id")
    chapter_idx = data.get("chapter_idx", 0)
    voice = data.get("voice", "en-US-AriaNeural")
    speed = data.get("speed", 1.0)
    engine = data.get("engine", "edge")
    api_key = data.get("api_key", "")

    book = books.get(book_id)
    if not book:
        return jsonify({"error": "Book not found. Please re-upload."}), 404

    if chapter_idx < 0 or chapter_idx >= len(book["chapters"]):
        return jsonify({"error": "Invalid chapter index"}), 400

    text = book["chapters"][chapter_idx]["text"]

    # Clean the text before synthesis
    text = _clean_text(text)
    # Collapse mid-sentence line breaks into spaces so TTS reads fluidly.
    # Keep paragraph breaks (double newlines) as they indicate real pauses.
    text = re.sub(r"\n\n+", "⏎⏎", text)        # protect paragraph breaks
    text = re.sub(r"\n", " ", text)              # collapse single line breaks
    text = text.replace("⏎⏎", "\n\n")           # restore paragraph breaks
    text = re.sub(r"  +", " ", text)             # clean up double spaces
    if not text:
        return jsonify({"error": "No readable text in this chapter."}), 400

    # Add diacritics to Arabic text for better TTS pronunciation
    if HAS_TASHKEEL and re.search(r"[؀-ۿ]", text):
        try:
            text = _vocalizer.tashkeel(text)
        except Exception:
            pass  # Fall through with unvocalized text

    # Split into manageable chunks for TTS
    chunks = _chunk_text(text, max_chars=4000)
    audio_files = []

    try:
        for chunk in chunks:
            if not chunk.strip():
                continue
            if engine == "openai" and HAS_OPENAI and api_key:
                # OpenAI voices: alloy, echo, fable, onyx, nova, shimmer
                openai_voice = data.get("openai_voice", "nova")
                path = generate_audio_openai(chunk, openai_voice, speed, api_key)
            else:
                path = generate_audio_edge(chunk, voice, speed)
            audio_files.append(path)
    except Exception as e:
        return jsonify({"error": f"TTS failed: {str(e)}"}), 500

    # If multiple chunks, concatenate using pydub or just return list
    if len(audio_files) == 1:
        audio_id = Path(audio_files[0]).stem
        return jsonify({"audio_id": audio_id, "parts": 1})
    else:
        # Return list of audio part IDs
        audio_ids = [Path(f).stem for f in audio_files]
        return jsonify({"audio_ids": audio_ids, "parts": len(audio_ids)})


@app.route("/api/audio/<audio_id>")
def serve_audio(audio_id):
    """Serve an audio file."""
    # Sanitize
    safe_id = re.sub(r"[^a-f0-9]", "", audio_id)
    filepath = AUDIO_DIR / f"{safe_id}.mp3"
    if not filepath.exists():
        return jsonify({"error": "Audio not found"}), 404
    return send_file(str(filepath), mimetype="audio/mpeg")


@app.route("/api/signup", methods=["POST"])
def signup():
    """Handle email signup from landing page."""
    data = request.get_json()
    name = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip()
    if not name or not email or "@" not in email:
        return jsonify({"ok": False, "error": "Please provide a valid name and email."}), 400
    _save_subscriber(name, email)
    _add_to_mailchimp(name, email)
    return jsonify({"ok": True})


@app.route("/api/clear-cache", methods=["POST"])
def clear_cache():
    """Clear audio cache."""
    count = 0
    for f in AUDIO_DIR.glob("*.mp3"):
        f.unlink()
        count += 1
    return jsonify({"cleared": count})


if __name__ == "__main__":
    print("\n📖 BookVoice is running at http://localhost:8080\n")
    app.run(host="0.0.0.0", port=8080, debug=True)
