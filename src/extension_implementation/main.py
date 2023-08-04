import eyepy as ep
import importlib
from pathlib import Path
from typing import Optional, Any
from datetime import datetime
import argparse
import sys
import os
import json
import numpy

CORRESPONDING_READERS = {
    ".fds": {"module_name": "fds", "class_name": "FDS"},
    ".fda": {"module_name": "fda", "class_name": "FDA"},
    ".e2e": {"module_name": "e2e", "class_name": "E2E"},
    ".E2E": {"module_name": "e2e", "class_name": "E2E"},
    ".img": {"module_name": "img", "class_name": "IMG"},
    ".OCT": {"module_name": "boct", "class_name": "BOCT"},
    ".oct": {"module_name": "poct", "class_name": "POCT"},
    ".dcm": {"module_name": "dicom", "class_name": "Dicom"},
}


def check_directory_exists(input_folder: str) -> None:
    if not os.path.exists(input_folder):
        print(
            f"Error: The provided directory '{input_folder}' does not exist.",
            file=sys.stderr,
        )
        sys.exit(1)


def main(input_folder: str):

    e2e_files = []
    all_files = os.listdir(input_folder)
    for single_file in all_files:
        if Path(single_file).suffix == ".e2e":
            e2e_files.append(single_file)

    for _e2e in e2e_files:
        final_file_path = Path(input_folder).joinpath(_e2e)
        ev = ep.import_heyex_e2e(final_file_path)

        print(ev)
        print(type(ev))
        print(dir(ev))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Process a folder with files")
    parser.add_argument("input_folder", help="Path to the folder containing the files")
    args = parser.parse_args()

    check_directory_exists(args.input_folder)
    main(args.input_folder)
