from dataclasses import dataclass, field, InitVar
from typing import Tuple
from selenium.webdriver.remote.webelement import WebElement
import seleniumwire, seleniumwire.webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.wait import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.common.exceptions import NoSuchElementException
from selenium.common.exceptions import ElementClickInterceptedException
from selenium.webdriver.common.keys import Keys
from webdriver_manager.chrome import ChromeDriverManager
import overdrive_download
from convert_metadata import to_hms
import convert_metadata
import os
import time
import sys

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

class Scraper:
    """Automated Overdrive audiobook downloader using Selenium."""
    def __init__(self, config, headless=True):
        """
        Initializes the scraper with user configuration and sets up the Chrome driver.

        Args:
            config (dict): Dictionary with keys 'library', 'user', 'pass', etc.
            headless (bool): Whether to run the browser in headless mode.
        """
        self.config = config

        # Fix URL construction - use the full library URL provided in config
        self.base_url = config["library"]
        if not self.base_url.startswith("https://"):
            self.base_url = "https://" + self.base_url

        self.chrome_options = Options()
        if headless:
            self.chrome_options.add_argument("--headless=new")
        self.chrome_options.add_argument("--log-level=2")
        self.chrome_options.add_argument("--no-sandbox")
        self.chrome_options.add_argument("--disable-dev-shm-usage")
        self.chrome_options.add_argument("--disable-gpu")
        self.chrome_options.add_argument("--disable-extensions")
        self.chrome_options.add_argument("--window-size=1920,1080")
        self.chrome_options.add_argument("--start-maximized")
        self.chrome_options.add_argument("--user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
        self.chrome_options.add_argument("--mute-audio")

        self.driver = None

    def __del__(self):
        if self.driver:
            self.driver.quit()

    def get_cookies(self):
        """Returns the current browser session cookies."""
        return self.driver.get_cookies().copy()

    def _login(self) -> list[dict]:
        """Handles login logic and returns a fresh list of cookies."""
        print("Logging in...")
        if not self.driver:
            raise Exception("Driver not initialized")

        self.driver.get(self.base_url + "/account/ozone/sign-in")

        # Dismiss cookie banner if present
        banners = self.driver.find_elements(By.CLASS_NAME, 'cookie-banner-close-button')
        if banners: banners[0].click()

        # Enter credentials
        sublibrary_inputs = self.driver.find_elements(By.ID, 'signin-options')
        sublibrary_input = sublibrary_inputs[0] if len(sublibrary_inputs) == 1 else None

        wait = WebDriverWait(self.driver, timeout=15)
        self.driver.implicitly_wait(1)

        # Logins with sublibrary sometimes require choosing it first.
        if sublibrary_input and sublibrary_input.is_displayed() and sublibrary_input.is_enabled():
            interactive = not self.config.get('sublibrary')
            sublibrary_input.click()
            sublibrary_output = self.driver.find_element(By.CLASS_NAME, 'ui-autocomplete')
            time.sleep(0.25)
            subs = sublibrary_output.find_elements(By.TAG_NAME, 'li')
            # Display numbered list of sublibraries and prompt for selection
            idx = None if interactive else find_biggest_match_index(self.config['sublibrary'], [sub.text for sub in subs])
            for index,sub in enumerate(subs):
                selector = ""
                if idx is None:
                    selector = f'{index:>2}'
                elif index == idx:
                    selector = '->'
                print(f" {selector:>2}: {sub.text}")
            if interactive:
                sub_index = int(input("Select sublibrary: "))
                subs[sub_index].click()
            else:
                if idx is None:
                    print(f"ERROR: Sublibrary {self.config['sublibrary']} not found in library {self.config['library']}")
                    sys.exit(2)
                else:
                    subs[idx].click()
                    time.sleep(0.25)

        signin_button = self.driver.find_element(By.CLASS_NAME, 'signin-button')

        wait.until(lambda _ : signin_button.is_enabled())

        username_input = self.driver.find_element(By.ID, 'username')
        password_input = self.driver.find_element(By.ID, 'password')
        username_input.send_keys(self.config['user'])
        password_input.send_keys(self.config['pass'])

        signin_button.click()

        time.sleep(1)

        if 'sign-in' in self.driver.current_url.lower():
            return []

        return self.get_cookies()

    def ensure_login(self, cookies: list[dict]) -> list[dict]: # Can pass in []
        """
        Ensures the user is logged in; attempts to use existing cookies.

        Args:
            cookies (list[dict]): Optional pre-existing cookies.

        Returns:
            list[dict]: Valid session cookies.
        """
        if not self.driver:
            service = Service(ChromeDriverManager().install())
            self.driver = seleniumwire.webdriver.Chrome(service=service, options=self.chrome_options)
            self.driver.get(self.base_url)
            try:
                for cookie in cookies:
                    # Check for site cookie domain, case insensitive (we keep all cookies in same file)
                    if self.config['library'].lower() in cookie['domain'].lower():
                        self.driver.add_cookie(cookie)
            except:
                print("Invalid cookies")
                return self._login()

        # Go to authenticated page to test if cookies are still valid
        self.driver.get(self.base_url + "/account/loans")

        loans_titles = self.driver.find_elements(By.CLASS_NAME, 'account-title')
        if loans_titles:
            if "Loans" in loans_titles[0].text:
                # Already have valid session
                return cookies
        else:
            return self._login()

    def get_loans(self):
        """
        Retrieves all current audiobook loans for the user.

        Returns:
            list: List of dictionaries with book info: title, author, link, and ID.
        """
        print("Finding books...")
        if not self.driver:
            raise Exception("Driver not initialized")

        self.driver.get(self.base_url + "/account/loans")

        try:
            WebDriverWait(self.driver, 15).until(
                EC.presence_of_element_located((By.CLASS_NAME, 'Loans-TitleContainerRight'))
            )
        except Exception as e:
            print(f"Failed to load loans: {e}")
            sys.exit(4)

        books = []
        loan_blocks = self.driver.find_elements(By.CLASS_NAME, 'Loans-TitleContainerRight')

        for index,block in enumerate(loan_blocks):
            title_element = block.find_element(By.CLASS_NAME, 'title-name')
            author_element = block.find_element(By.CLASS_NAME, 'title-author')
            listen_links = block.find_elements(By.PARTIAL_LINK_TEXT, 'Listen now')
            listen_link = book_id = None
            # Parse cautiously, other media types can be checked out but we don't get them.
            if listen_links:
                if listen_link := listen_links[0].get_attribute('href'):
                    book_id = listen_link.split('/')[-1]
            if not book_id:
                print(f"Book at index {index} has no listen link, may not be audiobook: {title_element.text.strip()}")
                continue

            books.append({"index": index, "title": title_element.text.strip(), "author": author_element.text.strip(), "link": listen_link, "id": book_id})

        return books

    def extract_minutes_to_seconds(self, raw_text: str):
        """
        Parses a string like '2m' to integer seconds.

        Args:
            raw_text (str): Text to parse.

        Returns:
            int or bool: Parsed seconds, or False on failure.
        """
        if not raw_text:
            return False
        cleaned = raw_text.strip().lower()  # remove whitespace and lowercase
        if cleaned.endswith("m"):
            minutes = int(cleaned[:-1].replace(',', ''))  # remove the 'm' and convert to seconds
            return minutes * 60
        return False

    def get_part_basename(self, part: int) -> str:
        return f"part{part:02d}"

    def get_book(self, selected_title_link: str, download_path: str) -> list[Tuple[int, int]]:
        """
        Downloads the selected audiobook and associated metadata.

        Args:
            selected_title_link (str): The "Listen Now" URL of the book.
            download_path (str): Folder path to save the book to.

        Returns:
            chapter_markers
        """
        if not self.driver:
            raise Exception("Driver is not initialized")

        # Go to book listen page
        self.driver.get(selected_title_link)
        time.sleep(1)

        # Fetch player elements
        toggle_play = self.driver.find_element(By.CLASS_NAME, 'playback-toggle')
        timeline_length = self.driver.find_element(By.CLASS_NAME, 'timeline-end-minutes').find_element(By.CLASS_NAME, 'place-phrase-visual')
        expected_time = timeline_length.get_attribute("textContent").replace("-", "")

        # Allow debugging if one or more fails to fetch. Also turns off "may be None" warnings.
        fetched = [toggle_play, timeline_length]
        if None in fetched:
            raise Exception(f"Failed to fetch one or more player elements: {fetched}")

        print(f"Final book should be ~{expected_time} in length.")

        mp3_searcher = Mp3Searcher(self, expected_time)
        current_location = mp3_searcher.get_current_location()

        # Download part files
        print("Getting files")
        current_location = mp3_searcher.get_current_location()
        loaded_duration = convert_metadata.get_total_duration(download_path)
        part_num = 1

        # Check for resumable part files.
        print("Checking for resumable parts.")
        tmp_dir = download_path
        if loaded_duration:
            parts = [self.get_part_basename(part_num)+".mp3" for part_num in range(1, max(mp3_searcher.part_to_seconds)+1)]

            # Check that the dir contains sequential part files.
            resumable_parts = [part for part in parts if os.path.isfile(os.path.join(tmp_dir, part))]
            if not resumable_parts or resumable_parts != parts[:len(resumable_parts)]:
                print(f"ERROR: tmp folder contains corrupted download (check numbered parts), please remove or clean up: {tmp_dir}")
                print(resumable_parts)
                sys.exit(1)
            resumable_parts = [fullname for part in parts if os.path.isfile(fullname := os.path.join(tmp_dir, part))]

            # Sum the durations of the resumable parts.
            exact_size = 0.0
            for _, fullname in enumerate(resumable_parts, 1):
                this_size = convert_metadata.get_mp3_duration(fullname)
                exact_size += this_size

            # Check that the dir doesn't have any other media files (duration
            # range accounts for inexactness).
            if not loaded_duration - 10 < exact_size < loaded_duration + 10:
                print(f"ERROR: tmp folder contains {to_hms(loaded_duration)} duration media files when expected {to_hms(int(exact_size))}, please remove or clean up: {tmp_dir}")
                sys.exit(1)
            part_num = len(resumable_parts) + 1
            print(f"Resuming download from part {part_num} at {to_hms(loaded_duration)}")

        # Main loop for walking through book
        while loaded_duration < mp3_searcher.expected_duration-1:
            # Collect available urls, and download next part.
            url = mp3_searcher.get_url(part_num)
            if url:
                length = overdrive_download.download_mp3_part(url, part_num, download_path, self.get_cookies())
                # If valid download, add the length of the part to the total, check progress through whole book
                if length:
                    # Use ground truth from metadata rather than adding approximations
                    loaded_duration = convert_metadata.get_total_duration(download_path)
                    print(f"{to_hms(loaded_duration)} / {to_hms(mp3_searcher.expected_duration)} - {loaded_duration}/{mp3_searcher.expected_duration} sec  -  {loaded_duration/mp3_searcher.expected_duration*100.0:.2f}%")
                    part_num += 1
                    continue
                else:
                    raise Exception(f"Download failed for part {part_num}")

            # Begin search for the absent part.
            lower_bound = int(loaded_duration)
            upper_bound = mp3_searcher.get_upper_bound(part_num, lower_bound)

            print(f"Missing part {part_num} between ({to_hms(lower_bound)}, {to_hms(upper_bound)})")
            old_upper_bound = None
            collapse_detected = False
            while not mp3_searcher.has_url(part_num):
                current_location = mp3_searcher.get_current_location()

                # Require progress on each iteration.
                if upper_bound == old_upper_bound:
                    # One last effort.
                    old_loc = current_location
                    span = upper_bound - lower_bound
                    print(f"Using play toggle between {to_hms(lower_bound)}, {to_hms(upper_bound)} ({span}s), start at {current_location}")
                    first_try = True
                    try:
                        toggle_play.click()
                        while first_try or (not mp3_searcher.has_url(part_num) and old_loc+span > current_location):
                            time.sleep(5 if first_try else 1)
                            current_location = mp3_searcher.get_current_location()
                            if not first_try and not current_location > old_loc:
                                print(f"Play toggle might not be responding, location was {to_hms(old_loc)}, now {to_hms(current_location)}")
                            first_try = False
                    finally:
                        if first_try:
                            time.sleep(5)
                        toggle_play.click()
                    if mp3_searcher.has_url(part_num):
                        print(f"Found part by using play toggle between {lower_bound}, {upper_bound}")
                        continue
                    print(f"Need more precise search for {upper_bound - lower_bound}s range between {to_hms(lower_bound)}, {upper_bound}")
                    raise Exception(f"Need more precise search for {upper_bound - lower_bound}s range between {lower_bound}, {upper_bound}")
                old_upper_bound = upper_bound

                # Handle a "collapse" (lower==upper) by trying to search the whole book.
                if not collapse_detected and lower_bound == upper_bound:
                    print(f"Could not find part {part_num}, retrying by searching the rest of the book.")
                    upper_bound = mp3_searcher.expected_duration
                    collapse_detected = True

                # First, see if there's a chapter mark that is closer to our
                # range than the current location.
                chapter_move, current_chapter = mp3_searcher.closest_chapter_mark(lower_bound, upper_bound, current_location)
                if chapter_move:
                    desired_chapter, desired_chapter_start = chapter_move

                    current_location = mp3_searcher.move_to_chapter(desired_chapter)
                    print(f"Clicked to chapter {desired_chapter}/{len(mp3_searcher.chapter_seconds)-1} at {to_hms(current_location)}")

                    if current_location != desired_chapter_start:
                        if mp3_searcher.chapter_seconds[current_chapter] == desired_chapter_start:
                            current_chapter = desired_chapter
                        else:
                            current_chapter = mp3_searcher.chapter_containing(current_location)
                    current_chapter_start = mp3_searcher.chapter_seconds[current_chapter]

                    # Sometimes the player dumps us in the middle of a chapter, so go back if it might help.
                    if current_location > current_chapter_start and current_chapter_start > lower_bound:
                        mp3_searcher.move_backward_chapter()

                    if mp3_searcher.has_url(part_num):
                        continue
                    elif lower_bound <= current_location < upper_bound:
                        if current_location > lower_bound + 10*60:
                            # I'm skeptical ... I should really add some reasoning about what part I'm actually IN.
                            print(f"No URL for {part_num} at {current_location}, but reducing upper bound by {to_hms(upper_bound-current_location)}.")
                            upper_bound = current_location - 1
                            continue
                # Next, try to use the minute-skip key to get into the range.
                body = self.driver.find_element(By.TAG_NAME, "body")
                old_location = current_location
                print(f"Locations: {to_hms(lower_bound)} <= {to_hms(current_location)} < {to_hms(upper_bound)}")
                if current_location < lower_bound:
                    # If we can avoid skipping the whole range, skip forward into the range.
                    if current_location+60 < upper_bound:
                        print(f"Skipping forward by minutes from {to_hms(current_location)} to {to_hms(upper_bound)}, max {(upper_bound-current_location)//60} skips")
                        start = current_location
                        # Try to go a halfway into the range, or a minute in if that is more; but not past the upper bound.
                        while current_location < min(upper_bound, max( (lower_bound+upper_bound)//2, lower_bound+60)) and not mp3_searcher.has_url(part_num):
                            # ffwd into the range if you can, without going past it.
                            body.send_keys(Keys.PAGE_DOWN)
                            time.sleep(.5)
                            current_location = mp3_searcher.get_current_location()
                        print(f"Skipped forward {to_hms(current_location-start)} to {to_hms(current_location)}")
                elif current_location > upper_bound:
                    if current_location-60 > lower_bound:
                        print(f"Skipping backward by minutes from {to_hms(current_location)} to {to_hms(lower_bound)}, max {(current_location-lower_bound)//60} skips")
                        start = current_location
                        while current_location-60 > lower_bound and not mp3_searcher.has_url(part_num):
                            # frewind back to near the start of the range, without going past it.
                            body.send_keys(Keys.PAGE_UP)
                            time.sleep(.5)
                            current_location = mp3_searcher.get_current_location()
                        print(f"Skipped backward {to_hms(start-current_location)} to {to_hms(current_location)}")
                if mp3_searcher.has_url(part_num):
                    # Shortcut if we're done.
                    continue
                if lower_bound < current_location <= upper_bound:
                    if current_location > lower_bound + 10*60:
                        print(f"No URL for {part_num} at {to_hms(current_location)}, reducing upper bound by {to_hms(upper_bound-current_location)}.")
                        upper_bound = current_location - 1
                # Next, try to use the small-skip key to get into the new range.
                old_location = current_location
                while current_location <= lower_bound and current_location <= upper_bound-15 and not mp3_searcher.has_url(part_num):
                    # fwd into the range if you can, without going past it.
                    body.send_keys(Keys.ARROW_RIGHT)
                    time.sleep(.5)
                    current_location = mp3_searcher.get_current_location()
                while current_location-15 > lower_bound and not mp3_searcher.has_url(part_num):
                    # rewind back to near the start of the range, without going past it.
                    body.send_keys(Keys.ARROW_LEFT)
                    time.sleep(.5)
                    current_location = mp3_searcher.get_current_location()
                if mp3_searcher.has_url(part_num):
                    continue
                if old_location != current_location:
                    dir = "forward" if old_location < current_location else "backward"
                    mins = abs(old_location - current_location)
                    print(f"Skipped {dir} {mins}s")
                if not mp3_searcher.has_url(part_num) and lower_bound+5*60 < current_location <= upper_bound:
                    print(f"No URL for {part_num} at {upper_bound}, reducing to {current_location-1}.")
                    upper_bound = current_location - 1

        if loaded_duration >= mp3_searcher.expected_duration-1:
            print("Downloaded complete audio")
            print(f"Book contained {part_num-1} part(s)")

        # Attempt to find and save cover image
        cover_image_url = next(
            (req.url for req in self.driver.requests if req.response and '.jpg' in req.url and 'listen.overdrive.com' in req.url),
            None
        )

        cover_path = os.path.abspath(os.path.join(download_path, "cover.jpg"))
        if cover_image_url and overdrive_download.download_cover(cover_image_url, cover_path, self.get_cookies(), self.config.get("abort_on_warning", False)):
            print("Downloaded cover")

        return mp3_searcher.chapter_markers

@dataclass
class Mp3Searcher(object):
    scraper: Scraper
    driver: seleniumwire.webdriver.Chrome = field(init=False)
    expected_length: InitVar[str]
    expected_duration: int = field(init=False)
    timeline_current_time: WebElement = field(init=False)
    chapter_table_open: WebElement = field(init=False)
    chapter_previous: WebElement = field(init=False)
    chapter_next: WebElement = field(init=False)
    mp3_urls: dict[int, str] = field(default_factory=dict)
    seen_parts: set[int] = field(default_factory=set)
    chapter_seconds: list[int] = field(default_factory=list)
    part_to_seconds: dict[int, int] = field(default_factory=dict)

    def __post_init__(self, expected_length: str):
        assert(self.scraper.driver)
        self.driver = self.scraper.driver

        self.expected_duration = convert_metadata.to_seconds(expected_length)

        self.timeline_current_time = self.driver.find_element(By.CLASS_NAME, 'timeline-start-minutes').find_element(By.CLASS_NAME, 'place-phrase-visual')
        self.chapter_table_open = self.driver.find_element(By.CLASS_NAME, 'chapter-bar-title-button')
        self.chapter_previous = self.driver.find_element(By.CLASS_NAME, 'chapter-bar-prev-button')
        self.chapter_next = self.driver.find_element(By.CLASS_NAME, 'chapter-bar-next-button')

        self.__build_chapters(expected_length)
        self.get_current_location()

    def get_upper_bound(self, part_num: int, lower_bound: int) -> int:
        # Get the time of the smallest known chapter remaining, or the total
        # duration, whichever is smaller. Total duration is included to make
        # this work even if no known chapters are left.
        upper_bound = min(tuple(self.part_to_seconds[p]-1 for p in self.part_to_seconds.keys() if p > part_num and self.part_to_seconds[p] > lower_bound)
                            + (self.expected_duration,))
        # Limit upper bound to 3 hours - parts are normally 30-60 minutes long.
        three_hours = lower_bound + 3*60*60
        return min(three_hours, upper_bound)

    def __update_seen_parts(self):
        ch = self.chapter_containing(self.current_location)
        self.__requests_to_mp3_files()
        parts = set(self.mp3_urls.keys()) - self.seen_parts
        if parts:
            part = min(parts)
            if len(parts) > 1:
                # This would imply we somehow loaded part URLs but didn't call
                # this function soon enough to know where they were.
                print(f"WARNING: Skipped {len(parts)-1} part(s) ({sorted(parts)} except {part}) while looking at chapter {ch}")
            self.part_to_seconds[part] = self.current_location
        self.seen_parts.update(parts)

    def __requests_to_mp3_files(self):
        """
        Extracts MP3 file URLs from the browser's request history.

        Returns:
            dict: Part number to URL mapping.
        """
        urls = {}

        for request in self.driver.requests:
            if request.response:
                if '.mp3' in request.url:
                    part_id = request.url.split("Part")[1].split(".mp3")[0]
                    part = int(part_id)
                    if part not in urls:
                        urls[part] = request.url
        self.mp3_urls = urls

    def __build_chapters(self, expected_length: str):
        print("Getting chapters")

        self.chapter_table_open.click()
        time.sleep(1)
        chapter_table_close = self.driver.find_element(By.CLASS_NAME, 'shibui-shield')
        try:
            chapter_dialog_table = self.driver.find_element(By.CLASS_NAME, 'chapter-dialog-table')
            if not chapter_dialog_table:
                raise Exception("Failed to find chapter dialog table")

            chapter_title_elements = chapter_dialog_table.find_elements(By.CLASS_NAME, 'chapter-dialog-row-button')
            chapter_time_elements = chapter_dialog_table.find_elements(By.CLASS_NAME, 'place-phrase-visual')

            if not chapter_title_elements:
                raise Exception("Failed to find chapter title elements")
            if not chapter_time_elements:
                raise Exception("Failed to find chapter time elements")

            self.chapter_markers = []
            chapter_times = []
    
            for elem in chapter_time_elements:
                if elem.text:
                    chapter_times.append(elem.text)
                    self.chapter_seconds.append(convert_metadata.to_seconds(elem.text))

            for index, title in enumerate(chapter_title_elements):
                # This will end with us in chapter 0.
                if index == 0:
                    title.click()
                # The end of each chapter is the start of the next.
                end = self.chapter_seconds[index+1] if index+1 < len(self.chapter_seconds) else None
                self.chapter_markers.append( (title.text, chapter_times[index], end) )
                print(f"Found chapter: '{title.text}' starting {chapter_times[index]}")
        finally:
            # Close chapter table
            chapter_table_close.click()
            time.sleep(1)

        self.chapter_seconds.append(self.expected_duration)

        print(f"Got {len(chapter_times)} chapters")

        if self.chapter_markers:
            # Modify the last chapter marker to end at the end of the book.
            title, start, _ = self.chapter_markers.pop()
            end = expected_length
        else:
            # Book has no chapters, give it a fake one.
            title, start, end = None, "0:0:0", expected_length
        self.chapter_markers.append( (title, start, end) )

        # Find what position we were placed at, also updating tables.
        self.get_current_location()

        print(f"Scanning {len(self.chapter_seconds)} chapters:", end='')

        # Partition the book by skimming through chapters and observing known parts.
        needs_end = True
        for ch in range(len(self.chapter_seconds)):
            # The reader sometimes dumps us into the middle of a chapter.
            if self.current_location > self.chapter_seconds[ch]:
                # We're indexing the parts at the start of each chapter, so check.
                if not self.move_backward_chapter():
                    raise Exception(f"ERROR: Failed to find beginning of chapter {ch}, should be at {to_hms(self.chapter_seconds[ch])} but no rewind allowed at {to_hms(self.current_location)}")
            if not self.move_forward_chapter():
                if ch == len(self.chapter_seconds) - 1 and self.current_location >= self.expected_duration - 1: # end of book
                    needs_end = False
                    print(f"Chapter skip stopped normally at chapter {ch} / {len(self.chapter_seconds)}")
                else:
                    raise Exception(f"ERROR: unexpected end of book at chapter {ch}/{len(self.chapter_seconds)}, at {to_hms(self.current_location)}/{to_hms(self.expected_duration)}")

        # TODO: honestly this doesn't seem needed, I should test whether it's ever run.
        if needs_end and not self.move_forward_chapter() and self.current_location < self.expected_duration - 1:
            raise Exception(f"ERROR: Failed to find end of book, at {to_hms(self.current_location)} / {to_hms(self.expected_duration)}")

        if not self.get_url(1):
            raise Exception(f"ERROR: Failed to find first part of book after loading contents.")

        print(f"Known parts and their hms offsets:")
        last_chapter = -1
        for part, offset in sorted(self.part_to_seconds.items()):
            ch = self.chapter_containing(offset)
            ch_text = ""
            if last_chapter != ch:
                last_chapter = self.chapter_containing(offset)
                ch_title = self.chapter_markers[ch][0]
                ch_text = f" (Chapter {ch}: {ch_title})"
            print(f"  {part:02d}: {to_hms(offset)} {ch_text}")

    def get_url(self, part: int) -> str|None:
        return self.mp3_urls.get(part)

    def move_forward_chapter(self) -> bool:
        enabled = self.chapter_next.is_enabled()
        if enabled:
            self.chapter_next.click()
            time.sleep(0.5)
        self.get_current_location()
        return enabled

    def move_backward_chapter(self) -> bool:
        enabled = self.chapter_previous.is_enabled()
        if enabled:
            self.chapter_previous.click()
            time.sleep(0.5)
        self.get_current_location()
        return enabled

    def get_current_location(self) -> int:
        s = self.timeline_current_time.get_attribute("textContent")
        if s is None:
            raise Exception("Couldn't get current time offset")
        self.current_location = convert_metadata.to_seconds(s)
        self.__update_seen_parts()
        return self.current_location

    def move_to_chapter(self, desired_chapter: int) -> int:
        self.chapter_table_open.click()
        time.sleep(1)
        chapter_table_close = self.driver.find_element(By.CLASS_NAME, 'shibui-shield')
        try:
            # Note that 'chapter-dialog-row' contains '...-button' and
            # '...-title', the latter is what we would print but can't be
            # clicked.
            chapter_title_elements = self.driver.find_elements(By.CLASS_NAME, 'chapter-dialog-row')
            clicked = False

            for index, title in enumerate(chapter_title_elements):
                # Go to the beginning of the chapter we need, or the
                # last chapter if we're trying to get to the end.
                if index == desired_chapter:
                    title.click()
                    clicked = True
                    break

            if not clicked:
                raise ValueError(f"Couldn't find chapter {desired_chapter} in {len(chapter_title_elements)} chapters")
        finally:
            # Close chapter table
            chapter_table_close.click()
        time.sleep(1)
        return self.get_current_location()

    def chapter_containing(self, current_location) -> int:
        candidate = None
        # Don't enumerate the last element, it's actually the end of the book.
        for i, start in enumerate(self.chapter_seconds[:-1]):
            if current_location >= start and current_location < self.chapter_seconds[i+1]:
                candidate = i
        # Account for the end of the book.
        return candidate if candidate is not None else len(self.chapter_seconds) - 2
    
    def closest_chapter_mark(self, lower_bound: int, upper_bound: int, current_location: int) -> Tuple[Tuple[int, int]|None, int]:
        """
            Finds the closest chapter mark to reach into the range near the lower bound.

            Args:
                lower_bound (int): Lower bound of the chapter marks.
                upper_bound (int): Upper bound of the chapter marks.
                current_location (int): Current location in the book.
        """
        current_chapter = self.chapter_containing(current_location)
        # Easy: the chapter containing the lower bound.
        earliest_chapter = self.chapter_containing(lower_bound)
        earliest_seconds = self.chapter_seconds[earliest_chapter]
        # Scan for the chapter after the lower bound.
        mid_chapter = earliest_chapter + 1
        mid_seconds = self.chapter_seconds[mid_chapter] if mid_chapter < len(self.chapter_seconds) else 0
        while mid_chapter < len(self.chapter_seconds) and self.chapter_seconds[mid_chapter] <= lower_bound:
            mid_chapter += 1
            mid_seconds = self.chapter_seconds[mid_chapter]
        # And the chapter after (not containing) the upper bound.
        ending_chapter = self.chapter_containing(upper_bound) + 1
        ending_seconds = self.chapter_seconds[ending_chapter] if ending_chapter < len(self.chapter_seconds) else 0
        while ending_chapter < len(self.chapter_seconds) and self.chapter_seconds[ending_chapter] < upper_bound:
            ending_chapter += 1
            ending_seconds = self.chapter_seconds[ending_chapter]
        print(f"Chapter marks: {earliest_chapter} < {mid_chapter} < {ending_chapter}, times: lb={to_hms(lower_bound)} {to_hms(earliest_seconds)} < {to_hms(mid_seconds)} < {to_hms(ending_seconds)} < ub={to_hms(upper_bound)}")
        if earliest_chapter < mid_chapter < ending_chapter:
            print(f"Found easy chapter mark {earliest_chapter} < {mid_chapter} < {ending_chapter}")
            return (mid_chapter, self.chapter_seconds[mid_chapter]), current_chapter
        # No easy chapter mark. We will assess three distances:
        # 1. Earliest chapter start to lower bound
        earliest_chapter_start = self.chapter_seconds[earliest_chapter]
        earliest_distance = abs(lower_bound - earliest_chapter_start)

        # 2. Ending chapter start to lower bound
        ending_chapter_start = self.chapter_seconds[ending_chapter]
        ending_distance = abs(lower_bound - ending_chapter_start)

        # 3. Current location to lower bound
        current_distance = abs(lower_bound - current_location)
        # Simple but sad case: no chapter jump will help.
        if current_distance <= earliest_distance and current_distance <= ending_distance:
            return None, current_chapter

        # Otherwise, choose the chapter that's closest to the lower bound.
        desired_chapter = earliest_chapter if earliest_distance <= ending_distance else ending_chapter
        return (desired_chapter, self.chapter_seconds[desired_chapter]), current_chapter

    def has_url(self, part_num) -> bool:
        self.__requests_to_mp3_files()
        return part_num in self.mp3_urls

