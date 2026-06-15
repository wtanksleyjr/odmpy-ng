import json
import pathlib
import time
from atomicwrites import atomic_write

def download_cover(context, cover_url: str, download_path: str, abort=False) -> bool:
    """
    Downloads a cover image from the given URL using Playwright's shared session context.
    """
    max_retries = 3
    delay = 2
    response = None
    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            response = context.request.get(cover_url)
            if response.ok:
                last_error = None
                break
            else:
                print(f"Cover download attempt {attempt}/{max_retries} failed with status code {response.status}")
                last_error = Exception(f"Cover download failed with status {response.status}")
        except Exception as e:
            print(f"Cover download attempt {attempt}/{max_retries} threw connection exception: {e}")
            last_error = e
        
        if attempt < max_retries:
            time.sleep(delay)
            delay *= 2

    if response and response.ok:
        try:
            with atomic_write(download_path, mode="wb", overwrite=True) as f:
                f.write(response.body())
            return True
        except Exception as e:
            print(f"Error writing cover image to disk: {e}")
            if abort:
                raise e
            return False
    else:
        if abort and last_error:
            raise last_error
        return False

def download_thunder_metadata(context, book_id: str, download_path: pathlib.Path) -> bool:
    """
    Downloads metadata utilizing the Overdrive Thunder API under the current active session.
    """
    if download_path.exists():
        return True

    api_url = f"https://thunder.api.overdrive.com/v2/media/{book_id}"
    max_retries = 3
    delay = 2
    response = None
    for attempt in range(1, max_retries + 1):
        try:
            response = context.request.get(api_url)
            if response.ok:
                break
            else:
                print(f"Metadata download attempt {attempt}/{max_retries} failed with status {response.status}")
        except Exception as e:
            print(f"Metadata download attempt {attempt}/{max_retries} threw connection exception: {e}")
        
        if attempt < max_retries:
            time.sleep(delay)
            delay *= 2

    if response and response.ok:
        try:
            with atomic_write(download_path, overwrite=True) as f:
                json.dump(response.json(), f, ensure_ascii=False, indent=4)
            return True
        except Exception as e:
            print(f"Error writing metadata to disk: {e}")
            return False
    else:
        return False

