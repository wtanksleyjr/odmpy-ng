#!/bin/python3

import sys
import os
import argparse
import subprocess
from pathlib import Path
import textwrap

def main():
    # Fetch default directories from the environment if present
    default_dest = os.getenv('AUDIOBOOK_FOLDER', None)
    default_tmp = os.getenv('AUDIOBOOK_TMP', None)

    # Configure option parser
    parser = argparse.ArgumentParser(
        usage="%(prog)s [-h] [-d DEST] [-t TMP] [{run,build}] [container_args ...]",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Helper to run odmpy-ng via Docker Compose with proper environment variables mapping.",
        epilog=textwrap.dedent("""
            Commands:
              run                 Build the Docker image (if missing) and run the container with container_args.
              build               Only compile the Docker image (default).

            Example:
                ./build-compose.py -d /path/to/destination -t /path/to/tmp run -L all -a
                (runs and displays a table of all books that would be downloaded)
            Or, setting up the environment variables instead:
                export AUDIOBOOK_FOLDER=/path/to/destination
                export AUDIOBOOK_TMP=/path/to/tmp
                ./build-compose.py run -L all -a
            The 'tmp' directory should not be pointed to your machine's /tmp folder, as it's used for fairly large files.
        """)
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

    # Partition command line arguments to separate build-compose.py's own arguments
    # from the command ('run' or 'build') and any container arguments.
    args_list = sys.argv[1:]
    command = 'build'
    container_args = []

    if 'run' in args_list:
        idx = args_list.index('run')
        our_args = args_list[:idx]
        command = 'run'
        container_args = args_list[idx+1:]
    elif 'build' in args_list:
        idx = args_list.index('build')
        our_args = args_list[:idx] + args_list[idx+1:]
        command = 'build'
    else:
        our_args = args_list
        command = 'build'

    opts = parser.parse_args(our_args)

    if not opts.dest or not opts.tmp:
        if not opts.dest:
            print(f"Error: --dest is missing (and not given in $AUDIOBOOK_FOLDER): {opts.dest}")
        if not opts.tmp:
            print(f"Error: --tmp is missing (and not given in $AUDIOBOOK_TMP): {opts.tmp}")
        parser.print_help()
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
        print("Error: 'config/config.json' was not found. Please create your config file first, see README.md.")
        sys.exit(1)

    if command == "build":
        # Force a rebuild.
        has_image = False
    else:
        # Check if the docker image is built; if not, we will build it automatically
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
            # Fall back to False if inspect fails (e.g. image does not exist)
            has_image = False

    if not has_image:
        print("Building the odmpy-ng Docker image...")
        try:
            build_res = subprocess.call(["docker", "compose", "build", "odmpy-ng"], env=env)
            if build_res != 0:
                print("Error: Failed to build the odmpy-ng Docker image.")
                sys.exit(build_res)
            print("Docker image built successfully.")
            if command == "build":
                sys.exit(0)
        except FileNotFoundError:
            print("Error: The 'docker' command line tool was not found in your PATH.")
            print("Please ensure Docker and Docker Compose are installed and running on your system.")
            sys.exit(127)

    if command == "run":
        # Detect TTY/Interactive options dynamically
        interactive_flags = []
        if sys.stdin.isatty():
            interactive_flags.append("-i")
        if sys.stdout.isatty():
            interactive_flags.append("-t")

        docker_cmd = ["docker", "compose", "run", "--remove-orphans", "--rm"] + interactive_flags + ["odmpy-ng"] + container_args

        print("Running odmpy-ng via Docker Compose...")
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

