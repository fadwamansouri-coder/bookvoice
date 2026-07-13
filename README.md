# BookVoice — AI Book Reader

A personal AI-powered book reader that reads your books aloud in **English**, **French**, or **Arabic** with state-of-the-art neural voices.

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Run the app
python app.py

# 3. Open in your browser
# → http://localhost:5000
```

## Features

- **Formats**: PDF, EPUB, plain text
- **Languages**: English (US/UK/AU), French (France/Canada), Arabic (Saudi/Egypt/Algeria/Morocco)
- **TTS Engines**:
  - **Edge TTS** (default) — Free, no API key needed, excellent neural voices
  - **OpenAI TTS-HD** (optional) — Premium quality, requires API key (~$15/1M characters)
- **Controls**: Play/pause, skip ±10s, chapter navigation, speed (0.5×–2×), progress bar scrubbing, text follow-along
- **Arabic support**: RTL text display, Amiri font, dialect-specific voices (MSA, Egyptian, Algerian, Moroccan)
- **Auto-advance**: Automatically moves to the next chapter when one finishes
- **Audio caching**: Generated audio is cached to avoid re-synthesis

## How It Works

1. Drop a book file into the browser
2. Pick your language and voice
3. Click play — the app synthesizes speech chapter by chapter
4. Use the text panel to follow along as it reads

## Notes

- First playback of a chapter takes a moment to synthesize (cached after that)
- For Arabic books, select the Arabic language *before* playing to get the correct voice
- Edge TTS requires an internet connection (uses Microsoft's free neural TTS API)
- OpenAI TTS requires a valid API key entered in the app
