#!/usr/bin/env python3
"""
Generate a year-grouped Quarto publication list from all BibTeX files in
publications/bibliography/.

Features
--------
- Reads every *.bib file in the bibliography directory.
- Deduplicates publications across files.
- Groups publications by year, newest first.
- Formats common BibTeX entry types.
- Adds DOI / URL / PDF / preprint links when present.
- Adds collapsible abstracts when present.
- Automatically highlights lab members using metadata from people/*.qmd.
- Supports publication-name aliases in people/*.qmd for robust author matching.

Dependencies
------------
    pip install "bibtexparser>=1.4,<2" "PyYAML>=6"

Typical Quarto integration
--------------------------
In _quarto.yml:

project:
  type: website
  pre-render:
    - python scripts/generate_publications.py
"""

from __future__ import annotations

import argparse
import html
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any, Iterable

try:
    import bibtexparser
    from bibtexparser.bparser import BibTexParser
    from bibtexparser.customization import convert_to_unicode
except ImportError as exc:
    raise SystemExit(
        'Missing dependency "bibtexparser". Install with:\n'
        '  pip install "bibtexparser>=1.4,<2" "PyYAML>=6"'
    ) from exc

try:
    import yaml
except ImportError as exc:
    raise SystemExit(
        'Missing dependency "PyYAML". Install with:\n'
        '  pip install "bibtexparser>=1.4,<2" "PyYAML>=6"'
    ) from exc


MONTHS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}

USEFUL_FIELDS = (
    "doi",
    "url",
    "pdf",
    "file",
    "preprint",
    "abstract",
    "journal",
    "booktitle",
    "volume",
    "number",
    "pages",
    "publisher",
    "month",
)


def clean_text(value: Any) -> str:
    """Normalize a BibTeX field for display."""
    if value is None:
        return ""
    text = str(value).strip()

    # BibTeX braces are often used only to protect capitalization.
    text = text.replace("{", "").replace("}", "")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def strip_outer_braces(value: str) -> str:
    value = value.strip()
    while len(value) >= 2 and value.startswith("{") and value.endswith("}"):
        value = value[1:-1].strip()
    return value


def split_bibtex_authors(author_field: str) -> list[str]:
    """
    Split a BibTeX author field on top-level ' and ' separators.

    This avoids splitting text inside braces.
    """
    text = author_field.strip()
    if not text:
        return []

    authors: list[str] = []
    buf: list[str] = []
    depth = 0
    i = 0

    while i < len(text):
        char = text[i]

        if char == "{":
            depth += 1
            buf.append(char)
            i += 1
            continue

        if char == "}":
            depth = max(0, depth - 1)
            buf.append(char)
            i += 1
            continue

        if depth == 0 and text[i : i + 5].lower() == " and ":
            authors.append("".join(buf).strip())
            buf = []
            i += 5
            continue

        buf.append(char)
        i += 1

    if buf:
        authors.append("".join(buf).strip())

    return [a for a in authors if a]


def parse_person_name(name: str) -> tuple[str, str, str]:
    """
    Return (given, family, suffix) from a reasonably conventional person name.

    Supports:
      Last, First
      Last, Jr, First
      First Middle Last

    This is intentionally conservative; publication-name aliases can be supplied
    in people/*.qmd for unusual or ambiguous names.
    """
    raw = strip_outer_braces(clean_text(name))
    if not raw:
        return "", "", ""

    if "," in raw:
        parts = [p.strip() for p in raw.split(",")]
        family = parts[0]
        if len(parts) == 2:
            given = parts[1]
            suffix = ""
        else:
            suffix = parts[1]
            given = parts[-1]
        return given, family, suffix

    tokens = raw.split()
    if len(tokens) == 1:
        return "", tokens[0], ""

    return " ".join(tokens[:-1]), tokens[-1], ""


def display_author_name(name: str) -> str:
    """Convert 'Last, First' to 'First Last' while preserving simple literals."""
    raw = strip_outer_braces(clean_text(name))
    if not raw:
        return ""

    given, family, suffix = parse_person_name(raw)

    if not given:
        return family

    result = f"{given} {family}".strip()
    if suffix:
        result = f"{result}, {suffix}"
    return result


def normalize_name_piece(text: str) -> str:
    """Lowercase, remove accents and punctuation for author matching."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]", "", text.lower())


def name_signature(name: str) -> tuple[str, str]:
    """Return normalized (family_name, given_name) signature."""
    given, family, _ = parse_person_name(name)
    return normalize_name_piece(family), normalize_name_piece(given)


def names_match(author: str, alias: str) -> bool:
    """
    Match a BibTeX author to a lab-member alias.

    Family names must match exactly after normalization. Given names match if:
    - the normalized given names are equal, or
    - one side is an initial and the first initials agree.

    This handles combinations such as:
      Holland-Moritz, Hannah
      Holland-Moritz, H.
      Hannah Holland-Moritz
    """
    a_family, a_given = name_signature(author)
    b_family, b_given = name_signature(alias)

    if not a_family or a_family != b_family:
        return False

    if not a_given or not b_given:
        return False

    if a_given == b_given:
        return True

    return a_given[0] == b_given[0] and (len(a_given) == 1 or len(b_given) == 1)


def read_front_matter(path: Path) -> dict[str, Any]:
    """Read YAML front matter from a .qmd file."""
    text = path.read_text(encoding="utf-8")

    match = re.match(r"^\s*---\s*\n(.*?)\n---\s*(?:\n|$)", text, flags=re.DOTALL)
    if not match:
        return {}

    data = yaml.safe_load(match.group(1))
    return data if isinstance(data, dict) else {}


def load_lab_member_aliases(people_dir: Path) -> dict[str, list[str]]:
    """
    Read people/*.qmd and return {display_name: [publication aliases]}.

    By default every person is highlighted. To opt someone out:
        highlight-publications: false

    Optional aliases:
        publication-names:
          - "Holland-Moritz, Hannah"
          - "Holland-Moritz, H."
    """
    members: dict[str, list[str]] = {}

    if not people_dir.exists():
        print(
            f"WARNING: people directory not found: {people_dir}. "
            "No authors will be highlighted.",
            file=sys.stderr,
        )
        return members

    for path in sorted(people_dir.glob("*.qmd")):
        if path.name == "index.qmd":
            continue

        meta = read_front_matter(path)
        if not meta:
            continue

        if meta.get("highlight-publications", True) is False:
            continue

        title = clean_text(meta.get("title", ""))
        if not title:
            print(
                f"WARNING: {path} has no title; skipping for author highlighting.",
                file=sys.stderr,
            )
            continue

        aliases: list[str] = [title]

        raw_aliases = (
            meta.get("publication-names")
            or meta.get("publication_names")
            or meta.get("publication-name")
            or meta.get("publication_name")
        )

        if isinstance(raw_aliases, str):
            aliases.append(raw_aliases)
        elif isinstance(raw_aliases, list):
            aliases.extend(str(x) for x in raw_aliases if x)

        # Preserve order while removing duplicates.
        aliases = list(dict.fromkeys(clean_text(x) for x in aliases if clean_text(x)))
        members[title] = aliases

    return members


def load_bib_entries(bib_dir: Path) -> list[dict[str, Any]]:
    """Load entries from every *.bib file in the bibliography directory."""
    files = sorted(bib_dir.glob("*.bib"))
    if not files:
        raise FileNotFoundError(f"No .bib files found in {bib_dir}")

    entries: list[dict[str, Any]] = []

    for bib_path in files:
        parser = BibTexParser(common_strings=True)
        parser.ignore_nonstandard_types = False
        parser.customization = convert_to_unicode

        try:
            with bib_path.open("r", encoding="utf-8") as handle:
                db = bibtexparser.load(handle, parser=parser)
        except Exception as exc:
            raise RuntimeError(f"Could not parse BibTeX file: {bib_path}") from exc

        for entry in db.entries:
            entry = dict(entry)
            entry["_source_file"] = str(bib_path)
            entries.append(entry)

    return entries


def normalize_doi(value: str) -> str:
    value = clean_text(value).lower()
    value = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", value)
    value = re.sub(r"^doi:\s*", "", value)
    return value.rstrip(" .")


def normalize_title(value: str) -> str:
    value = clean_text(value).lower()
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", "", value)


def entry_signatures(entry: dict[str, Any]) -> set[str]:
    """Build multiple signatures so duplicate records can match even if metadata differs."""
    signatures: set[str] = set()

    doi = normalize_doi(entry.get("doi", ""))
    if doi:
        signatures.add(f"doi:{doi}")

    title = normalize_title(entry.get("title", ""))
    year = clean_text(entry.get("year", ""))
    if title:
        signatures.add(f"titleyear:{title}:{year}")

    key = clean_text(entry.get("ID", "")).lower()
    if key:
        signatures.add(f"key:{key}")

    return signatures


def richness_score(entry: dict[str, Any]) -> int:
    """Prefer the most metadata-rich copy when a publication appears more than once."""
    score = sum(bool(clean_text(entry.get(field, ""))) for field in USEFUL_FIELDS)

    # DOI and abstract are particularly useful.
    if clean_text(entry.get("doi", "")):
        score += 3
    if clean_text(entry.get("abstract", "")):
        score += 2
    return score


def deduplicate_entries(entries: Iterable[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """
    Deduplicate by any matching DOI, title+year, or BibTeX key.

    Entries with richer metadata are considered first so that, when duplicates
    exist, the version with more links/abstract/journal information is retained.
    """
    ordered = sorted(entries, key=richness_score, reverse=True)

    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    duplicates = 0

    for entry in ordered:
        signatures = entry_signatures(entry)

        if signatures and signatures & seen:
            duplicates += 1
            continue

        unique.append(entry)
        seen.update(signatures)

    return unique, duplicates


def parse_year(entry: dict[str, Any]) -> int | None:
    match = re.search(r"\d{4}", clean_text(entry.get("year", "")))
    return int(match.group(0)) if match else None


def parse_month(entry: dict[str, Any]) -> int:
    raw = clean_text(entry.get("month", "")).lower().strip(".")
    if not raw:
        return 0

    if raw.isdigit():
        value = int(raw)
        return value if 1 <= value <= 12 else 0

    return MONTHS.get(raw, MONTHS.get(raw[:3], 0))


def find_matching_member(author: str, members: dict[str, list[str]]) -> str | None:
    for display_name, aliases in members.items():
        if any(names_match(author, alias) for alias in aliases):
            return display_name
    return None


def format_authors(author_field: str, members: dict[str, list[str]]) -> str:
    authors = split_bibtex_authors(author_field)
    rendered: list[str] = []

    for raw_author in authors:
        display = html.escape(display_author_name(raw_author))
        member = find_matching_member(raw_author, members)

        if member:
            rendered.append(f'<strong class="lab-member">{display}</strong>')
        else:
            rendered.append(display)

    if not rendered:
        return ""

    if len(rendered) == 1:
        return rendered[0]

    if len(rendered) == 2:
        return f"{rendered[0]} and {rendered[1]}"

    return ", ".join(rendered[:-1]) + f", and {rendered[-1]}"


def md_escape(text: str) -> str:
    """
    Escape only the characters most likely to interfere with generated Markdown.
    HTML escaping is also applied because entries may contain user-provided text.
    """
    text = html.escape(clean_text(text))
    return text.replace("|", r"\|")


def format_venue(entry: dict[str, Any]) -> str:
    """Format journal / proceedings / publisher information for common entry types."""
    entry_type = clean_text(entry.get("ENTRYTYPE", "")).lower()

    journal = md_escape(entry.get("journal", ""))
    booktitle = md_escape(entry.get("booktitle", ""))
    publisher = md_escape(entry.get("publisher", ""))
    institution = md_escape(entry.get("institution", ""))
    school = md_escape(entry.get("school", ""))
    volume = md_escape(entry.get("volume", ""))
    number = md_escape(entry.get("number", ""))
    pages = md_escape(entry.get("pages", ""))
    edition = md_escape(entry.get("edition", ""))

    bits: list[str] = []

    if journal:
        bits.append(f"*{journal}*")
        if volume:
            volume_text = f"**{volume}**"
            if number:
                volume_text += f"({number})"
            bits.append(volume_text)
        elif number:
            bits.append(f"({number})")
        if pages:
            bits.append(pages)

    elif booktitle:
        bits.append(f"In *{booktitle}*")
        if pages:
            bits.append(f"pp. {pages}")
        if publisher:
            bits.append(publisher)

    elif entry_type in {"book", "inbook"}:
        if edition:
            bits.append(f"{edition} ed.")
        if publisher:
            bits.append(publisher)

    elif school:
        bits.append(school)

    elif institution:
        bits.append(institution)

    elif publisher:
        bits.append(publisher)

    return ", ".join(bits)


def build_links(entry: dict[str, Any]) -> list[str]:
    links: list[str] = []

    doi = normalize_doi(entry.get("doi", ""))
    url = clean_text(entry.get("url", ""))
    pdf = clean_text(entry.get("pdf", ""))
    preprint = clean_text(entry.get("preprint", ""))

    # Some bibliography managers store a local/remote PDF in "file".
    if not pdf:
        file_field = clean_text(entry.get("file", ""))
        if re.match(r"^https?://", file_field):
            pdf = file_field

    if doi:
        doi_url = f"https://doi.org/{doi}"
        links.append(f"[DOI]({doi_url})")

    if url:
        normalized_url = url.strip()
        if not doi or normalize_doi(normalized_url) != doi:
            links.append(f"[HTML]({normalized_url})")

    if pdf:
        links.append(f"[PDF]({pdf})")

    if preprint:
        links.append(f"[Preprint]({preprint})")

    return links


def format_entry(entry: dict[str, Any], members: dict[str, list[str]]) -> str:
    authors = format_authors(clean_text(entry.get("author", "")), members)
    year = parse_year(entry)
    title = md_escape(entry.get("title", ""))
    venue = format_venue(entry)
    links = build_links(entry)

    pieces: list[str] = []

    if authors:
        pieces.append(authors.rstrip(".") + ".")

    if year:
        pieces.append(f"({year}).")

    if title:
        pieces.append(f"**{title.rstrip('.')}**.")

    if venue:
        pieces.append(venue.rstrip(".") + ".")

    citation = " ".join(pieces)

    if links:
        citation += " " + " ".join(links)

    abstract = md_escape(entry.get("abstract", ""))
    if abstract:
        citation += (
            "\n\n"
            '<details class="pub-abstract">\n'
            "<summary>Abstract</summary>\n\n"
            f"{abstract}\n\n"
            "</details>"
        )

    return citation


def publication_sort_key(entry: dict[str, Any]) -> tuple[int, int, str]:
    year = parse_year(entry) or 0
    month = parse_month(entry)
    title = clean_text(entry.get("title", "")).lower()

    # Newest year/month first, then alphabetical title for deterministic output.
    return (-year, -month, title)


def generate_markdown(
    entries: list[dict[str, Any]],
    members: dict[str, list[str]],
    include_unknown_year: bool = True,
) -> str:
    grouped: dict[int | None, list[dict[str, Any]]] = {}

    for entry in entries:
        year = parse_year(entry)
        if year is None and not include_unknown_year:
            continue
        grouped.setdefault(year, []).append(entry)

    lines: list[str] = [
        "<!--",
        "  AUTO-GENERATED FILE.",
        "  Do not edit this file directly.",
        "  Edit publications/bibliography/*.bib or people/*.qmd instead.",
        "-->",
        "",
    ]

    numeric_years = sorted((y for y in grouped if y is not None), reverse=True)

    for year in numeric_years:
        lines.append(f"## {year}")
        lines.append("")

        for entry in sorted(grouped[year], key=publication_sort_key):
            citation = format_entry(entry, members)
            citation = citation.replace("\n", "\n   ")
            lines.append(f"1. {citation}")
            lines.append("")

    if None in grouped:
        lines.append("## Year not specified")
        lines.append("")
        for entry in sorted(grouped[None], key=publication_sort_key):
            citation = format_entry(entry, members)
            citation = citation.replace("\n", "\n   ")
            lines.append(f"1. {citation}")
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate a year-grouped Quarto publication list from BibTeX files."
    )
    parser.add_argument(
        "--bib-dir",
        type=Path,
        default=Path("publications/bibliography"),
        help="Directory containing *.bib files.",
    )
    parser.add_argument(
        "--people-dir",
        type=Path,
        default=Path("people"),
        help="Directory containing one *.qmd file per lab member.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("publications/_publications.qmd"),
        help="Generated Quarto/Markdown fragment.",
    )
    parser.add_argument(
        "--exclude-unknown-year",
        action="store_true",
        help="Omit publications with no parseable year.",
    )
    args = parser.parse_args()

    try:
        entries = load_bib_entries(args.bib_dir)
    except (FileNotFoundError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    members = load_lab_member_aliases(args.people_dir)
    unique_entries, duplicate_count = deduplicate_entries(entries)

    output = generate_markdown(
        unique_entries,
        members,
        include_unknown_year=not args.exclude_unknown_year,
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(output, encoding="utf-8")

    print(f"Read {len(entries)} BibTeX entries from {args.bib_dir}")
    print(f"Removed {duplicate_count} duplicate entr{'y' if duplicate_count == 1 else 'ies'}")
    print(f"Loaded {len(members)} lab-member profile(s) for author highlighting")
    print(f"Wrote {len(unique_entries)} publication(s) to {args.output}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
