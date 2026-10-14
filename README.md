# Teams → BibTeX

Paper links posted in a Microsoft Teams channel become BibTeX entries in this
repo. Anyone can then import them into Paperpile, Zotero, EndNote, or any other
citation manager.

```
Teams post ──► Power Automate ──► GitHub issue "Paper: …" ──► GitHub Action
                                                               │
                         refs/{FirstAuthor}-{Year}-{Journal}-{Keyword}.bib
                         library.bib  (all entries, rebuilt every run)
                         comment on the issue, then close it
```

## Naming

Each paper gets its own file, and the citekey inside matches the file name:

```
refs/VanDerBerg-2023-NatCommun-TP53.bib

@article{VanDerBerg-2023-NatCommun-TP53,
	title = {...},
	...
}
```

| Part | Source | Examples |
|---|---|---|
| FirstAuthor | Family name of the first author, accents and spaces removed | `GarciaLopez`, `VanDerBerg`, `Bat1K` (consortium) |
| Year | Print date, else online date, else posted date | `2023` |
| ShortJournal | Crossref's short title with dots and spaces removed; else the one-word title; else initials | `NatCommun`, `Nature`, `PNAS`, `bioRxiv`, `arXiv` |
| Keyword | First word in the title that is 4+ letters and not a stopword | `TP53`, `Chromatin`, `Myotis` |

If two papers produce the same key, the second gets a letter suffix
(`…-Chromatinb`). A paper whose DOI is already in `refs/` is skipped and
reported as a duplicate.

## What links work

- DOIs, typed bare or as `doi.org` links
- Any URL with a DOI in it: bioRxiv, medRxiv, Wiley, Springer, PNAS, Science, Cell Press `doi/` links, and so on
- `nature.com/articles/…`, `arxiv.org/abs/…`, `elifesciences.org/articles/…`
- PubMed and PMC links (looked up through NCBI)
- Other publisher pages: the script reads the DOI from the page's metadata.
  Some publishers block automated requests, so these can fail. The issue
  comment says so, and posting the DOI fixes it.

## Setup

### 1. Create the GitHub repo

1. Create a new repo (private is fine) and push this folder to it.
2. Settings → Actions → General → Workflow permissions: choose
   **Read and write permissions**.
3. Optional: Settings → Secrets and variables → Actions → Variables → add
   `CROSSREF_MAILTO` with a lab email address. Crossref gives faster, more
   reliable service to requests that identify a contact.

### 2. Build the Power Automate flow

At [make.powerautomate.com](https://make.powerautomate.com), create an
**Automated cloud flow**.

1. **Trigger:** Microsoft Teams → *When a new channel message is added*.
   Pick the team and channel. This fires on new posts, not on replies.
2. **Condition** (optional but recommended): *Message body content*
   **contains** `http` **or** contains `10.`. This skips posts with no links.
3. **If yes:** GitHub → *Create an issue*. Sign in with a GitHub account that
   can write to the repo (a shared lab account works well).
   - Repository Owner: your GitHub user or org
   - Repository Name: this repo
   - **Title:** `Paper: ` followed by *Message subject*, or a fixed text such
     as `Paper: posted in Teams`. **The title must start with `Paper:`.**
     The Action ignores any other issue.
   - **Body:** *Message body content*. Adding the poster's name helps keep a
     record (`From:` *User display name*).

Both connectors are standard tier, so a regular Microsoft 365 licence is
enough.

### 3. Test

Post a DOI in the channel. Within a minute or two you should see a new issue,
a commit adding `refs/…bib`, and a comment listing what was added. Issues
where something failed stay open with a `needs-attention` label.

## Adding papers by hand

- **From GitHub:** Actions → *Add paper from Teams* → *Run workflow*, and
  paste DOIs or links.
- **Locally:** `python scripts/add_refs.py 10.1038/s41586-018-0279-9 https://…`
- **Preview keys without writing:** add `--dry-run`.
- **After deleting or editing files in `refs/`:** run
  `python scripts/add_refs.py --rebuild` to regenerate `library.bib`.

## Importing into a citation manager

- **Everything:** download `library.bib` (open it on GitHub, then *Download
  raw file*).
  - Paperpile: Add Papers → Upload Files
  - Zotero: File → Import
  - EndNote: File → Import → BibTeX
- **Only new papers:** download the individual files from `refs/`, or check
  the repo's commit history to see what was added since your last import.

Re-importing the whole `library.bib` can create duplicates. Paperpile flags
them for merging; in Zotero, use the *Duplicate Items* pane. Importing only
the new files from `refs/` avoids this.

## Tests

```
python -m unittest discover tests
```

The tests run offline against saved metadata.
