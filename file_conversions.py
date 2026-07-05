#!/usr/bin/env python3
"""
Audiobook file conversion utilities using ffmpeg.
"""
import subprocess
import glob
import os
import multiprocessing
from concurrent.futures import ThreadPoolExecutor

def encode_one_file(args):
    src_file, dest_file, bitrate = args
    cmd = [
        "ffmpeg", "-y", "-i", src_file,
        "-c:a", "aac", "-b:a", bitrate,
        dest_file
    ]
    try:
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        return True
    except Exception as e:
        print(f"Error encoding {src_file}: {e}")
        return False

def encode_aac_multiprocessing(src_dir, dest_dir, low_quality=False, num_workers=None):
    os.makedirs(dest_dir, exist_ok=True)
    src_files = sorted(glob.glob(os.path.join(src_dir, "*.mp3")))
    if not src_files:
        print("No MP3 files found to encode.")
        return False

    bitrate = "32k" if low_quality else "64k"

    tasks = []
    for f in src_files:
        base = os.path.splitext(os.path.basename(f))[0]
        dest_file = os.path.join(dest_dir, f"{base}.m4a")
        tasks.append((f, dest_file, bitrate))

    if num_workers is None:
        num_workers = multiprocessing.cpu_count()

    success = True
    with ThreadPoolExecutor(max_workers=num_workers) as executor:
        results = list(executor.map(encode_one_file, tasks))
        if not all(results):
            success = False

    return success

def concat_m4b(src_dir, dest_dir, output_filename):
    m4a_files = sorted(glob.glob(os.path.join(src_dir, "*.m4a")))
    if not m4a_files:
        print("No m4a files found to concatenate.")
        return False

    list_file_path = os.path.join(src_dir, "concat_list.txt")
    with open(list_file_path, "w", encoding="utf-8") as lf:
        for f in m4a_files:
            escaped_f = f.replace("'", "'\\''")
            lf.write(f"file '{escaped_f}'\n")

    output_path = os.path.join(dest_dir, output_filename)
    cmd = [
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", list_file_path, "-c", "copy", output_path
    ]
    try:
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        return True
    except Exception as e:
        print(f"Error concatenating files to M4B: {e}")
        return False
    finally:
        try:
            os.unlink(list_file_path)
        except Exception:
            pass

def concat_mka(src_dir, dest_dir, output_filename):
    mp3_files = sorted(glob.glob(os.path.join(src_dir, "*.mp3")))
    if not mp3_files:
        print("No MP3 files found to concatenate.")
        return False

    list_file_path = os.path.join(src_dir, "concat_list.txt")
    with open(list_file_path, "w", encoding="utf-8") as lf:
        for f in mp3_files:
            escaped_f = f.replace("'", "'\\''")
            lf.write(f"file '{escaped_f}'\n")

    output_path = os.path.join(dest_dir, output_filename)
    cmd = [
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", list_file_path, "-c", "copy", output_path
    ]
    try:
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        return True
    except Exception as e:
        print(f"Error concatenating files to MKA: {e}")
        return False
    finally:
        try:
            os.unlink(list_file_path)
        except Exception:
            pass

def encode_metadata(tmp_dir, input_file, output_file, metadata_file, cover_path=None):
    input_path = os.path.join(tmp_dir, input_file)
    meta_path = os.path.join(tmp_dir, metadata_file)

    is_mka_or_mkv = str(output_file).lower().endswith(('.mka', '.mkv'))

    cmd = ["ffmpeg", "-y"]

    # 1. Input file
    cmd += ["-i", input_path]

    # 2. Metadata file (if exists)
    has_meta = os.path.exists(meta_path)
    if has_meta:
        cmd += ["-i", meta_path, "-map_metadata", "1"]
    else:
        cmd += ["-map_metadata", "0"]

    # 3. Cover art (if exists)
    if cover_path and os.path.exists(cover_path):
        if is_mka_or_mkv:
            mime_type = "image/png" if str(cover_path).lower().endswith(".png") else "image/jpeg"
            cmd += [
                "-attach", str(cover_path),
                "-metadata:s:t", f"mimetype={mime_type}",
                "-map", "0:a",
                "-c:a", "copy"
            ]
        else:
            cmd += ["-i", str(cover_path)]
            cover_idx = "2" if has_meta else "1"
            cmd += ["-map", "0:a", "-map", f"{cover_idx}:v", "-c:a", "copy", "-c:v", "copy", "-disposition:v", "attached_pic"]
    else:
        cmd += ["-map", "0:a", "-c:a", "copy"]

    cmd.append(str(output_file))

    try:
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        return True
    except Exception as e:
        print(f"Error encoding metadata: {e}")
        return False
