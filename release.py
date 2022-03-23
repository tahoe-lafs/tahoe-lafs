#! /usr/bin/env python
# Tahoe-LAFS -- secure, distributed storage grid
#
# Copyright © 2006-2012 The Tahoe-LAFS Software Foundation
#
# This file is part of Tahoe-LAFS.
#
# See the docs/about.rst file for licensing information.
import os
import glob
import shutil
import re
import sys
import subprocess
import argparse
import datetime


class bcolors:
    HEADER = "\033[95m"
    OKBLUE = "\033[94m"
    OKCYAN = "\033[96m"
    OKGREEN = "\033[92m"
    WARNING = "\033[93m"
    FAIL = "\033[91m"
    ENDC = "\033[0m"
    BOLD = "\033[1m"
    UNDERLINE = "\033[4m"


parser = argparse.ArgumentParser()
parser.add_argument(
    "--clean", action="store_true", help="Cancel existing release process, clean files"
)
parser.add_argument(
    "--ignore-deps", action="store_true", help="Ignore dependency checks"
)
parser.add_argument("--fin", action="store_true", help="Finish release proccess")
parser.add_argument("--sign", type=str, help="Signing key")
parser.add_argument("--ticket", type=int, help="Ticket number", required=True)
parser.add_argument("--tag", type=str, help="Release tag", required=True)
args = parser.parse_args()


TAG = args.tag
TICKET = args.ticket
TODAY = datetime.date.today()
BRANCH = "{ticket}.release-{tag}".format(
    ticket=TICKET, tag=TAG
)  # looks like XXXX.release-1.16.0
RELEASE_TITLE = "Release {tag} ({date})".format(tag=TAG, date=TODAY)
RELEASE_FOLDER = "../tahoe-release-{0}".format(TAG)
CONTINUE_INSTRUCTION = (
    bcolors.BOLD
    + "Instruction : run ./venv/bin/python release.py --ignore-deps --tag {tag} --ticket {ticket} --sign {sign} --fin".format(
        tag=TAG, ticket=TICKET, sign="YOUR_SIGNING_KEY_HERE"
    )
    + bcolors.ENDC
)


def clean():
    for release_dir in glob.glob("../tahoe-release-*"):
        shutil.rmtree(release_dir, ignore_errors=True)


def check_dependencies():
    repo_clean = subprocess.run(["git", "status", "-s"], capture_output=True)
    if repo_clean.stdout:
        print(
            f"{bcolors.WARNING}Warning: repo is not clean, please commit changes!{bcolors.ENDC}"
        )
        sys.exit(1)
    try:
        import wheel
    except ModuleNotFoundError:
        print(
            f"{bcolors.WARNING}Warning: wheel is not installed. Install via pip!{bcolors.ENDC}"
        )
        sys.exit(1)


def start_release():
    subprocess.run(
        ["git", "clone", "https://github.com/tahoe-lafs/tahoe-lafs.git", RELEASE_FOLDER]
    )
    os.chdir(RELEASE_FOLDER)
    subprocess.run(["python", "-m", "venv", "venv"])
    subprocess.run(["./venv/bin/pip", "install", "--editable", ".[test]"])

    subprocess.run(["git", "branch", BRANCH])
    subprocess.run(["git", "checkout", BRANCH])
    subprocess.run(["./venv/bin/tox", "-e", "news"])
    subprocess.run(["touch", "newsfragments/{ticket}.minor".format(ticket=TICKET)])
    subprocess.run(["git", "add", "."])
    subprocess.run(
        ["git", "commit", "-s", "-m", "tahoe-lafs-{tag} news".format(tag=TAG)]
    )
    with open("NEWS.rst", "r+") as f:
        content = f.read()
        updated_content = re.sub(
            r"\.\.\stowncrier start line\n(Release\s(\d+\.\d+\.\d)(\.)post\d+\s\(\d{4}-\d{02}-\d{02}\))+(\n\'+)",
            "",
            content,
        )
        f.write(updated_content)
    print(f"{bcolors.OKGREEN}First release step complete.{bcolors.ENDC}")
    print(f"{bcolors.OKBLUE}Instruction: Please review News.rst{bcolors.ENDC}")
    print(
        f'{bcolors.OKBLUE}Instruction: Update "docs/known_issues.rst" (if neccesary){bcolors.ENDC}'
    )
    print(
        f"{bcolors.FAIL}Instruction: Make sure your github username, password and gpg passphrase are ready for the next step."
    )
    print(CONTINUE_INSTRUCTION)


def complete_release():
    os.chdir(RELEASE_FOLDER)
    subprocess.run(["git", "push", "origin", BRANCH])
    SIGNING_KEY = args.sign
    subprocess.run(
        ["git", "tag", "-s", "-u", SIGNING_KEY, "-m", RELEASE_TITLE.lower(), TAG]
    )
    subprocess.run(["./venv/bin/tox", "-e", "py37,codechecks,docs,integration"])
    subprocess.run(["./venv/bin/tox", "-e", "deprecations,upcoming-deprecations"])


if args.clean:
    print(f"{bcolors.OKCYAN}Start cleaning...{bcolors.ENDC}")
    clean()
    print(f"{bcolors.OKGREEN}Cleaning complete...{bcolors.ENDC}")
if args.fin:
    if args.sign is None:
        print("Signing key required to complete release process")
        sys.exit(1)
    complete_release()
    print(f"{bcolors.OKGREEN}INFO: Release procedure complete! :)...{bcolors.ENDC}")
    sys.exit(0)
if not args.ignore_deps:
    check_dependencies()

start_release()
