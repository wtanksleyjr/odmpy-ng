#!/usr/bin/env python3
"""
Converted Overdrive Audiobook Scraper using Playwright's Synchronous API.
"""
from dataclasses import dataclass, field, InitVar
from typing import Tuple, Any, Optional
import os
import io
import time
import sys
import json
import pathlib
import convert_metadata
from convert_metadata import to_hms
from atomicwrites import atomic_write
from playwright.sync_api import sync_playwright
import overdrive_download

class Keys:
    ARROW_LEFT = "ArrowLeft"
    ARROW_RIGHT = "ArrowRight"
    PAGE_UP = "PageUp"
    PAGE_DOWN = "PageDown"
    ESCAPE = "Escape"

def normalize_sublibrary_name(library: str) -> str:
    "Make a token like the api's subsite name from the user-friendly name"
    return "".join(library.split()).lower()

# return the length of the longest prefix of s1 that is found anywhere in s2
def matching_length(s1: str, s2: str) -> int:
    """
        matching_length:
            Returns the length of the longest prefix of s1 that is found anywhere in s2
    """
    return max(len(s1[:i]) for i in range(len(s1) + 1) if s1[:i] in normalize_sublibrary_name(s2))

# Find which single item in a list matches as much of the target string as possible
def find_biggest_match_index(target: str, candidates: list[str]) -> int|None:
    if not candidates:
        return None
    if len(candidates) == 1:
        return 0
    rankings = sorted(((matching_length(target, candidate), idx, candidate) for idx, candidate in enumerate(candidates)), reverse=True)
    if rankings[0][0] == rankings[1][0]:
        print(f"WARNING: Found more than 1 equally good candidates for '{target}': {rankings[:2]}")
        return None
    print(f"Found best candidate for '{target}': {rankings[0][2]} at index {rankings[0][1]}")
    return rankings[0][1]

@dataclass
class Cookies:
    cookies: Any  # an opaque type; we don't care what it is except it's JSON storable.
    version: int = 1

    def __post_init__(self):
        if not self.cookies:
            self.version = 0

    def __bool__(self):
        return self.version == 1 and bool(self.cookies)

    def write_to_file(self, filename):
        if self:
            with atomic_write(filename, overwrite=True) as f:
                f.write(json.dumps([self.version, self.cookies]))

    @classmethod
    def read_loaded(cls, loaded):
        if len(loaded) != 2:
            return cls([])
        version, cookies = loaded
        return cls(cookies, version)

    def insert_into_context(self, context):
        if self:
            context.add_cookies(self.cookies)

class Scraper:
    """Automated Overdrive audiobook downloader."""
    
    def __init__(self, config, cookies: Cookies, headless=True):
        """
        Initializes the scraper with user configuration and boots the Playwright context.
        """

        self.config = config

        # Fix URL construction - use the full library URL provided in config
        self.base_url = config["library"]
        if not self.base_url.startswith("https://"):
            self.base_url = "https://" + self.base_url

        self.playwright = sync_playwright().start()

        browser_args = ["--no-sandbox", "--disable-dev-shm-usage", "--mute-audio"]
        self.browser = self.playwright.chromium.launch(
            headless=headless,
            args=browser_args
        )

        self.context = self.browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080}
        )

        if cookies:
            print("Injecting cookies...")
            cookies.insert_into_context(self.context)

        self.page = self.context.new_page()
        self.captured_jpg_urls = []
        self.mp3_urls = {}

        def on_response(response):
            try:
                url = response.url
                url_lower = url.lower()
                
                # Capture jpg images
                if '.jpg' in url_lower:
                    self.captured_jpg_urls.append(url)
                    
                # Capture mp3 parts
                elif ".mp3" in url_lower:
                    req = response.request
                    original_url = req.url
                    while req.redirected_from is not None:
                        req = req.redirected_from
                        original_url = req.url
                        
                    part_num = None
                    # Case-sensitive "Part" split first
                    if "Part" in original_url:
                        try:
                            part_id = original_url.split("Part")[1].split(".mp3")[0]
                            part_num = int(part_id)
                        except Exception:
                            pass
                    
                    # Fallback case-insensitive matches
                    if part_num is None:
                        try:
                            p_idx = url_lower.find("part")
                            if p_idx != -1:
                                sub = url_lower[p_idx + 4:]
                                m_idx = sub.find(".mp3")
                                if m_idx != -1:
                                    import re
                                    digits = re.findall(r'\d+', sub[:m_idx])
                                    if digits:
                                        part_num = int(digits[0])
                        except Exception:
                            pass

                    if part_num is not None:
                        if part_num not in self.mp3_urls:
                            print(f"Snared live MP3 Part {part_num} URL: (Status: {response.status})")
                            self.mp3_urls[part_num] = original_url
            except Exception:
                pass

        self.page.on("response", on_response)

    def close(self):
        """Clean teardown to free system resources."""
        if hasattr(self, 'context'):
            try:
                self.context.close()
            except Exception:
                pass
        if hasattr(self, 'browser'):
            try:
                self.browser.close()
            except Exception:
                pass
        if hasattr(self, 'playwright'):
            try:
                self.playwright.stop()
            except Exception:
                pass

    def __del__(self):
        self.close()

    def get_cookies(self) -> Cookies:
        """
        Returns the current browser session cookies.
        """
        return Cookies(self.context.cookies())

    def download_mp3_part(self, url: str, part_num: int, download_path: pathlib.Path) -> int:
        """
        Downloads an MP3 part from the given URL and saves it.
        """
        fn = f"part{part_num:02d}.mp3"
        download_path.mkdir(parents=True, exist_ok=True)
        full_path = os.path.join(download_path, fn)

        print(f"Requesting part {part_num}: ", end="", flush=True)
        
        max_retries = 4
        delay = 2
        response = None
        for attempt in range(1, max_retries + 1):
            try:
                response = self.context.request.get(url)
                if response.ok:
                    break
                else:
                    if attempt == 1:
                        print(f"failed status {response.status}")
                    print(f"  [Retry {attempt}/{max_retries}] requesting part {part_num}: ", end="", flush=True)
            except Exception as e:
                if attempt == 1:
                    print(f"connection error: {e}")
                print(f"  [Retry {attempt}/{max_retries}] requesting part {part_num}: ", end="", flush=True)
            
            if attempt < max_retries:
                time.sleep(delay)
                delay *= 2

        if response and response.ok:
            body = response.body()
            if len(body) == 0:
                print(f"download found 0 bytes for part {part_num}.")
                return 0
            dur = convert_metadata.get_mp3_duration(io.BytesIO(body))
            if not dur:
                print(f"download found no duration for part {part_num}.")
                return 0
            with atomic_write(full_path, mode="wb", overwrite=True) as f:
                f.write(body)
            print(f"downloaded part {part_num} ({to_hms(dur)}s)")
            return dur
        else:
            final_status = response.status if response else "No Response / Exception"
            print(f"Failed to request mp3 part with status code {final_status}")
            return 0

    def _login(self) -> Cookies:
        """
        Executes sublibrary login flow.
        """
        print("Navigating to sign-in page...")
        self.page.goto(f"{self.base_url}/account/ozone/sign-in")

        banner = self.page.locator('.cookie-banner-close-button').first
        try:
            if banner.is_visible():
                banner.click()
        except Exception:
            pass

        sublibrary_input = self.page.locator('#signin-options')
        if sublibrary_input.count() == 1 and sublibrary_input.is_visible():
            interactive = not self.config.get('sublibrary')
            sublibrary_input.click()
            autocomplete_list = self.page.locator('.ui-autocomplete')

            sub_elements = autocomplete_list.locator('li')
            sub_elements.first.wait_for(state="visible", timeout=5000)

            sub_texts = sub_elements.all_text_contents()
            idx = None if interactive else find_biggest_match_index(self.config['sublibrary'], sub_texts)

            for index, text in enumerate(sub_texts):
                selector = " "
                if idx is None:
                    selector = f'{index:>2}'
                elif index == idx:
                    selector = '->'
                print(f" {selector:>2}: {text}")

            if interactive:
                sub_index = int(input("Select sublibrary: "))
                sub_elements.nth(sub_index).click()
            else:
                if idx is None:
                    print(f"ERROR: Sublibrary {self.config['sublibrary']} not found in library {self.config['library']}")
                    sys.exit(2)
                else:
                    sub_elements.nth(idx).click()
                    self.page.wait_for_timeout(250)

        username_input = self.page.locator('#username')
        password_input = self.page.locator('#password')
        signin_button = self.page.locator('.signin-button')

        username_input.fill(self.config['user'])
        password_input.fill(self.config['pass'])

        with self.page.expect_navigation(timeout=15000):
            signin_button.click()

        return self.get_cookies()

    def ensure_login(self) -> Cookies:
        """
        Ensures the user is logged in.
        """
        self.page.goto(f"{self.base_url}/account/loans")
        try:
            loans_title_locator = self.page.locator('.account-title').first
            loans_title_locator.wait_for(state="visible", timeout=5000)
            content = loans_title_locator.text_content()
            if content and "Loans" in content:
                print("Session is active and valid.")
                return self.get_cookies()
        except Exception:
            print("Session expired. Performing login...")

        return self._login()

    def get_loans(self) -> list[dict[str, Any]]:
        """
            Retrieves all current audiobook loans for the user.
        """
        print("Finding books...")
        if not self.page:
            raise Exception("Browser page not initialized")

        self.page.goto(f"{self.base_url}/account/loans")

        # Ensure the page actually loaded by checking that an h1 of the class account-title is present
        account_title_locator = self.page.locator('h1.account-title').first
        try:
            account_title_locator.wait_for(state="visible", timeout=15000)
        except Exception as e:
            print(f"Failed to load loans page (h1.account-title not visible): {e}")
            sys.exit(4)

        # It's completely fine if there are no loans; in that case, the loan blocks won't be present.
        loan_blocks_locator = self.page.locator('.Loans-TitleContainerRight')
        try:
            loan_blocks_locator.first.wait_for(state="visible", timeout=2000)
        except Exception:
            pass

        books = []
        loan_blocks = loan_blocks_locator.all()

        for index, block in enumerate(loan_blocks):
            title_element = block.locator('.title-name')
            author_element = block.locator('.title-author')
            listen_link_locator = block.get_by_role("link", name="Listen", exact=False).first

            listen_link = None
            book_id = None

            # Short fallback wait per card if not immediately visible (supports delayed rendering)
            if not listen_link_locator.is_visible():
                try:
                    listen_link_locator.wait_for(state="visible", timeout=500)
                except Exception:
                    pass

            if listen_link_locator.is_visible():
                listen_link = listen_link_locator.get_attribute('href')
                if listen_link:
                    book_id = listen_link.split('/')[-1]
            else:
                print(f"Book card at index {index} has no listen link or isn't visible")

            if not book_id:
                title_text = title_element.text_content() or "Unknown Title"
                print(f"Book card at index {index} has no listen link: {title_text.strip()}")
                continue

            books.append({
                "index": index,
                "title": (title_element.text_content() or "").strip(),
                "author": (author_element.text_content() or "").strip(),
                "link": listen_link,
                "id": book_id
            })

        return books

    def get_part_basename(self, part: int) -> str:
        return f"part{part:02d}"

    def get_book(self, bookinfo: dict, download_path: pathlib.Path, config: dict) -> list[Tuple[str, str, str]]:
        """
            Downloads selected audiobook.
        """
        overdrive_download.download_thunder_metadata(self.context, bookinfo["id"], download_path / 'info.json')
        selected_title_link = bookinfo["link"]
        if not selected_title_link.startswith("http"):
            selected_title_link = f"{self.base_url}{selected_title_link}"

        self.page.goto(selected_title_link)
        playback_toggle = self.page.locator('.playback-toggle').first
        playback_toggle.wait_for(state="visible", timeout=15000)

        timeline_length_locator = self.page.locator('.timeline-end-minutes .place-phrase-visual').first
        timeline_length_locator.wait_for(state="visible", timeout=5050)
        contents = timeline_length_locator.text_content()
        if not contents:
            raise Exception("Failed to fetch timeline length")
        expected_time = contents.replace("-", "").strip()

        print(f"Final book should be ~{expected_time} in length.")

        mp3_searcher = Mp3Searcher(self, expected_time, download_path)
        if config.get('get-metadata'):
            fn = download_path / "chs.json"
            if not fn.is_file():
                download_path.mkdir(parents=True, exist_ok=True)
                with open(fn, 'w', encoding='utf-8') as f:
                    json.dump(mp3_searcher.chapter_markers, f, indent=2)
            return mp3_searcher.chapter_markers

        print("Getting files")
        part_num = 1
        # Skip parts that have already been contiguous-resolved during startup scan
        while part_num in mp3_searcher.part_to_seconds and mp3_searcher.part_to_seconds[part_num] > 0:
            part_num += 1

        while True:
            # Terminate if we have downloaded all expected parts according to metadata
            if mp3_searcher.max_parts is not None:
                if part_num > mp3_searcher.max_parts:
                    print(f"All {mp3_searcher.max_parts} expected parts are fully downloaded. Finishing download process.")
                    break
            else:
                if mp3_searcher.downloaded_duration >= mp3_searcher.expected_duration - 30:
                    break

            has_part_duration = part_num in mp3_searcher.part_durations

            if has_part_duration:
                # Part is already downloaded on disk (e.g., from pre-determination or prior run)
                # Register its cumulative timeline offset since it's now contiguous!
                dur = mp3_searcher.part_durations[part_num]
                new_duration = mp3_searcher.downloaded_duration + dur
                mp3_searcher.register_downloaded_part(part_num, new_duration)
                progress = (new_duration / mp3_searcher.expected_duration) * 100.0
                print(f"Found part {part_num} ending at {to_hms(new_duration)} / {to_hms(mp3_searcher.expected_duration)} - {progress:.2f}%")
                part_num += 1
                continue

            # This part MIGHT be seen by the scan, but not downloaded; should be rare, but let's do it.
            url = mp3_searcher.get_url(part_num)
            if url:
                length = self.download_mp3_part(url, part_num, download_path)
                if length:
                    mp3_searcher.part_durations[part_num] = length
                    # We don't move to the next part, let the loop start handle it.
                    continue
                else:
                    raise Exception(f"Download failed for part {part_num}")

            # Otherwise we haven't visited this part yet, so seek to it!
            lower_bound, upper_bound = mp3_searcher.set_bounds(part_num)
            print(f"Missing part {part_num} between ({to_hms(lower_bound)}, {to_hms(upper_bound)})")
            old_upper_bound = None
            collapse_detected = False
            soft_lower_bound = 15

            while not mp3_searcher.has_url(part_num):
                mp3_searcher.get_current_location()
                if mp3_searcher.has_new_bounds():
                    lower_bound, upper_bound = mp3_searcher.set_bounds(part_num)
                    print(f"Missing part {part_num} between ({to_hms(lower_bound)}, {to_hms(upper_bound)})")

                if upper_bound == old_upper_bound:
                    if not mp3_searcher.move_to(lower_bound) or mp3_searcher.get_current_location() > lower_bound + 5:
                        raise Exception("Couldn't seek timeline near lower bound.")
                    if mp3_searcher.has_new_bounds():
                        continue
                    old_loc = mp3_searcher.current_location
                    span = max(upper_bound - old_loc, 6)
                    try:
                        playback_toggle.click()
                        while old_loc == mp3_searcher.get_current_location():
                            time.sleep(1)
                        elapsed = 0
                        while not mp3_searcher.has_url(part_num) and elapsed < span:
                            time.sleep(2)
                            elapsed += 2
                            mp3_searcher.get_current_location()
                    finally:
                        playback_toggle.click()
                    if mp3_searcher.has_url(part_num):
                        continue
                    raise Exception(f"Need more precise search between {to_hms(lower_bound)} and {to_hms(upper_bound)}")

                old_upper_bound = upper_bound

                if not collapse_detected and lower_bound == upper_bound:
                    upper_bound = mp3_searcher.expected_duration
                    collapse_detected = True

                middle = (lower_bound + upper_bound) // 2
                if lower_bound + soft_lower_bound + 1 < middle:
                    mp3_searcher.move_to(lower_bound + soft_lower_bound + 1)
                    soft_lower_bound *= 2

                if mp3_searcher.has_new_bounds():
                    continue

                div_point = upper_bound
                halvings = 0
                while not mp3_searcher.has_new_bounds() and div_point > lower_bound + soft_lower_bound + 15:
                    div_point = (lower_bound + soft_lower_bound + div_point) // 2
                    halvings += 1
                    mp3_searcher.move_to(div_point)

                if mp3_searcher.has_new_bounds():
                    continue

                if not mp3_searcher.move_to(mp3_searcher.lower_bound) and not mp3_searcher.has_new_bounds():
                    print("Pivot move failed to yield url.")

        print("Downloaded complete audio")

        # Attempt to find and save cover image the old way (by scanning all response URLs)
        cover_image_url = next(
            (url for url in self.captured_jpg_urls if 'listen.overdrive.com' in url.lower()),
            next(
                (url for url in self.captured_jpg_urls if 'od-cdn.com' in url.lower()),
                next(
                    (url for url in self.captured_jpg_urls),
                    None
                )
            )
        )
        cover_path = os.path.abspath(download_path / "cover.jpg")

        if cover_image_url:
            print(f"Captured cover image URL from network traffic: {cover_image_url}")
            overdrive_download.download_cover(self.context, cover_image_url, cover_path, config.get("abort_on_warning", False))
        else:
            print("Warning: No candidate cover image URL (.jpg) was captured in network traffic.")

        return mp3_searcher.chapter_markers

@dataclass
class Mp3Searcher:
    scraper: Scraper
    page: Any = field(init=False)
    expected_length: InitVar[str]
    download_path: pathlib.Path
    downloaded_duration: int = field(default=0)
    expected_duration: int = field(init=False)
    marked_new_bounds: bool = field(default=False)
    part_num: int = field(init=False)
    lower_part: int = field(init=False)
    upper_part: int = field(init=False)
    lower_bound: int = field(init=False)
    upper_bound: int = field(init=False)
    
    timeline_current_time: Any = field(init=False)
    chapter_table_open: Any = field(init=False)
    chapter_previous: Any = field(init=False)
    chapter_next: Any = field(init=False)
    
    mp3_urls: dict[int, str] = field(default_factory=dict)
    seen_parts: set[int] = field(default_factory=set)
    chapter_seconds: list[int] = field(default_factory=list)
    part_to_seconds: dict[int, int] = field(default_factory=dict)
    seconds_to_part: dict[int, int] = field(default_factory=dict)
    chapter_markers: list[tuple[str, str, str]] = field(default_factory=list)
    part_durations: dict[int, int] = field(default_factory=dict)

    def __post_init__(self, expected_length: str):
        self.mp3_urls = self.scraper.mp3_urls
        self.page = self.scraper.page
        self.expected_duration = convert_metadata.to_seconds(expected_length)
        self.part_to_seconds[1] = 0
        self.seconds_to_part[0] = 1
        self.part_num = 1
        self.seen_parts.add(1)
        self.upper_bound = self.lower_bound = self.lower_part = self.upper_part = -1

        self.max_parts: Optional[int] = None
        info_json_path = self.download_path / "info.json"
        if info_json_path.exists():
            try:
                with open(info_json_path, 'r', encoding='utf-8') as f:
                    meta = json.load(f)
                if "formats" in meta and isinstance(meta["formats"], list):
                    for fmt in meta["formats"]:
                        if isinstance(fmt, dict) and "partCount" in fmt:
                            self.max_parts = int(fmt["partCount"])
                            print(f"Loaded partCount from info.json: {self.max_parts} parts expected.")
                            break
            except Exception as e:
                print(f"Warning: Could not parse partCount from info.json: {e}")

        self.timeline_current_time = self.page.locator('.timeline-start-minutes .place-phrase-visual').first
        self.chapter_table_open = self.page.locator('.chapter-bar-title-button').first
        self.chapter_previous = self.page.locator('.chapter-bar-prev-button').first
        self.chapter_next = self.page.locator('.chapter-bar-next-button').first

        # Scan for already existing parts first to populate our state correctly
        self.scan_existing_parts()

        self.__build_chapters(expected_length)
        self.get_current_location()

    def scan_existing_parts(self):
        self.part_durations = {}
        # Scan for existing parts on disk
        for pn in range(1, 250):
            fn = f"part{pn:02d}.mp3"
            full_p = self.download_path / fn
            if os.path.isfile(full_p):
                duration = convert_metadata.get_mp3_duration(full_p)
                if duration > 0:
                    self.part_durations[pn] = duration
                    print(f"Scanned existing Part {pn}: duration={to_hms(duration)}s")
                else:
                    print(f"Found corrupt existing Part {pn} (0 duration), removing file from disk.")
                    try:
                        os.remove(full_p)
                    except Exception as clean_err:
                        print(f"Could not remove corrupt Part {pn} file: {clean_err}")
        
        # Now, register contiguous downloaded parts starting from Part 1
        cumulative = 0
        pn = 1
        while pn in self.part_durations:
            cumulative += self.part_durations[pn]
            self.register_downloaded_part(pn, cumulative)
            pn += 1
        
        # In case we have something contiguous registered, update downloaded_duration
        if pn > 1:
            print(f"Starting contiguous resume duration: {to_hms(cumulative)} (up to part {pn-1})")

    def find_bounds_at(self, location: int) -> tuple[tuple[int, int], tuple[int, int]]:
        lower_part = max([p for p, s in self.part_to_seconds.items() if s <= location], default=0)
        return self.find_bounds(lower_part)

    def find_bounds(self, part_num: int) -> tuple[tuple[int, int], tuple[int, int]]:
        lower_bound, lower_part = max([(s, p) for s, p in self.seconds_to_part.items()
                                          if p <= part_num and s >= self.downloaded_duration
                                       ],
                                       default=(self.downloaded_duration, 0))
        upper_bound, upper_part = min([(s, p) for s, p in self.seconds_to_part.items()
                                        if s > lower_bound and p > part_num
                                       ],
                                       default=(self.expected_duration, max(self.part_to_seconds.keys(), default=1)))
        return (lower_bound, upper_bound), (lower_part, upper_part)

    def set_bounds(self, part_num: int) -> tuple[int, int]:
        old_state = (self.part_num, (self.lower_bound, self.upper_bound), (self.lower_part, self.upper_part))
        new_state = part_num, self.find_bounds(part_num)
        if old_state == new_state:
            raise Exception(f"ERROR: Double state bounds mismatch: {old_state}")
        self.part_num, ((self.lower_bound, self.upper_bound), (self.lower_part, self.upper_part)) = new_state
        self.marked_new_bounds = False
        return self.lower_bound, self.upper_bound

    def register_downloaded_part(self, part_num: int, downloaded_duration: int):
        self.downloaded_duration = downloaded_duration
        self.part_to_seconds[part_num] = downloaded_duration
        self.seconds_to_part[downloaded_duration] = part_num
        self.lower_bound = downloaded_duration

    def __update_seen_parts(self):
        urls_keys = set(self.mp3_urls.keys())
        parts = urls_keys - self.seen_parts
        if parts:
            part = min(parts)
            self.__add_part_sighting(part, self.current_location)
            if self.current_location > self.lower_bound:
                print(f"(Seen part {part} at {to_hms(self.current_location)} in chapter {self.chapter_containing(self.current_location)})")
            self.seen_parts.add(part)

        if parts or self.current_location in self.seconds_to_part:
            return

        second_range, part_range = self.find_bounds_at(self.current_location)
        if not second_range[0] <= self.downloaded_duration:
            return

        if part_range[1] - part_range[0] == 1 and part_range[0] < self.part_num < part_range[1]:
            self.__add_part_sighting(part_range[1], self.current_location)

    def __add_part_sighting(self, part_num: int, location: int):
        self.seconds_to_part.setdefault(location, part_num)
        self.part_to_seconds.setdefault(part_num, location)

    def __build_chapters(self, expected_length: str):
        print("Parsing chapters index table...")
        self.chapter_table_open.click()
        self.page.wait_for_timeout(1000)

        chapter_dialog_table = self.page.locator('.chapter-dialog-table').first
        chapter_dialog_table.wait_for(state="visible", timeout=5000)

        chapter_title_elements = chapter_dialog_table.locator('.chapter-dialog-row-button').all()
        chapter_time_elements = chapter_dialog_table.locator('.place-phrase-visual').all()

        if not chapter_title_elements or not chapter_time_elements:
            raise Exception("No chapter elements found")

        chapter_times = []
        for ch, elem in enumerate(chapter_time_elements):
            text = elem.text_content()
            if text:
                chapter_times.append(text)
                self.chapter_seconds.append(convert_metadata.to_seconds(text))
            else:
                raise Exception(f"Blank space at chapter index {ch}")

        for index, elem in enumerate(chapter_title_elements):
            title = elem.text_content() or f"Chapter {index+1}"
            end = chapter_times[index+1] if index+1 < len(chapter_times) else expected_length
            self.chapter_markers.append((title.strip(), chapter_times[index], end))

        self.page.keyboard.press("Escape")
        self.page.wait_for_timeout(1000)

        self.chapter_seconds.append(self.expected_duration)

        loc = self.get_current_location()
        self.move_to_chapter(0)
        self.move_to_chapter(len(self.chapter_seconds) - 1)
        
        # Give a small buffer time for the net response of the last part
        self.page.wait_for_timeout(1000)
        
        self.move_to_chapter(self.chapter_containing(loc))

        if not self.get_url(1):
            self.page.wait_for_timeout(1500)

        # Fallback/validation: retrieve max_parts from seeking to the end if not already validated
        seen_cand = set(self.seen_parts)
        seen_cand.update(self.mp3_urls.keys())
        if seen_cand:
            highest_seen = max(seen_cand)
            if self.max_parts is None:
                self.max_parts = highest_seen
                print(f"Dynamically determined max_parts = {self.max_parts} from the highest part number seen after seeking to the end.")
            elif self.max_parts != highest_seen:
                # If they are different, let's prefer the highest seen (as the user says, "That's always been reliable")
                print(f"Loaded {self.max_parts} from info.json, but seek dance saw up to part {highest_seen}. Updating max_parts to {highest_seen}.")
                self.max_parts = highest_seen

    def get_url(self, part: int) -> Optional[str]:
        return self.mp3_urls.get(part)

    def has_url(self, part_num) -> bool:
        return part_num in self.mp3_urls

    def has_new_bounds(self) -> bool:
        if self.marked_new_bounds:
            return True
        self.marked_new_bounds = True
        
        if self.has_url(self.part_num):
            return True
        bounds, parts = self.find_bounds(self.part_num)
        if bounds != (self.lower_bound, self.upper_bound) or parts != (self.lower_part, self.upper_part):
            return True
            
        self.marked_new_bounds = False
        return False

    def chapter_boundary_closest(self, target: int) -> tuple[Optional[int], Optional[int]]:
        curr_dist = abs(target - self.current_location)
        close_chaps = [(s, ch) for ch, s in enumerate(self.chapter_seconds) if abs(s - target) < curr_dist]
        if not close_chaps:
            return None, None
        return min(close_chaps, key=lambda s: abs(s[0] - target))

    def move_by_chapters(self, target: int) -> bool:
        start = self.current_location
        boundary, ch = self.chapter_boundary_closest(target)
        if boundary is None or ch is None:
            return False
        self.move_to_chapter(ch)
        return start != self.current_location or self.has_new_bounds()

    def move_by_nudges(self, target: int, acceptable_bounds: tuple[int, int]) -> bool:
        return self.__move_primitive(target, left=Keys.ARROW_LEFT, right=Keys.ARROW_RIGHT, bounds=acceptable_bounds)

    def move_by_minutes(self, target: int, acceptable_bounds: tuple[int, int]) -> bool:
        return self.__move_primitive(target, left=Keys.PAGE_UP, right=Keys.PAGE_DOWN, bounds=acceptable_bounds)

    def __move_primitive(self, target: int, left: str, right: str, bounds: tuple[int, int]) -> bool:
        bottom, top = bounds
        def success(): return target + bottom < self.current_location <= target + top or self.has_new_bounds()
        
        if success():
            return True

        key = left if self.current_location > target else right
        while not success():
            old = self.current_location
            self.page.keyboard.press(key)
            self.page.wait_for_timeout(1000)
            self.get_current_location()
            if not success() and self.current_location == old:
                return False
        return self.current_location <= target < self.current_location + 15

    def wait_for_location_near(self, target_seconds: int, timeout_ms: int = 8000) -> int:
        start_time = time.time()
        curr = self.current_location
        while time.time() - start_time < timeout_ms / 1000.0:
            s_text = self.timeline_current_time.text_content()
            if s_text:
                curr = convert_metadata.to_seconds(s_text)
                if abs(curr - target_seconds) <= 15:
                    self.current_location = curr
                    self.__update_seen_parts()
                    return curr
            self.page.wait_for_timeout(200)
        return self.get_current_location()

    def move_forward_chapter(self, target_seconds_opt: Optional[int] = None) -> bool:
        enabled = False
        try:
            enabled = self.chapter_next.is_enabled()
            if enabled:
                self.chapter_next.click()
                if target_seconds_opt is not None:
                    self.wait_for_location_near(target_seconds_opt, timeout_ms=8000)
                else:
                    self.page.wait_for_timeout(500)
        except Exception:
            pass
        self.get_current_location()
        return enabled

    def get_current_location(self) -> int:
        s = self.timeline_current_time.text_content()
        if s is None:
            raise Exception("Timeline counter read error")
        self.current_location = convert_metadata.to_seconds(s)
        self.__update_seen_parts()
        return self.current_location

    def move_to(self, goal: int) -> bool:
        start = self.get_current_location()
        if self.has_new_bounds():
            return True
        if goal - 15 < start <= goal:
            return True
            
        print(f"Advancing head: {to_hms(start)} -> {to_hms(goal)}")
        if self.move_by_chapters(goal):
            ch = self.chapter_containing(self.current_location)
            print(f"Jumping to Chapter {ch}...")
            
        if not goal - 60 < self.current_location <= goal and not self.has_new_bounds():
            self.move_by_minutes(goal, acceptable_bounds=(-60, 0))
        if not goal - 15 < self.current_location <= goal and not self.has_new_bounds():
            self.move_by_nudges(goal, acceptable_bounds=(-15, 0))
            
        return True

    def move_to_chapter(self, desired_chapter: int) -> int:
        wants_end = (desired_chapter == len(self.chapter_seconds) - 1)
        if wants_end:
            desired_chapter -= 1

        self.chapter_table_open.click()
        self.page.wait_for_timeout(1000)
        
        try:
            chapter_dialog_table = self.page.locator('.chapter-dialog-table').first
            chapter_title_elements = chapter_dialog_table.locator('.chapter-dialog-row-button').all()
            if desired_chapter < len(chapter_title_elements):
                chapter_title_elements[desired_chapter].click()
                self.wait_for_location_near(self.chapter_seconds[desired_chapter], timeout_ms=8000)
            else:
                raise ValueError(f"Chapter out of range: {desired_chapter}")
        finally:
            self.page.keyboard.press("Escape")
            self.page.wait_for_timeout(1000)

        if wants_end:
            self.get_current_location()
            self.move_forward_chapter(target_seconds_opt=self.chapter_seconds[-1])
            self.page.wait_for_timeout(1000)

        return self.get_current_location()

    def chapter_containing(self, s: int) -> int:
        candidate = None
        for i, start in enumerate(self.chapter_seconds[:-1]):
            if s >= start and s < self.chapter_seconds[i+1]:
                candidate = i
        return candidate if candidate is not None else len(self.chapter_seconds) - 2

