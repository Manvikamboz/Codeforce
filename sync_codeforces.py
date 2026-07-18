#!/usr/bin/env python3
"""
Codeforces Submission Syncer (Cloud / GitHub Actions Edition)
--------------------------------------------------------------
Reads CF_HANDLE from GitHub Repository Secrets via environment variables.
Fetches accepted submissions via the Codeforces API, downloads source code,
and writes a clean submission table to README.md.
Git commit & push are handled by the GitHub Actions workflow, NOT this script.
"""


import os
import sys
import re
import time
from datetime import datetime

import requests
from bs4 import BeautifulSoup
from colorama import init, Fore, Style

init(autoreset=True)

DEFAULT_SOLUTIONS_DIR = "solutions"
README_FILE = "README.md"

LANGUAGE_EXTENSIONS = {
    'cpp': ['c++', 'clang++', 'g++', 'gnu c++', 'msc++'],
    'c': ['gcc', 'gnu c', 'clang c'],
    'py': ['python', 'pypy'],
    'java': ['java'],
    'kt': ['kotlin'],
    'rs': ['rust'],
    'go': ['go'],
    'cs': ['c#', 'mono c#', '.net'],
    'js': ['javascript', 'node.js', 'node'],
    'ts': ['typescript'],
    'hs': ['haskell'],
    'rb': ['ruby'],
    'scala': ['scala'],
    'php': ['php'],
    'pl': ['perl'],
    'pas': ['pascal', 'delphi', 'fpc'],
    'ml': ['ocaml'],
    'd': ['d'],
    'swift': ['swift'],
}

def get_extension(lang_str):
    """Return the file extension for a given Codeforces language string."""
    lang_lower = lang_str.lower()
    for ext, keywords in LANGUAGE_EXTENSIONS.items():
        if any(k in lang_lower for k in keywords):
            return f".{ext}"
    return ".txt"

def sanitize_filename(name):
    """Strip characters that are invalid in filenames."""
    cleaned = re.sub(r'[\\/*?:"<>|]', '', name)
    return re.sub(r'\s+', ' ', cleaned).strip()

def load_config():
    """Load configuration exclusively from environment variables (GitHub Secrets)."""
    handle = os.environ.get("CF_HANDLE", "").strip()
    if not handle:
        print(f"{Fore.RED}ERROR: CF_HANDLE environment variable is not set.{Style.RESET_ALL}")
        print("Add CF_HANDLE as a GitHub Repository Secret and re-run the workflow.")
        sys.exit(1)

    return {
        "cf_handle": handle,
        "solutions_dir": os.environ.get("CF_SOLUTIONS_DIR", DEFAULT_SOLUTIONS_DIR),
    }

def fetch_cf_user_info(session, handle):
    """Fetch basic user profile from the Codeforces API."""
    url = f"https://codeforces.com/api/user.info?handles={handle}"
    try:
        r = session.get(url, timeout=10)
        data = r.json()
        if data.get("status") == "OK":
            return data["result"][0]
    except Exception as e:
        print(f"{Fore.YELLOW}Warning: Could not fetch user info: {e}{Style.RESET_ALL}")
    return None

def fetch_cf_submissions(session, handle):
    """Fetch up to 10 000 submissions for the given handle via the Codeforces API."""
    print(f"{Fore.CYAN}Fetching submissions for handle: {Fore.YELLOW}{handle}{Style.RESET_ALL}")
    url = f"https://codeforces.com/api/user.status?handle={handle}&from=1&count=10000"
    try:
        r = session.get(url, timeout=20)
        data = r.json()
        if data.get("status") != "OK":
            print(f"{Fore.RED}API error: {data.get('comment', 'unknown')}{Style.RESET_ALL}")
            sys.exit(1)
        subs = data["result"]
        print(f"Fetched {Fore.GREEN}{len(subs)}{Style.RESET_ALL} total submissions.")
        return subs
    except Exception as e:
        print(f"{Fore.RED}Error fetching submissions: {e}{Style.RESET_ALL}")
        sys.exit(1)

def get_submission_code(session, contest_id, submission_id):
    """Scrape the source code of a submission from its Codeforces page."""
    is_gym = contest_id >= 100000
    if is_gym:
        url = f"https://codeforces.com/gym/{contest_id}/submission/{submission_id}"
    else:
        url = f"https://codeforces.com/contest/{contest_id}/submission/{submission_id}"

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
        )
    }
    time.sleep(2)  # Respect Codeforces rate limits
    try:
        r = session.get(url, headers=headers, timeout=20)
        if r.status_code != 200:
            print(f"{Fore.RED}HTTP {r.status_code} for submission {submission_id}{Style.RESET_ALL}")
            return None
        soup = BeautifulSoup(r.text, "html.parser")
        el = soup.find("pre", id="program-source-text") or soup.find("pre", class_="program-source-text")
        if el:
            return el.get_text()
        print(f"{Fore.RED}Source not found for {submission_id} — submission may be private.{Style.RESET_ALL}")
        return None
    except Exception as e:
        print(f"{Fore.RED}Error scraping {submission_id}: {e}{Style.RESET_ALL}")
        return None

RANK_COLOR = {
    "newbie":                 "808080",
    "pupil":                  "008000",
    "specialist":             "03a89e",
    "expert":                 "0000ff",
    "candidate master":       "aa00aa",
    "master":                 "ff8c00",
    "international master":   "ff8c00",
    "grandmaster":            "ff0000",
    "international grandmaster": "ff0000",
    "legendary grandmaster":  "ff0000",
}

def diff_label(rating):
    """Return a plain-text difficulty label for a given rating."""
    if not isinstance(rating, int):
        return "-"
    if rating < 1200: return "Easy"
    if rating < 1600: return "Medium"
    if rating < 2100: return "Hard"
    return "Expert"

def update_readme(solved_list, user_info=None):
    """Generate a beautiful README.md with profile header, stats, and submission table."""
    print(f"\n{Fore.CYAN}=== Updating README.md ==={Style.RESET_ALL}")

    solved_list.sort(key=lambda x: x["creationTimeSeconds"], reverse=True)
    total = len(solved_list)

    # ── Rating / tag frequency ────────────────────────────────────────────────
    rating_counts: dict[int, int] = {}
    tag_counts:    dict[str, int] = {}
    for sub in solved_list:
        p = sub["problem"]
        r = p.get("rating")
        if isinstance(r, int):
            rating_counts[r] = rating_counts.get(r, 0) + 1
        for t in p.get("tags", []):
            tag_counts[t] = tag_counts.get(t, 0) + 1

    # ── Build README ─────────────────────────────────────────────────────────
    out: list[str] = []

    # --- Header / profile card ------------------------------------------------
    if user_info:
        handle   = user_info.get("handle", "")
        rank     = user_info.get("rank", "unrated")
        rating   = user_info.get("rating", 0)
        max_rank = user_info.get("maxRank", "unrated")
        max_rat  = user_info.get("maxRating", 0)
        avatar   = user_info.get("titlePhoto") or user_info.get("avatar", "")
        color    = RANK_COLOR.get(rank.lower(), "lightgrey")
        rank_lbl = rank.replace(" ", "%20").title()

        out.append('<div align="center">\n\n')
        if avatar:
            out.append(f'<img src="{avatar}" width="110" style="border-radius:50%;border:3px solid #{color};" />\n\n')
        out.append(f'# {handle} — Codeforces Solutions\n\n')
        out.append(
            f'[![Rank](https://img.shields.io/badge/Rank-{rank_lbl}-{color}?style=for-the-badge&logo=codeforces&logoColor=white)]'
            f'(https://codeforces.com/profile/{handle}) '
            f'[![Rating](https://img.shields.io/badge/Rating-{rating}-{color}?style=for-the-badge)]'
            f'(https://codeforces.com/profile/{handle}) '
            f'[![Max Rating](https://img.shields.io/badge/Max%20Rating-{max_rat}-{color}?style=for-the-badge)]'
            f'(https://codeforces.com/profile/{handle}) '
            f'[![Solved](https://img.shields.io/badge/Solved-{total}-brightgreen?style=for-the-badge)]'
            f'(https://codeforces.com/profile/{handle})\n\n'
        )
        out.append(f'> Max rank achieved: **{max_rank.title()}** ({max_rat})\n\n')
        out.append('</div>\n\n')
        out.append('---\n\n')
    else:
        out.append('# Codeforces Solutions\n\n')
        out.append(
            f'[![Solved](https://img.shields.io/badge/Solved-{total}-brightgreen?style=for-the-badge)]'
            f'(https://codeforces.com)\n\n'
        )
        out.append('---\n\n')

    # --- Stats ----------------------------------------------------------------
    out.append('## Stats\n\n')

    # Difficulty bar chart
    if rating_counts:
        out.append('<details>\n<summary><b>Difficulty Distribution</b></summary>\n\n')
        out.append('```\n')
        max_c = max(rating_counts.values())
        for r in sorted(rating_counts):
            c   = rating_counts[r]
            bar = "#" * round(c / max_c * 25)
            out.append(f"  {diff_label(r):6s} {r:4d}  {bar:<25}  {c}\n")
        out.append('```\n\n</details>\n\n')

    # Top tags row
    if tag_counts:
        top = sorted(tag_counts.items(), key=lambda x: x[1], reverse=True)[:12]
        badges = " ".join(
            f'`{t} ×{c}`'
            for t, c in top
        )
        out.append(f'**Top Topics:** {badges}\n\n')

    out.append('---\n\n')

    # --- Submission table -----------------------------------------------------
    out.append('## Accepted Submissions\n\n')
    out.append('| # | Problem | Difficulty | Tags | Language | Date |\n')
    out.append('|:-:|---------|:----------:|------|----------|:----:|\n')

    for idx, sub in enumerate(solved_list, 1):
        prob       = sub["problem"]
        contest_id = sub["contestId"]
        prob_idx   = prob["index"]
        prob_name  = prob["name"]
        rating_val = prob.get("rating")
        tags_str   = " • ".join(f"`{t}`" for t in prob.get("tags", []))
        lang       = sub["programmingLanguage"]
        date       = datetime.fromtimestamp(sub["creationTimeSeconds"]).strftime("%b %d, %Y")

        diff_str = f'{rating_val} ({diff_label(rating_val)})' if isinstance(rating_val, int) else "N/A"

        is_gym   = contest_id >= 100000
        base_url = "https://codeforces.com/gym" if is_gym else "https://codeforces.com/contest"
        prob_url  = f"{base_url}/{contest_id}/problem/{prob_idx}"
        prob_link = f"**[{prob_name}]({prob_url})**"

        out.append(f"| {idx} | {prob_link} | {diff_str} | {tags_str} | {lang} | {date} |\n")

    out.append('\n---\n\n')
    out.append(
        '<div align="center">\n\n'
        '*Auto-synced daily via [GitHub Actions](../../actions) · '
        'Powered by the [Codeforces API](https://codeforces.com/apiHelp)*\n\n'
        '</div>\n'
    )

    with open(README_FILE, "w", encoding="utf-8") as f:
        f.writelines(out)

    print(f"{Fore.GREEN}README.md written ({total} problems).{Style.RESET_ALL}")



def main():
    config       = load_config()
    handle       = config["cf_handle"]
    solutions_dir = config["solutions_dir"]

    os.makedirs(solutions_dir, exist_ok=True)

    print(f"\n{Fore.CYAN}=== Codeforces Sync (GitHub Actions) ==={Style.RESET_ALL}")
    print(f"Handle : {Fore.YELLOW}{handle}{Style.RESET_ALL}")

    session = requests.Session()

    user_info   = fetch_cf_user_info(session, handle)
    submissions = fetch_cf_submissions(session, handle)

    # Keep only accepted submissions
    ok_subs = [s for s in submissions if s.get("verdict") == "OK"]
    print(f"Accepted submissions : {Fore.GREEN}{len(ok_subs)}{Style.RESET_ALL}")

    # Deduplicate — one entry per (contestId, problemIndex), newest first
    seen, unique = {}, []
    for sub in ok_subs:
        cid = sub.get("contestId")
        if not cid:
            continue
        key = (cid, sub["problem"]["index"])
        if key not in seen:
            seen[key] = True
            unique.append(sub)

    # Process oldest first so we don't re-download on subsequent runs
    unique.sort(key=lambda x: x["creationTimeSeconds"])
    print(f"Unique solved problems: {Fore.GREEN}{len(unique)}{Style.RESET_ALL}")

    for sub in unique:
        prob       = sub["problem"]
        contest_id = sub["contestId"]
        prob_idx   = prob["index"]
        prob_name  = prob["name"]
        sub_id     = sub["id"]
        lang       = sub["programmingLanguage"]

        contest_dir = os.path.join(solutions_dir, str(contest_id))
        os.makedirs(contest_dir, exist_ok=True)

        filename = f"{prob_idx}_{sanitize_filename(prob_name)}{get_extension(lang)}"
        filepath = os.path.join(contest_dir, filename)

        if os.path.exists(filepath):
            continue  # Already downloaded in a previous run

        print(f"Downloading {contest_id}{prob_idx} — {prob_name} …")
        code = get_submission_code(session, contest_id, sub_id)
        if code:
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(code)
            print(f"  {Fore.GREEN}Saved → {filepath}{Style.RESET_ALL}")
        else:
            print(f"  {Fore.RED}Skipped (code unavailable){Style.RESET_ALL}")

    # Always regenerate README so the table reflects the current state
    update_readme(unique, user_info)


if __name__ == "__main__":
    main()
