#!/usr/bin/env python3
"""Turn paper links posted in a Teams channel into BibTeX files.

Reads text (a GitHub issue body created by Power Automate, or text passed on
the command line), finds DOIs and paper URLs, fetches metadata from doi.org,
and writes one BibTeX file per paper to refs/:

    refs/{FirstAuthor}-{Year}-{ShortJournal}-{Keyword}.bib

The citekey inside each file uses the same pattern. library.bib is rebuilt
from refs/ on every run.

Only the Python standard library is used, so the GitHub Action needs no
dependency install.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REFS_DIR = ROOT / "refs"
LIBRARY = ROOT / "library.bib"

MAILTO = os.environ.get("CROSSREF_MAILTO", "")
USER_AGENT = "teams-to-bibtex/1.0 (+https://github.com)" + (
    f" mailto:{MAILTO}" if MAILTO else ""
)
TIMEOUT = 20

# --------------------------------------------------------------------------
# Finding identifiers in message text
# --------------------------------------------------------------------------

DOI_RE = re.compile(r"\b(10\.\d{4,9}/[^\s\"'<>]+)", re.IGNORECASE)
URL_RE = re.compile(r"https?://[^\s\"'<>\]\)]+", re.IGNORECASE)
TRAILING_PUNCT = ".,;:)]}>'\""

# Hosts that are never papers (Teams/Office plumbing, images, etc.).
IGNORED_HOSTS = (
    "teams.microsoft.com",
    "statics.teams.cdn.office.net",
    "graph.microsoft.com",
    "sharepoint.com",
    "outlook.office.com",
    "github.com",
)


def clean_doi(doi: str) -> str:
    doi = urllib.parse.unquote(doi).strip()
    doi = doi.rstrip(TRAILING_PUNCT)
    # Strip common suffixes publishers append to DOI-style paths.
    doi = re.sub(r"(\.full|\.abstract|\.full\.pdf|\.pdf|/full|/abstract|/pdf)$", "", doi, flags=re.I)
    doi = re.sub(r"v\d+$", "", doi) if doi.lower().startswith("10.1101/") else doi
    return doi.lower()


def extract_candidates(text: str) -> tuple[list[str], list[str]]:
    """Return (dois, urls_without_doi) found in text. Order preserved, unique."""
    text = html.unescape(text)
    dois: list[str] = []
    urls: list[str] = []

    for raw in URL_RE.findall(text):
        url = raw.rstrip(TRAILING_PUNCT)
        host = urllib.parse.urlparse(url).netloc.lower()
        if any(host.endswith(h) for h in IGNORED_HOSTS):
            continue
        doi = doi_from_url(url)
        if doi:
            if doi not in dois:
                dois.append(doi)
        elif url not in urls:
            urls.append(url)

    # Bare DOIs typed into the message (not inside a URL we already handled).
    for raw in DOI_RE.findall(URL_RE.sub(" ", text)):
        doi = clean_doi(raw)
        if doi not in dois:
            dois.append(doi)

    return dois, urls


def doi_from_url(url: str) -> str | None:
    """Get a DOI from a URL without any network call, when the URL encodes one."""
    parsed = urllib.parse.urlparse(url)
    host = parsed.netloc.lower()
    path = urllib.parse.unquote(parsed.path)

    m = DOI_RE.search(path) or DOI_RE.search(urllib.parse.unquote(parsed.query))
    if m:
        return clean_doi(m.group(1))

    # nature.com/articles/s41586-024-01234-5 -> 10.1038/s41586-024-01234-5
    m = re.match(r"/articles/([a-z0-9][\w.-]+)", path)
    if host.endswith("nature.com") and m:
        return clean_doi("10.1038/" + m.group(1))

    # arxiv.org/abs/2401.01234 -> 10.48550/arXiv.2401.01234
    m = re.match(r"/(?:abs|pdf)/(\d{4}\.\d{4,5})", path)
    if host.endswith("arxiv.org") and m:
        return "10.48550/arxiv." + m.group(1)

    # elifesciences.org/articles/12345 -> 10.7554/eLife.12345
    m = re.match(r"/(?:articles|reviewed-preprints)/(\d+)", path)
    if host.endswith("elifesciences.org") and m:
        return "10.7554/elife." + m.group(1)

    return None


# --------------------------------------------------------------------------
# Network helpers
# --------------------------------------------------------------------------


def http_get(url: str, accept: str = "*/*") -> tuple[str, str]:
    """GET url, return (final_url, body text). Retries once on 429/5xx."""
    req = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": USER_AGENT})
    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                charset = resp.headers.get_content_charset() or "utf-8"
                return resp.geturl(), resp.read().decode(charset, errors="replace")
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt == 0:
                time.sleep(3)
                continue
            raise
    raise RuntimeError("unreachable")


def doi_from_pubmed(url: str) -> str | None:
    m = re.search(r"pubmed\.ncbi\.nlm\.nih\.gov/(\d+)", url) or re.search(
        r"ncbi\.nlm\.nih\.gov/pmc/articles/(PMC\d+)", url, re.I
    ) or re.search(r"pmc\.ncbi\.nlm\.nih\.gov/articles/(PMC\d+)", url, re.I)
    if not m:
        return None
    ident = m.group(1)
    api = (
        "https://www.ncbi.nlm.nih.gov/pmc/utils/idconv/v1.0/?format=json&ids="
        + urllib.parse.quote(ident)
    )
    _, body = http_get(api, "application/json")
    for rec in json.loads(body).get("records", []):
        if rec.get("doi"):
            return clean_doi(rec["doi"])
    return None


META_DOI_RE = re.compile(
    r"<meta[^>]+name=[\"'](?:citation_doi|dc\.identifier|DC\.Identifier|prism\.doi|bepress_citation_doi)[\"'][^>]*>",
    re.I,
)


def doi_from_page(url: str) -> str | None:
    """Fetch the landing page and read the DOI from its meta tags."""
    final_url, body = http_get(url, "text/html")
    doi = doi_from_url(final_url)
    if doi:
        return doi
    for tag in META_DOI_RE.findall(body):
        m = re.search(r"content=[\"']([^\"']+)", tag, re.I)
        if m:
            d = DOI_RE.search(m.group(1))
            if d:
                return clean_doi(d.group(1))
    return None


def resolve_url(url: str) -> str | None:
    try:
        return doi_from_pubmed(url) or doi_from_page(url)
    except Exception as e:  # noqa: BLE001 - report and move on
        print(f"  could not resolve {url}: {e}", file=sys.stderr)
        return None


def fetch_csl(doi: str) -> dict:
    _, body = http_get(
        "https://doi.org/" + urllib.parse.quote(doi, safe="/"),
        "application/vnd.citationstyles.csl+json",
    )
    return json.loads(body)


def fetch_bibtex(doi: str) -> str:
    _, body = http_get(
        "https://doi.org/" + urllib.parse.quote(doi, safe="/"), "application/x-bibtex"
    )
    return body.strip()


# --------------------------------------------------------------------------
# Citekey: {FirstAuthor}-{Year}-{ShortJournal}-{Keyword}
# --------------------------------------------------------------------------

STOPWORDS = set(
    """a an and are as at be by but can do does for from has have how in into is it
    its of on or our over than that the their them these this those through to
    toward towards under using use via vs versus what when where which while who why
    with within without new novel study studies analysis analyses evidence role
    roles effect effects based reveal reveals revealed identify identifies
    identification across between among during after before show shows insight
    insights""".split()
)

# Words dropped when abbreviating a journal title to initials.
JOURNAL_STOP = {"of", "the", "and", "in", "for", "on", "a", "an", "&", "de", "la"}

PREPRINT_SERVERS = {
    "10.1101": None,  # bioRxiv or medRxiv; decided from metadata below
    "10.48550": "arXiv",
    "10.21203": "ResearchSquare",
    "10.20944": "Preprints",
    "10.31219": "OSF",
    "10.2139": "SSRN",
}


def ascii_alnum(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9]", "", s)


def camel(s: str) -> str:
    """'García-López' -> 'GarciaLopez', 'van der Berg' -> 'VanDerBerg'."""
    parts = re.split(r"[\s\-']+", s)
    return "".join(ascii_alnum(p)[:1].upper() + ascii_alnum(p)[1:] for p in parts if ascii_alnum(p))


def first_author(csl: dict) -> str:
    for role in ("author", "editor"):
        people = csl.get(role) or []
        if people:
            p = people[0]
            name = p.get("family") or p.get("literal") or p.get("name") or ""
            if p.get("non-dropping-particle"):
                name = p["non-dropping-particle"] + " " + name
            if p.get("literal") and not p.get("family"):
                name = name.split()[0]  # consortium: first word only
            out = camel(name)
            if out:
                return out
    return "Anon"


def year(csl: dict) -> str:
    for k in ("published-print", "published-online", "issued", "posted", "created"):
        parts = (csl.get(k) or {}).get("date-parts") or []
        if parts and parts[0] and parts[0][0]:
            return str(parts[0][0])
    return "nd"


def _first(v) -> str:
    if isinstance(v, list):
        return v[0] if v else ""
    return v or ""


def short_journal(csl: dict, doi: str) -> str:
    prefix = doi.split("/")[0]
    if prefix in PREPRINT_SERVERS:
        if PREPRINT_SERVERS[prefix]:
            return PREPRINT_SERVERS[prefix]
        blob = json.dumps(csl).lower()
        return "medRxiv" if "medrxiv" in blob else "bioRxiv"

    short = _first(csl.get("container-title-short")) or _first(csl.get("short-container-title"))
    if short:
        out = "".join(w[:1].upper() + w[1:] for w in (ascii_alnum(x) for x in short.split()) if w)
        if out:
            return out

    full = _first(csl.get("container-title"))
    if not full:
        if csl.get("type") in ("posted-content", "article"):
            inst = _first([i.get("name") for i in csl.get("institution", []) if i.get("name")])
            return ascii_alnum(inst) or "Preprint"
        return ascii_alnum(_first(csl.get("publisher")).split(" ")[0]) or "NA"

    words = [w for w in re.split(r"[\s:,/]+", full) if w]
    if len(words) == 1:
        return ascii_alnum(words[0])  # Nature, Cell, eLife, Science
    initials = "".join(ascii_alnum(w)[:1].upper() for w in words if w.lower() not in JOURNAL_STOP)
    return initials or "NA"


def keyword(csl: dict) -> str:
    title = html.unescape(re.sub(r"<[^>]+>", "", _first(csl.get("title"))))
    for w in re.findall(r"[A-Za-zÀ-ɏ][\wÀ-ɏ-]*", title):
        token = ascii_alnum(w)
        if len(token) >= 4 and token.lower() not in STOPWORDS:
            return token[:1].upper() + token[1:]
    return "Untitled"


def make_key(csl: dict, doi: str) -> str:
    return "-".join([first_author(csl), year(csl), short_journal(csl, doi), keyword(csl)])


# --------------------------------------------------------------------------
# Writing files
# --------------------------------------------------------------------------

BIB_DOI_RE = re.compile(r"\bdoi\s*=\s*[{\"]\s*([^}\"]+?)\s*[}\"]", re.I)


def existing_index() -> tuple[dict[str, str], set[str]]:
    """Map DOI -> key for files already in refs/, plus the set of used keys."""
    by_doi: dict[str, str] = {}
    keys: set[str] = set()
    for f in REFS_DIR.glob("*.bib"):
        keys.add(f.stem)
        m = BIB_DOI_RE.search(f.read_text(encoding="utf-8"))
        if m:
            by_doi[clean_doi(m.group(1))] = f.stem
    return by_doi, keys


def unique_key(base: str, used: set[str]) -> str:
    if base not in used:
        return base
    for suffix in "bcdefghijklmnopqrstuvwxyz":
        if base + suffix not in used:
            return base + suffix
    raise RuntimeError(f"too many collisions for {base}")


def set_citekey(bibtex: str, key: str, doi: str) -> str:
    out, n = re.subn(r"^(\s*@\w+\s*\{)\s*[^,\s]*\s*,", rf"\g<1>{key},", bibtex, count=1)
    if n == 0:
        raise ValueError("doi.org did not return a BibTeX entry")
    if not BIB_DOI_RE.search(out):
        out = re.sub(r"\}\s*$", f",\n\tdoi = {{{doi}}}\n}}", out)
    # Pretty-print the single-line entries doi.org returns.
    if "\n" not in out.strip():
        out = re.sub(r",\s*(\w+)\s*=", r",\n\t\1 = ", out)
        out = re.sub(r"\s*\}\s*$", "\n}", out)
    return out.strip() + "\n"


def rebuild_library() -> int:
    files = sorted(REFS_DIR.glob("*.bib"), key=lambda p: p.stem.lower())
    header = "% Generated from refs/*.bib. Do not edit by hand.\n\n"
    LIBRARY.write_text(header + "\n".join(f.read_text(encoding="utf-8") for f in files), encoding="utf-8")
    return len(files)


@dataclass
class Result:
    added: list[tuple[str, str]] = field(default_factory=list)  # (key, doi)
    duplicates: list[tuple[str, str]] = field(default_factory=list)  # (key, doi)
    failed: list[tuple[str, str]] = field(default_factory=list)  # (input, reason)


def process(text: str, dry_run: bool = False) -> Result:
    REFS_DIR.mkdir(exist_ok=True)
    res = Result()
    dois, urls = extract_candidates(text)

    for url in urls:
        doi = resolve_url(url)
        if doi:
            if doi not in dois:
                dois.append(doi)
        else:
            res.failed.append((url, "no DOI found on the page"))

    by_doi, used = existing_index()
    for doi in dois:
        if doi in by_doi:
            res.duplicates.append((by_doi[doi], doi))
            continue
        try:
            csl = fetch_csl(doi)
            key = unique_key(make_key(csl, doi), used)
            bib = set_citekey(fetch_bibtex(doi), key, doi)
        except Exception as e:  # noqa: BLE001
            res.failed.append((doi, str(e)))
            continue
        used.add(key)
        by_doi[doi] = key
        if not dry_run:
            (REFS_DIR / f"{key}.bib").write_text(bib, encoding="utf-8")
        res.added.append((key, doi))
        time.sleep(0.5)  # be polite to doi.org

    if not dry_run:
        rebuild_library()
    return res


def summary_markdown(res: Result) -> str:
    lines = []
    if res.added:
        lines.append("**Added**")
        lines += [f"- `{k}` — https://doi.org/{d}" for k, d in res.added]
    if res.duplicates:
        lines.append("\n**Already in the library**")
        lines += [f"- `{k}` — https://doi.org/{d}" for k, d in res.duplicates]
    if res.failed:
        lines.append("\n**Could not add** (post the DOI instead, or add it by hand)")
        lines += [f"- {i} — {r}" for i, r in res.failed]
    if not lines:
        lines.append("No DOIs or paper links found in this message.")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("text", nargs="*", help="DOIs/URLs/text. Default: the GitHub issue body.")
    ap.add_argument("--dry-run", action="store_true", help="Print keys without writing files.")
    ap.add_argument("--rebuild", action="store_true", help="Only rebuild library.bib.")
    args = ap.parse_args()

    if args.rebuild:
        print(f"library.bib rebuilt with {rebuild_library()} entries")
        return 0

    if args.text:
        text = " ".join(args.text)
    elif os.environ.get("GITHUB_EVENT_PATH"):
        event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
        issue = event.get("issue") or {}
        text = (issue.get("title") or "") + "\n" + (issue.get("body") or "")
        text += "\n" + ((event.get("inputs") or {}).get("text") or "")
    else:
        text = sys.stdin.read()

    res = process(text, dry_run=args.dry_run)
    md = summary_markdown(res)
    print(md)

    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as fh:
            fh.write(f"added={len(res.added)}\n")
            fh.write(f"failed={len(res.failed)}\n")
            fh.write("summary<<__EOF__\n" + md + "__EOF__\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
