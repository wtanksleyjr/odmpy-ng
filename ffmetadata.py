#!/usr/bin/env python3
"""
ffmpeg metadata file writer.
"""
import os
import re
import json
from convert_metadata import to_seconds

def escape_val(val) -> str:
    if not val:
        return ""
    val_str = str(val).replace('\r\n', '\n')
    escaped = ""
    for char in val_str:
        if char in ('=', ';', '#', '\\'):
            escaped += '\\' + char
        elif char == '\n':
            escaped += '\\\n'
        else:
            escaped += char
    return escaped

def write_metafile(tmp_dir, book_chapter_markers, book_title, book_author):
    meta_path = os.path.join(tmp_dir, "ffmetadata")

    # Try to load overdrive thunder metadata if it exists
    info_data = {}
    info_path = os.path.join(tmp_dir, "info.json")
    if os.path.exists(info_path):
        try:
            with open(info_path, "r", encoding="utf-8") as f:
                info_data = json.load(f)
        except Exception as e:
            print(f"Warning: could not read info.json for metadata enrichment: {e}")

    title = info_data.get("title") or book_title
    subtitle = info_data.get("subtitle")
    if subtitle:
        title = f"{title}: {subtitle}"

    # Extract creators (Authors and Narrators)
    authors = []
    narrators = []
    creators = info_data.get("creators", [])
    if isinstance(creators, list):
        for c in creators:
            if isinstance(c, dict):
                name = c.get("name", "").strip()
                role = c.get("role", "").strip().lower()
                if name:
                    if role == "author":
                        authors.append(name)
                    elif role == "narrator":
                        narrators.append(name)

    author_str = ", ".join(authors) if authors else book_author
    narrator_str = ", ".join(narrators) if narrators else ""

    # Extract publishers
    publishers_list = []
    publishers = info_data.get("publishers", [])
    if isinstance(publishers, list):
        for p in publishers:
            if isinstance(p, dict):
                name = p.get("name", "").strip()
                if name:
                    publishers_list.append(name)
    publisher_str = ", ".join(publishers_list) if publishers_list else ""

    # Extract genres / subjects
    subjects_list = []
    subjects = info_data.get("subjects", [])
    if isinstance(subjects, list):
        for s in subjects:
            if isinstance(s, dict):
                name = s.get("name", "").strip()
                if name:
                    subjects_list.append(name)
    genre_str = ", ".join(subjects_list) if subjects_list else ""

    # Extract publish/release date
    date_str = info_data.get("firstPublishDate") or info_data.get("publishDate") or info_data.get("releaseDate") or ""

    # Extract and clean up HTML description
    raw_desc = info_data.get("description", "")
    desc_str = ""
    if raw_desc:
        # Strip HTML tags
        clean_desc = re.sub(r'<[^<]+?>', '', raw_desc)
        # Normalize whitespace
        desc_str = " ".join(clean_desc.split())

    with open(meta_path, "w", encoding="utf-8") as f:
        f.write(";FFMETADATA1\n")
        f.write(f"title={escape_val(title)}\n")
        f.write(f"artist={escape_val(author_str)}\n")
        f.write(f"album_artist={escape_val(author_str)}\n")
        f.write(f"album={escape_val(book_title)}\n")
        if narrator_str:
            f.write(f"composer={escape_val(narrator_str)}\n")
        if genre_str:
            f.write(f"genre={escape_val(genre_str)}\n")
        if publisher_str:
            f.write(f"publisher={escape_val(publisher_str)}\n")
        if date_str:
            f.write(f"date={escape_val(date_str)}\n")
        if desc_str:
            f.write(f"comment={escape_val(desc_str)}\n")
            f.write(f"description={escape_val(desc_str)}\n")
        f.write("\n")

        for marker in book_chapter_markers:
            title_marker, start, end = marker

            # Safely parse start and end times to float seconds, then convert to milliseconds
            try:
                start_sec = to_seconds(str(start))
            except Exception:
                start_sec = 0.0

            try:
                end_sec = to_seconds(str(end))
            except Exception:
                end_sec = 0.0

            start_ms = int(start_sec * 1000)
            end_ms = int(end_sec * 1000)

            f.write("[CHAPTER]\n")
            f.write("TIMEBASE=1/1000\n")
            f.write(f"START={start_ms}\n")
            f.write(f"END={end_ms}\n")
            f.write(f"title={escape_val(title_marker)}\n")
            f.write("\n")
