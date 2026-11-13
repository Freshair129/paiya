"""Check Markdown links, the docs index, and requirement traceability."""

import os
import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
INDEX = DOCS / "README.md"
TRACE = DOCS / "quality/TRACEABILITY.md"
SOURCE = {
    "PR": DOCS / "product/PRD.md",
    "FR": DOCS / "requirements/SRS.md",
    "AT": DOCS / "requirements/SRS.md",
}
IGNORE = {
    ".git",
    ".venv",
    ".uv-cache",
    ".uv-python",
    ".tmp",
    ".paiya-validator-env",
    ".collector-state",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "artifacts",
    "node_modules",
    "dist",
    "build",
}
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
INLINE_CODE = re.compile(r"(`+)(.+?)\1")
IDS = re.compile(r"(?<![\w-])(PR|FR|AT)-(\d{2}(?:/\d{2})*)(?![\w-])")
# Accept angle-wrapped destinations and balanced parentheses one level deep.
DEST = r"(?:<([^>]+)>|((?:[^\s()<>]|\([^\s()<>]*\))+))"
LINK = re.compile(r"(?<!\\)\]\(\s*" + DEST + r"""(?:\s+["'][^"']*["'])?\s*\)""")
REF_DEF = re.compile(r"^ {0,3}\[[^\]]+\]:\s*" + DEST)


def markdown_lines(file):
    fence = ""
    for number, line in enumerate(file.read_text(encoding="utf-8").splitlines(), 1):
        match = FENCE.match(line)
        if fence:
            if match and match[1].startswith(fence) and not match[2].strip():
                fence = ""
        elif match:
            fence = match[1]
        elif not line.startswith(("    ", "\t")):
            yield number, line


def main():
    errors, definitions, references = [], {}, []
    indexed, traced, docs_files = set(), set(), set()
    required = {INDEX, TRACE, *SOURCE.values()}
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = sorted(d for d in dirs if d not in IGNORE)
        for name in sorted(files):
            if not name.lower().endswith(".md"):
                continue
            file = Path(base) / name
            required.discard(file)
            if file.is_relative_to(DOCS) and file != INDEX:
                docs_files.add(file)
            for n, line in markdown_lines(file):
                where = f"{file.relative_to(ROOT)}:{n}"
                # A requirement in a code span can be a real reference. Ignore
                # other inline examples, including links written as sample syntax.
                id_text = INLINE_CODE.sub(lambda m: m[2] if IDS.fullmatch(m[2]) else " ", line)
                cell = id_text.strip().removeprefix("|").split("|", 1)[0] if "|" in line else ""
                first = re.match(r"\s*[*_\[]*((?:PR|FR|AT)-\d{2})\b", cell)
                if first:
                    rid = first[1]
                    if file == SOURCE[rid[:2]]:
                        if rid in definitions:
                            errors.append(f"{where}: duplicate {rid} (first at {definitions[rid]})")
                        else:
                            definitions[rid] = where
                    if file == TRACE and rid.startswith("PR-"):
                        traced.add(rid)
                for match in IDS.finditer(id_text):
                    references.extend((f"{match[1]}-{num}", where) for num in match[2].split("/"))
                plain = INLINE_CODE.sub(" ", line)
                for pattern in (LINK, REF_DEF):
                    for match in pattern.finditer(plain):
                        dest = match[1] or match[2]
                        if any(c in dest for c in "{}<>"):
                            continue
                        url = urlsplit(dest)
                        if url.scheme or url.netloc or not url.path:
                            continue
                        path = unquote(url.path)
                        target = ((ROOT if path.startswith("/") else file.parent) / path.lstrip("/")).resolve()
                        if not target.is_relative_to(ROOT) or not target.exists():
                            errors.append(f"{where}: broken local link {dest}")
                        if file == INDEX and pattern is LINK:
                            indexed.add(target)

    errors.extend(f"missing required file: {f.relative_to(ROOT)}" for f in sorted(required))
    for prefix, file in SOURCE.items():
        if not any(rid.startswith(prefix + "-") for rid in definitions):
            errors.append(f"{file.relative_to(ROOT)}: no {prefix} definitions found")
    errors.extend(f"{where}: undefined {rid}" for rid, where in references if rid not in definitions)
    errors.extend(f"{TRACE.relative_to(ROOT)}: missing row for {rid}"
                  for rid in sorted(r for r in definitions if r.startswith("PR-") and r not in traced))
    errors.extend(f"{INDEX.relative_to(ROOT)}: missing link to {f.relative_to(DOCS)}"
                  for f in sorted(docs_files - indexed))
    if errors:
        print("\n".join(dict.fromkeys(errors)), file=sys.stderr)
        return 1
    print(f"Docs OK: {len(docs_files)} indexed documents, {len(definitions)} requirement and test IDs.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
