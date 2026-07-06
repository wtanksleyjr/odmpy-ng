#!/bin/python3

import sys
import os
import argparse
import subprocess
from pathlib import Path

def main():
    # Fetch default directories from the environment if present
    default_dest = os.getenv('AUDIOBOOK_FOLDER', None)
    default_tmp = os.getenv('AUDIOBOOK_TMP', None)

    # Configure option parser
    parser = argparse.ArgumentParser(
        description="Helper to run odmpy-ng via Docker Compose with proper environment variables mapping."
    )
    parser.add_argument(
        '-d', '--dest',
        type=str,
        default=default_dest,
        help=f'Final storage directory (default: $AUDIOBOOK_FOLDER={default_dest})'
    )
    parser.add_argument(
        '-t', '--tmp',
        type=str,
        default=default_tmp,
        help='Temporary files directory (default: $AUDIOBOOK_TMP or dest/tmp)'
    )

    # Use parse_known_args to capture all other arguments to pass directly to the container
    opts, extra_args = parser.parse_known_args()

    if not opts.dest:
        print("Error: no destination directory specified. Use -d or set the AUDIOBOOK_FOLDER environment variable.")
        sys.exit(1)

    dest = Path(opts.dest)
    tmp = dest / 'tmp' if not opts.tmp else Path(opts.tmp)

    # Determine UID and GID with non-Unix fallbacks
    uid = getattr(os, 'getuid', lambda: 1000)()
    gid = getattr(os, 'getgid', lambda: 1000)()

    # Prepare environment for Docker Compose
    env = os.environ.copy()
    env["HOST_UID"] = str(uid)
    env["HOST_GID"] = str(gid)
    env["AUDIOBOOK_FOLDER"] = str(dest.absolute())
    env["AUDIOBOOK_TMP"] = str(tmp.absolute())

    # Check if config/config.json exists
    config_path = Path("config/config.json")
    if not config_path.exists():
        print("Error: 'config/config.json' was not found. Please create your config file first.")
        sys.exit(1)

    # Check if the docker image is built; if not, build it automatically
    has_image = True
    try:
        has_image = subprocess.call(
            ["docker", "image", "inspect", "odmpy-ng:dev"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        ) == 0
    except FileNotFoundError:
        print("Error: The 'docker' command line tool was not found in your PATH.")
        print("Please ensure Docker and Docker Compose are installed and running on your system.")
        sys.exit(127)
    except Exception:
        # Fall back to True for other unexpected errors during inspection
        has_image = True

    if not has_image:
        print("Docker image 'odmpy-ng:dev' not found. Building it now...")
        try:
            build_res = subprocess.call(["docker", "compose", "build", "odmpy-ng"], env=env)
            if build_res != 0:
                print("Error: Failed to build the odmpy-ng Docker image.")
                sys.exit(build_res)
        except FileNotFoundError:
            print("Error: The 'docker' command line tool was not found in your PATH.")
            print("Please ensure Docker and Docker Compose are installed and running on your system.")
            sys.exit(127)

    # Formulate command to run the container
    # If "run" is present in extra_args, strip it to keep the syntax backwards-compatible
    if extra_args and extra_args[0] == "run":
        extra_args = extra_args[1:]

    # Detect TTY/Interactive options dynamically
    interactive_flags = []
    if sys.stdin.isatty():
        interactive_flags.append("-i")
    if sys.stdout.isatty():
        interactive_flags.append("-t")

    docker_cmd = ["docker", "compose", "run", "--remove-orphans", "--rm"] + interactive_flags + ["odmpy-ng"] + extra_args

    print(f"Running odmpy-ng via Docker Compose...")
    try:
        res = subprocess.call(docker_cmd, env=env)
        sys.exit(res)
    except FileNotFoundError:
        print("Error: The 'docker' command line tool was not found in your PATH.")
        print("Please ensure Docker and Docker Compose are installed and running on your system.")
        sys.exit(127)
    except KeyboardInterrupt:
        print("\nOperation cancelled.")
        sys.exit(130)

if __name__ == '__main__':
    main()

