from dataclasses import dataclass, field, InitVar
from typing import Tuple
from selenium.webdriver.remote.webelement import WebElement
import seleniumwire, seleniumwire.webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.wait import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
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
            subs = WebDriverWait(self.driver, 5).until(
                    lambda _ : (elts := sublibrary_output.find_elements(By.TAG_NAME, 'li'))
                                and all((el.is_displayed() and el.is_enabled()) for el in elts)
                                and elts # return the found list.
                )
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
        toggle_play = WebDriverWait(self.driver, 5).until(EC.element_to_be_clickable((By.CLASS_NAME, 'playback-toggle')))

        # Fetch player elements
        timeline_length = self.driver.find_element(By.CLASS_NAME, 'timeline-end-minutes').find_element(By.CLASS_NAME, 'place-phrase-visual')
        contents = timeline_length.get_attribute("textContent")
        if not contents:
            raise Exception("Failed to fetch timeline length")
        expected_time = contents.replace("-", "")

        # Allow debugging if one or more fails to fetch. Also turns off "may be None" warnings.
        if toggle_play is None:
            raise Exception(f"Failed to fetch play button: {selected_title_link}")

        print(f"Final book should be ~{expected_time} in length.")

        mp3_searcher = Mp3Searcher(self, expected_time)

        # Download part files
        print("Getting files")
        loaded_duration = convert_metadata.get_total_duration(download_path)
        part_num = 1

        # Check for resumable part files.
        print("Checking for resumable parts.")
        tmp_dir = download_path
        if loaded_duration:
            parts = [self.get_part_basename(pn)+".mp3" for pn in range(1, max(mp3_searcher.part_to_seconds)+1)]

            # Check that the dir contains sequential part files.
            resumable_parts = [part for part in parts if os.path.isfile(os.path.join(tmp_dir, part))]
            if not resumable_parts or resumable_parts != parts[:len(resumable_parts)]:
                print(f"ERROR: tmp folder contains corrupted download (check numbered parts), please remove or clean up: {tmp_dir}")
                print(resumable_parts)
                sys.exit(1)
            resumable_parts = [fullname for part in parts if os.path.isfile(fullname := os.path.join(tmp_dir, part))]

            # Sum the durations of the resumable parts.
            exact_size = 0.0
            for part_num, fullname in enumerate(resumable_parts, 1):
                this_size = convert_metadata.get_mp3_duration(fullname)
                exact_size += this_size
                mp3_searcher.register_downloaded_part(part_num, int(exact_size))

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
                    mp3_searcher.register_downloaded_part(part_num, loaded_duration)
                    print(f"{to_hms(loaded_duration)} / {to_hms(mp3_searcher.expected_duration)} - {loaded_duration}/{mp3_searcher.expected_duration} sec  -  {loaded_duration/mp3_searcher.expected_duration*100.0:.2f}%")
                    part_num += 1
                    continue
                else:
                    raise Exception(f"Download failed for part {part_num}")

            # Begin search for the absent part. Initialize some state.
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

                # Require progress on each iteration.
                if upper_bound == old_upper_bound:
                    # Otherwise, we can't find the part. Make a heroic effort.
                    print(f"Locations before repositioning for play: {to_hms(lower_bound)} <= {to_hms(mp3_searcher.current_location)} < {to_hms(upper_bound)}")
                    if not mp3_searcher.move_to(lower_bound) or not mp3_searcher.get_current_location() <= lower_bound:
                        raise Exception(f"Couldn't find part {part_num} between ({to_hms(lower_bound)}, {to_hms(upper_bound)}), failed to move {to_hms(mp3_searcher.current_location)} near lower bound.")
                    if mp3_searcher.has_new_bounds():
                        continue
                    old_loc = mp3_searcher.current_location
                    span = max(upper_bound - mp3_searcher.current_location, 6)
                    print(f"Using play toggle between {to_hms(mp3_searcher.current_location)} - {to_hms(upper_bound)} ({span}s)...")
                    try:
                        toggle_play.click()
                        print(f"Pressed play, waiting for response: ", end="")
                        while old_loc == mp3_searcher.get_current_location():
                            time.sleep(1)
                            print(".", end="")
                        print(" found!")

                        print(f"Played to: {to_hms(mp3_searcher.current_location)}... ", end="")
                        while not mp3_searcher.has_url(part_num) and old_loc+span > mp3_searcher.current_location:
                            mp3_searcher.get_current_location()
                            print(f"{to_hms(mp3_searcher.current_location)}... ", end="")
                            time.sleep(5)
                        print()
                    finally:
                        toggle_play.click()
                    if mp3_searcher.has_url(part_num):
                        print(f"Found part by using play toggle between {lower_bound}, {upper_bound}")
                        continue
                    raise Exception(f"Need more precise search for {to_hms(upper_bound - lower_bound)} range between {to_hms(lower_bound)}, {to_hms(upper_bound)}")
                old_upper_bound = upper_bound

                # Handle a "collapse" (lower==upper) by trying to search the whole book.
                if not collapse_detected and lower_bound == upper_bound:
                    print(f"Could not find part {part_num}, retrying by searching the rest of the book.")
                    upper_bound = mp3_searcher.expected_duration
                    collapse_detected = True

                middle = (lower_bound+upper_bound)//2
                if lower_bound+soft_lower_bound+1 < middle:
                    # First, go close to the lower bound, the next part should follow it closely.
                    print(f"Locations before finding data following soft lower bound of {soft_lower_bound}s: {to_hms(lower_bound)} <= {to_hms(mp3_searcher.current_location)} < {to_hms(upper_bound)}")
                    mp3_searcher.move_to( mp3_searcher.lower_bound + soft_lower_bound + 1 )
                    soft_lower_bound *= 2
                    if mp3_searcher.has_new_bounds():
                        continue

                # Next, divide the range in half, getting a better limit on the size.
                # This should establish new bounds, so the loop will restart.
                print(f"Locations before halving to {to_hms(middle)}: {to_hms(lower_bound)} <= {to_hms(mp3_searcher.current_location)} < {to_hms(upper_bound)}")
                mp3_searcher.move_to( middle )
                if mp3_searcher.has_new_bounds():
                    continue

                # Next, position ON the lower bound. This should set up for play-toggle to find the part.
                if not mp3_searcher.move_to( mp3_searcher.lower_bound ) and not mp3_searcher.has_new_bounds():
                    print(f"No URL for {part_num} found beneath {to_hms(upper_bound)}.")
                if mp3_searcher.has_new_bounds():
                    continue

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
    downloaded_duration: int = field(default=0)
    expected_duration: int = field(init=False)
    marked_new_bounds: bool = field(default=False)
    part_num: int = field(init=False)
    lower_part: int = field(init=False)
    upper_part: int = field(init=False)
    lower_bound: int = field(init=False)
    upper_bound: int = field(init=False)
    timeline_current_time: WebElement = field(init=False)
    chapter_table_open: WebElement = field(init=False)
    chapter_previous: WebElement = field(init=False)
    chapter_next: WebElement = field(init=False)
    mp3_urls: dict[int, str] = field(default_factory=dict)
    seen_parts: set[int] = field(default_factory=set)
    chapter_seconds: list[int] = field(default_factory=list)
    part_to_seconds: dict[int, int] = field(default_factory=dict)
    seconds_to_part: dict[int, int] = field(default_factory=dict)

    def __post_init__(self, expected_length: str):
        assert(self.scraper.driver)
        self.driver = self.scraper.driver

        self.expected_duration = convert_metadata.to_seconds(expected_length)
        self.part_to_seconds[1] = 0
        self.seconds_to_part[0] = 1
        self.part_num = 1
        self.seen_parts.add(1)
        self.upper_bound = self.lower_bound = self.lower_part = self.upper_part = -1

        self.timeline_current_time = self.driver.find_element(By.CLASS_NAME, 'timeline-start-minutes').find_element(By.CLASS_NAME, 'place-phrase-visual')
        self.chapter_table_open = self.driver.find_element(By.CLASS_NAME, 'chapter-bar-title-button')
        self.chapter_previous = self.driver.find_element(By.CLASS_NAME, 'chapter-bar-prev-button')
        self.chapter_next = self.driver.find_element(By.CLASS_NAME, 'chapter-bar-next-button')

        self.__build_chapters(expected_length)
        self.get_current_location()

    def find_bounds_at(self, location: int) -> tuple[tuple[int, int], tuple[int, int]]:
        lower_part = max([p for p, s in self.part_to_seconds.items()
                            if s <= location
                         ], default=0)
        return self.find_bounds(lower_part)

    def find_bounds(self, part_num: int) -> tuple[tuple[int, int], tuple[int, int]]:
        lower_bound, lower_part = max([(s,p) for s, p in self.seconds_to_part.items()
                                          if p <= part_num and s >= self.downloaded_duration
                                      ],
                                      default=(self.downloaded_duration,0) )
        upper_bound, upper_part = min([(s, p) for s, p in self.seconds_to_part.items()
                                        if s > lower_bound and p > part_num
                                      ],
                                      default=(self.expected_duration,max(self.part_to_seconds.keys())))
        return (lower_bound, upper_bound), (lower_part, upper_part)

    def set_bounds(self, part_num: int) -> tuple[int, int]:
        old_state = (self.part_num, (self.lower_bound, self.upper_bound), (self.lower_part, self.upper_part))
        new_state = part_num, self.find_bounds(part_num)
        if old_state == new_state:
            raise Exception(f"ERROR: Set bounds to the same thing twice: {old_state}")
        self.part_num, ((self.lower_bound, self.upper_bound), (self.lower_part, self.upper_part)) = new_state
        self.marked_new_bounds = False

        return self.lower_bound, self.upper_bound

    def register_downloaded_part(self, part_num: int, downloaded_duration: int):
        """
            Called when the end of `part_num` is known, usually due to downloading it.
        """
        # We have now "seen" this part at the end of the known duration.
        self.downloaded_duration = downloaded_duration
        self.part_to_seconds[part_num] = downloaded_duration
        self.seconds_to_part[downloaded_duration] = part_num
        self.lower_bound = downloaded_duration

    def __update_seen_parts(self):
        self.__requests_to_mp3_files()
        parts = set(self.mp3_urls.keys()) - self.seen_parts
        if parts:
            part = min(parts)
            if len(parts) > 1:
                # This would imply we somehow loaded part URLs but didn't call
                # this function soon enough to know where they were.
                print(f"WARNING: Skipped {len(parts)-1} part(s) ({sorted(parts)} except {part})")
            self.__add_part_sighting(part, self.current_location)
            if self.current_location > self.lower_bound:
                print(f"(Seen part {part} at {to_hms(self.current_location)} in chapter {self.chapter_containing(self.current_location)})")
        self.seen_parts.update(parts)

        # If we found a new URL, accounting complete.
        if parts:
            return

        # Check if we already accounted for this exact location in the past.
        if self.current_location in self.seconds_to_part:
            return

        # Otherwise try to infer the part number, if possible.
        second_range, part_range = self.find_bounds_at(self.current_location)

        # If the lower bound is already downloaded, we know for sure it ended
        # there. (Otherwise I return early, I don't know if I can infer.)
        if not second_range[0] <= self.downloaded_duration:
            return

        # Now we know we can't be in the lower part because the lower_bound is
        # its end, and because we didn't see an URL we can't be in the
        # part_num. That leaves only the upper part.
        if part_range[1] - part_range[0] == 1 and part_range[0] < self.part_num < part_range[1]:
            self.__add_part_sighting(part_range[1], self.current_location)

    def __add_part_sighting(self, part_num: int, location: int):
        self.seconds_to_part.setdefault(location, part_num)
        self.part_to_seconds.setdefault(part_num, location)

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
        # I'd like to remove all of the sleeps, but this Wait doesn't work, it says it's clickable but it's not.
        # WebDriverWait(self.driver, 5).until(EC.element_to_be_clickable((By.CLASS_NAME, 'shibui-shield')))
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
            if len(chapter_title_elements) != len(chapter_time_elements):
                raise Exception(f"Found {len(chapter_title_elements)} chapter title elements but {len(chapter_time_elements)} chapter time elements")

            self.chapter_markers = []
            chapter_times = []
    
            for ch, elem in enumerate(chapter_time_elements):
                if elem.text:
                    chapter_times.append(elem.text)
                    try:
                        self.chapter_seconds.append(convert_metadata.to_seconds(elem.text))
                    except Exception as e:
                        raise ValueError(f"Failed to parse chapter time: '{elem.text}' for chapter {ch}") from e
                else:
                    raise Exception(f"Found blank chapter time for chapter {ch}")

            for index, title in enumerate(chapter_title_elements):
                # The end of each chapter is the start of the next.
                end = self.chapter_seconds[index+1] if index+1 < len(self.chapter_seconds) else None
                self.chapter_markers.append( (title.text, chapter_times[index], end) )
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
        loc = self.get_current_location()
        # Move to the beginning and end of the book, finding the extreme part numbers.
        self.move_to_chapter(0)
        self.move_to_chapter(len(self.chapter_seconds) - 1)
        # And back to where we were.
        self.move_to_chapter(self.chapter_containing(loc))

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

    def has_url(self, part_num) -> bool:
        self.__requests_to_mp3_files()
        return part_num in self.mp3_urls

    def has_new_bounds(self) -> bool:
        if self.marked_new_bounds:
            return True
        # Assume new unless proven otherwise.
        self.marked_new_bounds = True
        if self.has_url(self.part_num):
            print(f"(Found MP3 URL for part {self.part_num}, continuing.)")
            return True
        bounds, parts = self.find_bounds(self.part_num)
        if bounds != (self.lower_bound, self.upper_bound):
            print(f"(Found new bounds for part {self.part_num}: {to_hms(bounds[0])} to {to_hms(bounds[1])} (was {to_hms(self.lower_bound)} to {to_hms(self.upper_bound)}))")
            return True
        if parts != (self.lower_part, self.upper_part):
            print(f"(Found new parts for part {self.part_num}: {parts} (was {self.lower_part}, {self.upper_part}))")
            return True
        # Proven otherwise!
        self.marked_new_bounds = False
        return False

    def chapter_boundary_closest(self, target: int) -> tuple[int|None,int|None]:
        # Return None if current_location is closest, otherwise return the
        # chapter closer to the target than current_location is.
        current_distance = abs(target - self.current_location)
        close_chapters = ((s,ch) for ch,s in enumerate(self.chapter_seconds) if abs(s - target) < current_distance)
        # Define "best" as the chapter closest to the target.
        best_s, best_ch = min(close_chapters, key=lambda s: (abs(s[0] - target), s),
                      default=(None, None))
        return best_s, best_ch

    def move_by_chapters(self, target: int) -> bool:
        start = self.current_location
        # Check if there's a chapter marker we can skip to closer to the target.
        boundary, ch = self.chapter_boundary_closest(target)
        if boundary is None or ch is None:
            return False
        self.move_to_chapter(ch)
        if start == self.current_location and not self.has_new_bounds():
            raise Exception(f"ERROR: Failed to move AT ALL rather than to desired chapter {ch} at {to_hms(boundary)} between {to_hms(target)} and original location {to_hms(start)}, currently {to_hms(self.current_location)}")
        return True

    def move_by_nudges(self, target: int, acceptable_bounds: tuple[int,int]) -> bool:
        return self.__move_primitive(target, left=Keys.ARROW_LEFT, right=Keys.ARROW_RIGHT, bounds=acceptable_bounds)

    def move_by_minutes(self, target: int, acceptable_bounds: tuple[int,int]) -> bool:
        return self.__move_primitive(target, left=Keys.PAGE_UP, right=Keys.PAGE_DOWN, bounds=acceptable_bounds)

    def __move_primitive(self, target: int, left: str, right: str, bounds: tuple[int,int]) -> bool:
        bottom, top = bounds
        def success(self): return target+bottom < self.current_location <= target+top or self.has_new_bounds()
        if success(self):
            return True

        body = self.driver.find_element(By.TAG_NAME, "body")
        key = left if self.current_location > target else right
        direction = "left" if key == left else "right"
        while not success(self):
            old = self.current_location
            body.send_keys(key)
            time.sleep(1)
            self.get_current_location()
            if not success(self) and self.current_location == old:
                # This implies we're at either end of the book.
                print(f"Failed to move {direction} from {to_hms(self.current_location)} to {to_hms(target)}")
                return False
        return self.current_location <= target < self.current_location+15

    def move_forward_chapter(self) -> bool:
        enabled = self.chapter_next.is_enabled()
        if enabled:
            self.chapter_next.click()
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

    def move_to(self, goal: int) -> bool:
        start = self.get_current_location()
        if self.has_new_bounds():
            return True
        if goal-15 < start <= goal:
            # Already there
            print(f"Already at {to_hms(self.current_location)} nearly == requested {to_hms(goal)}")
            return True
        # Print one flushed line, then construct the rest.
        print(f"Moving from {to_hms(start)}:")
        print(f"   to {to_hms(goal)}", flush=True)
        # Chapter movement will get as close as possible in one move.
        if self.move_by_chapters(goal):
            ch = self.chapter_containing(self.current_location)
            print(f" to chapter {ch}", flush=True)
        if not goal-60 < self.current_location <= goal and not self.has_new_bounds():
            self.move_by_minutes(goal, acceptable_bounds=(-60, 0))
        if not goal-15 < self.current_location <= goal and not self.has_new_bounds():
            self.move_by_nudges(goal, acceptable_bounds=(-15, 0))
        print(f" to {to_hms(self.current_location)}.", flush=True)
        if self.has_new_bounds():
            return True
        if not goal-15 < self.current_location <= goal:
            direction = "forward" if goal - self.current_location > 0 else "backward"
            raise Exception(f"Couldn't get from {to_hms(start)} to {to_hms(goal)}, now at {to_hms(self.current_location)}, so missed by {to_hms(abs(goal - self.current_location))} {direction}")
        return True

    def move_to_chapter(self, desired_chapter: int) -> int:
        wants_end = (desired_chapter == len(self.chapter_seconds) - 1)
        if wants_end:
            desired_chapter -= 1

        self.chapter_table_open.click()
        #chapter_table_close = WebDriverWait(self.driver, 1).until(EC.element_to_be_clickable((By.CLASS_NAME, 'shibui-shield')))
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
        if wants_end:
            self.move_forward_chapter()
        return self.get_current_location()

    def chapter_containing(self, s: int) -> int:
        candidate = None
        # Don't enumerate the last element, it's actually the end of the book.
        for i, start in enumerate(self.chapter_seconds[:-1]):
            if s >= start and s < self.chapter_seconds[i+1]:
                candidate = i
        # Account for the end of the book.
        return candidate if candidate is not None else len(self.chapter_seconds) - 2
    
