import json
import pathlib
from atomicwrites import atomic_write

def download_cover(context, cover_url: str, download_path: str, abort=False) -> bool:
    """
    Downloads a cover image from the given URL using Playwright's shared session context.
    """
    try:
        response = context.request.get(cover_url)
        if response.ok:
            with atomic_write(download_path, mode="wb", overwrite=True) as f:
                f.write(response.body())
            return True
        else:
            print(f"Failed to download cover with status code {response.status}")
            if abort:
                raise Exception(f"Cover download failed with status {response.status}")
            return False
    except Exception as e:
        print(f"Error downloading cover: {e}")
        if abort:
            raise e
        return False

def download_thunder_metadata(context, book_id: str, download_path: pathlib.Path) -> bool:
    """
    Downloads metadata utilizing the Overdrive Thunder API under the current active session.
    """
    if download_path.exists():
        return True

    api_url = f"https://thunder.api.overdrive.com/v2/media/{book_id}"
    try:
        response = context.request.get(api_url)
        if response.ok:
            with atomic_write(download_path, overwrite=True) as f:
                json.dump(response.json(), f, ensure_ascii=False, indent=4)
            return True
        else:
            print(f"Failed to fetch metadata with status {response.status}")
            return False
    except Exception as e:
        print(f"Error downloading metadata: {e}")
        return False

