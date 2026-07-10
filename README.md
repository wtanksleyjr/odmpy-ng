# odmpy-ng
**OverDrive Manager Next Generation** — a tool for downloading and organizing audiobooks from OverDrive.
⚠️ **Use at your own risk and in compliance with local law. Requires a valid library account.**

---

## Features

- Interactive or Docker-based audiobook downloader for OverDrive
- Scrapes audio URLs, chapters, and cover images
- Converts MP3s into .m4b or .mka files with embedded chapter metadata
- Generates metadata.json compatible with Audiobookshelf
- Customizable and automated scraping via Playwright
- Fully command-line compatible (one book per call or automatic fetching of new books)

---

## Running

Before running, you must configure the tool with a configuration file, see
Configuration section below.

### Option 1: Run with Docker Compose

This only needs to be told the path to the books ouptut directory
(and that can be provided by an environment variable, AUDIOBOOK_FOLDER).

Use `./build-compose.py` to build the docker-compose file.

See `./build-compose.py run --help` for odmpy-ng options.

Requirements:
- Docker Compose (note that the older docker-compose is not supported)
- Git
- Python 3.9+ on your host computer

```bash
git clone https://github.com/kernalbin/odmpy-ng.git
cd odmpy-ng
cp config/config.example.json config/config.json
# edit config/config.json
./build-compose.py -d ~/audiobooks -t ~/mytmp run
```

The above session will (if all is well) allow you to pick out which
library, and then let you pick a book from it. A more powerful option
is:

```bash
export AUDIOBOOK_FOLDER=~/audiobooks
export AUDIOBOOK_TMP=~/mytmp
./build-compose.py
./build-compose.py run -L all
```

Save those environment variables for future use (the rest of this
session will assume they're present); you want to store
your books in the same place so this can manage your downloads. For
the ultimate in automation, try the -a option:

```bash
./build-compose.py run -L all -a 5
```

The above session will scan all of your libraries and download the 5
undownloaded books closest to expiring automatically. If you have more
than 5 books you can run it again, or pass a larger number. If you don't
know, run the following to be told without downloading anything:

```bash
./build-compose.py run -L all -a
```

---

### Option 3: Run Locally

Running locally is fully supported on Windows, macOS, and Linux. Playwright runs perfectly in a local environment, but requires its browser binaries to be installed after installing the python requirements.

Requirements:
- Python 3.9+
- [ffmpeg](https://ffmpeg.org/download.html)
- `pip install -r requirements.txt`
- Playwright Chromium browser binaries

```bash
git clone https://github.com/kernalbin/odmpy-ng.git
cd odmpy-ng
pip install -r requirements.txt
playwright install chromium
python interactive.py [config_file_path]
```

> **Note:** If you forget to run `playwright install chromium`, Playwright will display a helpful message instructing you to run it.

---

## Configuration

You'll need to create a configuration file with your library's OverDrive URL and login credentials.  
Example `config.json` (saved as `config/config.json`):

```json
{
    "libraries": [
        {
            "name": "Example Library",
            "url": "https://yourlibrary.overdrive.com",
            "card_number": "YOUR_CARD_NUMBER",
            "pin": "YOUR_PIN",
            "sublibrary": "Your Sublibrary"
        }
    ],
    "book_dir_template": "{author}/{title} - {id}",
    "low_quality_encode": 0,
    "encoding": "aac",
    "download_thunder_metadata": 0,
    "convert_audiobookshelf_metadata": 0,
    "abort_on_warning": 0,
    "skip_reencode": 0,
    "encoder_count": 4
}
```

### Config Options Description

- **`book_dir_template`**: Template for output folders relative to `/downloads`. Supports `{id}`, `{title}`, and `{author}` wildcards. Defaults to `"{author}/{title}"` if omitted.
- **`sublibrary`**: Needed if your library is part of a cooperative. Enter the name of the sublibrary as it appears on the Overdrive library login page. If not needed, you can omit this key.
- **`encoding`**: `"aac"` or `"mka"`. The former is more commonly supported, the latter is much faster, keeps the original audio quality, and works with Audiobookshelf.
- **`skip_reencode`**: Set to `1` to download and assemble the audiobook without re-encoding, which runs significantly faster but leaves the book as multiple MP3 files.

See the provided `config.example.json` file for more details.

---

## Command Line Options

A quick look at the command line options:

```bash
$ ./build-compose.py -d ~/audiobooks run --help
Starting ODMPY-NG
usage: interactive.py [-h] [--id ID] [--retry] [--get-metadata] [--force] [--autofetch [AUTOFETCH]] [--library LIBRARY] config_file

positional arguments:
  config_file           Path to config file

options:
  -h, --help            show this help message and exit
  --id ID, -i ID        Libby ID for a single book to download
  --retry, -r           Allow retry of stopped downloads (if left in tmp dir)
  --get-metadata        Get metadata only for all indicated books, do not download
  --force, -f           Allow selection/re-download of already downloaded books and replace their contents
  --autofetch [AUTOFETCH], -a [AUTOFETCH]
                        Automatically download at most the N most about-to-expire books (if N is specified)
  --library LIBRARY, -L LIBRARY
                        Index of library within config to download from, or 'all'
```

This demo shows a run of the builder, which has three options you need to know
about: `-d`/`--download-base`, `-t`/`--tmp-dir`, and `run`. The first is the
location of the output directory for the downloaded files, the second allows a
different folder to be used for in-progress downloads, and the third allows
you, after building, to actually run the audiobook downloader. Any options
following `run` will be passed to the downloader (note: they're all optional!).
For frequent use, `-d` can be omitted if `AUDIOBOOK_FOLDER` is set in your
environment, and `-t` can be specified with `AUDIOBOOK_TMP` or allowed to default
to the `tmp` subfolder of your download folder, leaving a very simple `run`
command.

Once you've used the downloader a few times and checked that it puts files in
the right places, you might want to check out the options. You can see the help
for them above, but here's a table with some brief descriptions:

| Option                | Description |
|-----------------------|-------------|
| `-i`, `--id`              | Libby ID for a single book to download. You can see this from your library's webpage for the book. |
| `-r`, `--retry`           | Allow retry of stopped downloads (if left in tmp dir). You can enable this after a download fails; cleanup happens before the run, not after. |
| `-L`, `--library`         | If you have multiple libraries in your config, you can specify which one to download from, counted from 0 (or `"all"` to process all libraries). |
| `-a`, `--autofetch` [N or ""]         | Automatically download at most the N most about-to-expire books (if N is specified, otherwise display which books and exit). |

---

## Project Structure

| File / Script             | Description |
|--------------------------|-------------|
| `build-compose.py`       | Main entry point to run on your local machine, builds the rest |
| `interactive.py`         | Main entry point to run within Docker, parses command line and config file and runs the rest |
| `scraper.py`             | Scrapes OverDrive for audio, chapter, and cover metadata |
| `overdrive_download.py`  | Downloads MP3 parts using scraped info and cookies |
| `ffmetadata.py`          | Creates chapter and metadata file for m4b embedding |
| `file_conversions.py`    | Converts MP3s into mka or m4b with AAC and metadata |
| `Dockerfile`             | Docker setup using Playwright Chrome base image |
| `docker-compose.yml`     | Docker compose file for running in Docker |
| `entrypoint.sh`          | Entrypoint script for Docker container |

---

## Roadmap

- [X] Batch download multiple books  
- [X] Support for branch libraries
- [ ] Support ebooks

---

## ⚠️ Disclaimer

This tool is intended for personal use only, and the user is responsible to adhere to the Terms of Service for Libby and Overdrive.
You must have a valid library account with OverDrive access.

> Use responsibly. The maintainers are not responsible for any misuse or violation of OverDrive’s terms of service.
