"""
ODMPY-NG: OverDrive audiobook download and conversion tool
"""

import argparse
import json
import os
import string
import shutil
import sys
import pathlib
import ffmetadata
from scraper import Scraper, Cookies
import file_conversions
import convert_metadata

# Convert user entered string into a list of valid book indexes
# Allows for comma separated items and dash separated ranges
def parse_book_selection_input(userinput: str, books: list) -> list[int]:
    """
    Parses a comma-separated and range-based string into a list of valid book indexes.

    Args:
        userinput (str): String input from user selecting books.
        books (list): Complete list of books to validate against

    Returns:
        set: Parsed ordered list of selected books
    """
    userinput.strip()
    if not userinput:
        return []

    parts_set = set()
    parts = userinput.split(',')

    valid_indexes = {book["index"] for book in books}

    for part in parts:
        part = part.strip()
        if '-' in part:
            parts = part.split('-')
            if all(part.isdigit() for part in parts):
                start, end = map(int, part.split('-'))
                parts_set.update(range(start, end+1))
            else:
                raise ValueError(f"Invalid range input: {part}")
        else:
            if part.isdigit():
                parts_set.add(int(part))
            else:
                raise ValueError(f"Invalid integer input: {part}")

    return sorted(parts_set.intersection(valid_indexes))

def get_book_by_index(index: int, books: list):
    """
        Retrieves a book dictionary by index from a list of books.

        Args:
            index (int): Index in book list
            books (list): Complete list of books to search

        Returns:
            dict: Book info for given index
    """
    return next((b for b in books if b["index"] == index), None)

def main():
    print("Starting ODMPY-NG")

    # Command line parsing
    parser = argparse.ArgumentParser()
    parser.add_argument("config_file", type=str, help="Path to config file")
    parser.add_argument("--id", "-i", type=int, help="Libby ID for a single book to download")
    parser.add_argument("--retry", "-r", action="store_true", help="Allow retry of stopped downloads (if left in tmp dir)")
    parser.add_argument("--name-dir", "-n", type=str, help="Fixed subdirectory relative to /downloads to move single downloaded book to")
    parser.add_argument("--get-metadata", action="store_true", help="Get metadata only for all indicated books, do not download.")
    # These two are mutually exclusive
    exclusive_group = parser.add_mutually_exclusive_group(required=False)
    exclusive_group.add_argument("--library", "-L", type=int, help="Index of library within config to download from")
    exclusive_group.add_argument("--site-id", "-s", type=int, help="Site-Id assigned in config to library to download from")
    args = parser.parse_args()

    if not os.path.exists(args.config_file):
        print(f"Error: Config file '{args.config_file}' not found")
        sys.exit(1)

    config_file = args.config_file
    if os.path.isfile(config_file):
        with open(config_file) as f:
            try:
                config = json.load(f)
            except json.JSONDecodeError:
                print(f"Error: Config file '{config_file}' is not valid JSON")
                sys.exit(1)
    else:
        print(f"Error: Config file '{config_file}' not found")
        sys.exit(1)

    config_dir = os.path.dirname(config_file)
    if not os.path.exists(config_dir):
        print(f"Error: Config directory '{config_dir}' not found")
        sys.exit(1)

    downloads_dir = pathlib.Path("/downloads")
    tmp_base = pathlib.Path("/tmp-downloads")
    tmp_base.mkdir(parents=True, exist_ok=True)

    cookies = Cookies([])
    cookie_file = os.path.join(config_dir, "cookies")
    if os.path.exists(cookie_file):
        try:
            with open(cookie_file) as f:
                loaded = json.load(f)
            # Old cookie file used a dict; new is a list so add a version check.
            if not isinstance(loaded, list) or not loaded or loaded[0] != 1:
                print("Warning: old cookie file, ignoring.")
            else:
                cookies = Cookies.read_loaded(loaded)
        except Exception as e:
            print(f"Warning: error loading cookies, will ignore: {e}")
            cookies = Cookies([])

    print("Config loaded")

    if config.get("low_quality_encode", 0):
        print("WARNING: Low quality mode set, 32k audio encodes.")

    libraries = config.get("libraries", [])
    if not libraries:
        print("No libraries found, did you create a valid config file?")
        sys.exit(1)

    # Enforce unique site-ids in libraries (except None is fine)
    site_ids = [s for library in libraries if (s := library.get("site-id")) is not None]
    if len(site_ids) != len(set(site_ids)):
        print(f"Error: site-ids must be unique within libraries, please edit {config_file}")
        sys.exit(1)

    library_index = None
    print("\nAvailable libraries:")
    for i, library in enumerate(libraries):
        visible_marker = "    "
        if args.library is not None:
            if i == args.library:
                visible_marker = " -> "
                library_index = i
        elif args.site_id is not None:
            if library.get("site-id") == args.site_id:
                visible_marker = " -> "
                library_index = i
        else:
            visible_marker = f"{i:>3}:"
        print(f"{visible_marker} {library['name']} - {library['url']}")

    if library_index is None and args.library is not None:
        print(f"Error: Library {args.library} not found in config")
        sys.exit(1)
    if library_index is None and args.site_id is not None:
        print(f"Error: Library matching site-id {args.site_id} not found in config")
        sys.exit(1)

    if len(libraries) == 1:
        # Only one library, automatically select it
        library_index = 0
    elif library_index is None:
        # Let user select which library to use
        library_text = input("\nSelect a library to use: ")
        if not library_text:
            sys.exit(0) # Easy polite exit
        elif not library_text.isdigit():
            library_index = None
        library_index = int(library_text)

    if library_index is None or library_index < 0 or library_index >= len(libraries):
        print("Invalid library selection")
        sys.exit(1)

    # Create a compatible config object for the scraper
    selected_library = libraries[library_index]
    scraper_config = {
        "library": selected_library["url"],
        "user": selected_library["card_number"],
        "pass": selected_library["pin"],
        "tmp-dir": None, # to be filled in later
        "allow-retry": args.retry,
        "id": args.id,
        "get-metadata": args.get_metadata,
    }
    if "sublibrary" in selected_library:
        scraper_config["sublibrary"] = selected_library["sublibrary"]

    os.makedirs(downloads_dir, mode=0o755, exist_ok=True)

    print(f"Using library: {selected_library['name']}")

    scraper = Scraper(scraper_config, cookies)
    new_cookies: Cookies = scraper.ensure_login()

    if not new_cookies:
        print("Sign in failed")
        sys.exit(1)
    else:
        print("Sign in successful")

    # Update cookie file from the login - may overwrite later, but for now save the login.
    new_cookies.write_to_file(cookie_file)

    # Collect list of loans
    books = scraper.get_loans() # [{"index": 0, "title": "", "author": "", "link": "", "id": 0}]

    # Print loans for selection by user
    title_selections = []

    find_id = str(scraper_config["id"]) if scraper_config["id"] else ''
    for book in books:
        this_one = False
        if book["id"] == find_id:
            title_selections.append(book["index"])
            this_one = True

        visible_marker = "->" if this_one else "  "
        print(f"{visible_marker} {book['index']}: {book['title']} - {book['author']} ({book['id']})")

    if not title_selections and books:
        assert not find_id, f"Libby shows checkout of {find_id} but was not found in books"
        selections_input = input("Select a title to download (e.g., 0,1,2-3): ")
        title_selections = parse_book_selection_input(selections_input, books)

    if not title_selections:
        print("No books selected")
        sys.exit(1)

    if args.name_dir and len(title_selections) > 1:
        print("ERROR: Cannot use --name-dir with multiple books")
        sys.exit(1)

    # For each selected book, get the data
    for title_index in title_selections:
        # Get book selection from index
        book_selection = get_book_by_index(title_index, books)
        if not book_selection:
            print(f"ERROR: Invalid book selection, should not happen: {title_index}")
            continue

        # Create tmp directory with absolute path, one for each book.
        tmp_dir = tmp_base / book_selection["id"]
        scraper_config["tmp-dir"] = str(tmp_dir)

        # Check if progress markers exist to decide on directory cleanup
        has_markers = False
        if os.path.exists(tmp_dir):
            marker_files = [
                "download_completed.marker",
                "encode_completed.marker",
                "concat_completed.marker",
                "metadata_generated.marker",
                "metadata_encoded.marker"
            ]
            has_markers = any((tmp_dir / marker).exists() for marker in marker_files)

        if os.path.exists(tmp_dir) and not scraper_config.get("allow-retry") and not has_markers:
            print(f"Removing old temporary directory (no progress markers found): {tmp_dir}")
            shutil.rmtree(tmp_dir)

        tmp_dir.mkdir(parents=True, exist_ok=True)

        print(f"Accessing {book_selection['title']}, ID: {book_selection['id']}")

        # Step 1: Download Step (save a marker of its progress, so that the step can be skipped if already run)
        download_marker = tmp_dir / "download_completed.marker"
        markers_json = tmp_dir / "chapter_markers.json"
        book_chapter_markers = None

        if download_marker.exists() and markers_json.exists():
            print(f"Existing download marker found for '{book_selection['title']}'. Restoring progress...")
            try:
                with open(markers_json, "r", encoding="utf-8") as f:
                    loaded_markers = json.load(f)
                # Reconstruct list of tuples (str, int, int)
                book_chapter_markers = [tuple(item) for item in loaded_markers]
                print(f"Successfully restored {len(book_chapter_markers)} chapter markers.")
            except Exception as e:
                print(f"Warning: Failed to load chapter markers from {markers_json}: {e}. Will re-download.")
                book_chapter_markers = None

        if not book_chapter_markers:
            print(f"Downloading book '{book_selection['title']}'...")
            book_chapter_markers = scraper.get_book(book_selection, tmp_dir, scraper_config)
            if not book_chapter_markers:
                print("Failed to download book.")
                continue # Do not clean up tmp_dir so user can retry

            # Save progress markers
            try:
                with open(markers_json, "w", encoding="utf-8") as f:
                    json.dump(book_chapter_markers, f, ensure_ascii=False, indent=4)
                download_marker.touch()
                print("Download progress saved.")
            except Exception as e:
                print(f"Warning: Could not save download progress markers: {e}")

        # Reformat returned tuple for easier readability
        book_title = book_selection["title"]
        book_author = book_selection["author"]

        filter_table = str.maketrans(dict.fromkeys(string.punctuation))

        if args.name_dir:
            download_path = os.path.abspath(os.path.join(downloads_dir, args.name_dir))
        else:
            # Filter to remove punctuation from book title/author for file path
            download_path = os.path.abspath(os.path.join(
                downloads_dir,
                book_author.translate(filter_table),
                book_title.translate(filter_table)
            ))

        os.makedirs(download_path, exist_ok=True)

        if config.get("convert_audiobookshelf_metadata", 0):
            chs = convert_metadata.convert_odm_to_abs_chapters(book_chapter_markers)
            abs_metadata_path = pathlib.Path(download_path) / 'metadata.json'

            tmp_info_path = tmp_dir / 'info.json'
            if tmp_info_path.exists():
                print("Converting thunder metadata to ABS format")
                temp_info_path = pathlib.Path(shutil.copy(tmp_info_path, download_path))
                convert_metadata.convert_odm_to_abs(temp_info_path, chs, abs_metadata_path, title=book_title, author=book_author)
                if config.get("download_thunder_metadata", 0):
                    print("Downloaded json metadata")
                else:
                    os.unlink(temp_info_path)
            else:
                print("No thunder metadata found")
                convert_metadata.convert_odm_to_abs(None, chs, abs_metadata_path, title=book_title, author=book_author)

            print("Provided audiobookshelf metadata")
        elif config.get("download_thunder_metadata", 0):
            tmp_info_path = tmp_dir / 'info.json'
            if tmp_info_path.exists():
                shutil.copy(tmp_info_path, download_path)
                print("Downloaded json metadata")

        skip_reencode = config.get("skip_reencode", 0) or config.get("skip_encoding", 0)
        is_mka = (config.get("encoding") == "mka")

        if skip_reencode:
            # Just copy everything to the dest.
            source, dest = pathlib.Path(tmp_dir), pathlib.Path(download_path)
            for p in source.iterdir():
                if p.is_file() and not p.name.endswith(".marker") and p.name != "chapter_markers.json":
                    shutil.copy(p, dest)
        else:
            # Determine format and output file extension
            ext = ".mka" if is_mka else ".m4b"
            temp_filename = "temp.mka" if is_mka else "temp.m4b"

            # Step 2: Encode MP3 files to AAC M4B (skip if mka)
            if is_mka:
                print("MKA mode selected. Skipping AAC encoding step to preserve original MP3 streams.")
            else:
                encode_marker = tmp_dir / "encode_completed.marker"
                if encode_marker.exists():
                    print("Encoding to AAC already completed. Skipping encoding step.")
                else:
                    print("Converting all files to AAC M4B...")
                    encode_success = file_conversions.encode_aac_multiprocessing(
                        tmp_dir, tmp_dir, config.get("low_quality_encode", 0), config.get("encoder_count", 4)
                    )
                    if not encode_success:
                        print("ERROR: Converting all files to AAC M4B failed.")
                        continue # Do NOT clean up tmp_dir so we can recover / retry

                    try:
                        encode_marker.touch()
                        print("Encoding progress saved.")
                    except Exception as e:
                        print(f"Warning: Could not save encoding progress marker: {e}")

                # Clean up original MP3 files (old step) AFTER encoding succeeds (no step loses info early)
                for mp3_file in tmp_dir.glob("*.mp3"):
                    try:
                        os.unlink(mp3_file)
                        print(f"Cleaned up original MP3 file: {mp3_file.name}")
                    except Exception as e:
                        print(f"Warning: Could not remove MP3 file {mp3_file.name}: {e}")

            # Step 3: Concatenate files to temp file
            concat_marker = tmp_dir / "concat_completed.marker"
            if concat_marker.exists() and (tmp_dir / temp_filename).exists():
                print(f"Concatenation already completed. Skipping concatenation step.")
            else:
                print(f"Converting to single {ext[1:].upper()} (concatenating)...")
                # Remove any partial temp file from a previous failed run before starting concatenation
                temp_file_path = tmp_dir / temp_filename
                if temp_file_path.exists():
                    try:
                        os.unlink(temp_file_path)
                    except Exception:
                        pass

                if is_mka:
                    concat_success = file_conversions.concat_mka(tmp_dir, tmp_dir, 'temp.mka')
                else:
                    concat_success = file_conversions.concat_m4b(tmp_dir, tmp_dir, 'temp.m4b')

                if not concat_success:
                    print(f"ERROR: Converted to single {ext[1:].upper()} failed.")
                    continue # Do NOT clean up tmp_dir so we can retry concat without re-download/re-encode!

                try:
                    concat_marker.touch()
                    print("Concatenation progress saved.")
                except Exception as e:
                    print(f"Warning: Could not save concatenation progress marker: {e}")

            # Clean up individual part files (old step) AFTER concatenation succeeds
            if is_mka:
                for part_file in tmp_dir.glob("*.mp3"):
                    if part_file.name != "temp.mp3" and part_file.is_file():
                        try:
                            os.unlink(part_file)
                            print(f"Cleaned up individual part file: {part_file.name}")
                        except Exception as e:
                            print(f"Warning: Could not remove individual part file {part_file.name}: {e}")
            else:
                for part_file in list(tmp_dir.glob("*.m4b")) + list(tmp_dir.glob("*.m4a")):
                    if part_file.name != "temp.m4b" and part_file.is_file():
                        try:
                            os.unlink(part_file)
                            print(f"Cleaned up individual part file: {part_file.name}")
                        except Exception as e:
                            print(f"Warning: Could not remove individual part file {part_file.name}: {e}")

            # Step 4: Generate metadata file
            metadata_marker = tmp_dir / "metadata_generated.marker"
            if metadata_marker.exists() and (tmp_dir / "ffmetadata").exists():
                print("Metadata file generation already completed. Skipping.")
            else:
                print("Generating metadata...")
                try:
                    ffmetadata.write_metafile(tmp_dir, book_chapter_markers, book_title, book_author)
                    metadata_marker.touch()
                except Exception as e:
                    print(f"ERROR: Generating metadata failed: {e}")
                    continue # Do NOT clean up tmp_dir

            # Step 5: Add metadata to audiobook
            metadata_encoded_marker = tmp_dir / "metadata_encoded.marker"
            cover_path = os.path.abspath(os.path.join(tmp_dir, "cover.jpg"))
            sanitized_title = book_title.translate(filter_table).replace(" ", "")
            output_file = os.path.abspath(os.path.join(download_path, sanitized_title + ext))

            if metadata_encoded_marker.exists() and os.path.exists(output_file):
                print("Adding metadata to audiobook already completed. Skipping.")
            else:
                print("Adding metadata to audiobook...")
                if os.path.exists(output_file):
                    try:
                        os.unlink(output_file)
                    except Exception:
                        pass

                encode_meta_success = file_conversions.encode_metadata(tmp_dir, temp_filename, output_file, "ffmetadata", cover_path)
                if not encode_meta_success:
                    print("ERROR: Adding metadata to audiobook failed.")
                    continue # Do NOT clean up tmp_dir

                try:
                    metadata_encoded_marker.touch()
                    print("Finished file created successfully.")
                except Exception as e:
                    print(f"Warning: Could not save final metadata progress marker: {e}")

        # Clean up temporary files only when the entire pipeline for this book is completed successfully
        try:
            shutil.rmtree(tmp_dir)
            print("Temporary files cleaned up successfully")
        except Exception as e:
            print(f"Warning: Could not remove temporary directory: {e}")

    # Update cookie file from the login and all operations.
    scraper.get_cookies().write_to_file(cookie_file)

    del scraper

def overdrive_chapters_to_abs(odm_chs: list[tuple[str, int, int]]):
    chapters = []
    for i, ch in enumerate(odm_chs):
        title, start, end = ch
        chapters.append({'id': i, 'title': title, 'start': start, 'end': end})
    return chapters

if __name__ == "__main__":
    main()
