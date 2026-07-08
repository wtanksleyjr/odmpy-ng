#!/usr/bin/env python3
"""
ffmpeg metadata file writer.
"""
import os
import sys
import re
import json
from convert_metadata import to_seconds, to_hms

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

        actual_audio_duration = 0.0
        for fname in ["temp.mka", "temp.m4b"]:
            audio_path = os.path.join(tmp_dir, fname)
            if os.path.exists(audio_path):
                try:
                    from file_conversions import get_audio_duration
                    actual_audio_duration = get_audio_duration(audio_path)
                    if actual_audio_duration > 0:
                        print(f"Detected actual concatenated audio file duration: {actual_audio_duration:.2f}s ({to_hms(int(actual_audio_duration))})")
                except Exception as e:
                    print(f"Warning: Could not check actual audio duration: {e}")
                break

        # Check total chapter duration vs actual audio duration for an early warning alert!
        if actual_audio_duration > 0.0 and book_chapter_markers:
            try:
                last_ch = book_chapter_markers[-1]
                expected_end = float(to_seconds(str(last_ch[2])))
                discrepancy = abs(expected_end - actual_audio_duration)
                if discrepancy > 15.0:
                    print("\n" + "="*80)
                    print("⚠️  WARNING: POTENTIAL AUDIOBOOK TRANSCRIPTION OR DOWNLOAD CORRUPTION DETECTED!  ⚠️")
                    print(f"Expected metadata end: {to_hms(int(expected_end))} ({expected_end:.1f}s)")
                    print(f"Actual audio file duration: {to_hms(int(actual_audio_duration))} ({actual_audio_duration:.1f}s)")
                    print(f"Absolute discrepancy: {discrepancy:.1f} seconds.")
                    if actual_audio_duration < expected_end - 15.0:
                        print("The actual audio file is significantly SHORTER than the book's chapter markers indicate!")
                        print("Please double check if some MP3 parts failed to download or were truncated during scraping.")
                    else:
                        print("The actual audio file is significantly LONGER than the book's chapter markers indicate!")
                    print("="*80 + "\n")
                    sys.exit(1)
            except Exception as check_err:
                print(f"Warning: Could not compute discrepancy checking: {check_err}")

        for marker in book_chapter_markers:
            title_marker, start, end = marker

            # Safely parse start and end times to float seconds, then convert to milliseconds
            try:
                start_sec = float(to_seconds(str(start)))
            except Exception:
                start_sec = 0.0

            try:
                end_sec = float(to_seconds(str(end)))
            except Exception:
                end_sec = 0.0

            # Check if start/end are within the bounds of the actual audio file
            if actual_audio_duration > 0.0:
                if start_sec >= actual_audio_duration:
                    print(f"WARNING: Skipping chapter '{title_marker}' starting at {to_hms(int(start_sec))} ({start_sec}s), "
                          f"which is after/at actual audio duration {to_hms(int(actual_audio_duration))} ({actual_audio_duration}s).")
                    continue
                if end_sec > actual_audio_duration:
                    print(f"WARNING: Truncating chapter '{title_marker}' end time from {to_hms(int(end_sec))} ({end_sec}s) "
                          f"to actual audio duration {to_hms(int(actual_audio_duration))} ({actual_audio_duration}s).")
                    end_sec = actual_audio_duration

            if start_sec >= end_sec:
                # Skip invalid zero-duration or negative chapters
                continue

            start_ms = int(start_sec * 1000)
            end_ms = int(end_sec * 1000)

            f.write("[CHAPTER]\n")
            f.write("TIMEBASE=1/1000\n")
            f.write(f"START={start_ms}\n")
            f.write(f"END={end_ms}\n")
            f.write(f"title={escape_val(title_marker)}\n")
            f.write("\n")
