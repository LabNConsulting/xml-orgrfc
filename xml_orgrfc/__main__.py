#!/usr/bin/env python3
# -*- coding: utf-8 eval: (blacken-mode 1) -*-
#
# August 24 2024, Christian Hopps <chopps@labn.net>
#
# Copyright (c) 2024, LabN Consulting, L.L.C.
#
"""Convert XMLRFC document to markdown."""
import argparse
import logging
import re
import sys
import textwrap
import xml.etree.ElementTree as ET


class Globals:
    """A placeholder for global values."""

    did_author = False
    keywords = set()
    sec_refs = {}


glb = Globals()


def _unindent(text):
    return re.sub("\n[ \t]+", " ", text)


def add_elt_attr(elt, attr, name, lines):
    """Add org mode statement based on presence of xml attribute."""
    v = elt.attrib.get(attr, "").strip()
    if v:
        lines.append(f"#+{name}: {v}\n")


def _fill(*args, **kwargs):
    s = textwrap.fill(*args, **kwargs)
    s = re.sub(r"\n([ \t]*)((\d+\.(\D|$)|- |\* )[ \t]?)", r" \2\n\1", s)
    return s


rfc_attr_map = {
    "category": "RFC_CATEGORY",
    "consensus": "RFC_CONSENSUS",
    "docName": "RFC_NAME",
    "ipr": "RFC_IPR",
    "obsoletes": "RFC_OBSOLETES",
    "submissionType": "RFC_STREAM",
    "tocDepth": "RFC_TOC_DEPTH",
    "updates": "RFC_UPDATES",
    "version": "RFC_XML_VERSION",
}


def _cvt_rfc_attr(elt, lines):
    for k, v in elt.attrib.items():
        if k == "docName":
            m = re.match(r"([-a-z0-9]+)-([0-9][0-9])", v)
            assert m, "Invalid docName format"
            lines.append(f"#+RFC_NAME: {m.group(1)}\n")
            lines.append(f"#+RFC_VERSION: {m.group(2)}\n")
        elif k in rfc_attr_map:
            if k in ["obsoletes", "updates"] and not v:
                continue
            lines.append(f"#+{rfc_attr_map[k]}: {v}\n")
        # elif k == "tocInclude":
        #     pass
        # elif k == "tocDepth":
        #     pass
        # elif k == "symRefs":
        #     pass
        # elif k == "sortRefs":
        #     pass
        else:
            logging.warning("Not processing rfc tag attribute: %s", k)

    lines.append("\n")


def _cvt_front_ref(front, lines):
    e = front.find("title")
    if e is not None:
        lines.append(f":REF_TITLE: {e.text.strip()}\n")

    author = front.find("author")
    if author is not None:
        org = author.find("organization")
        lines.append(f":REF_ORG: {org.text.strip()}\n")


def _cvt_reference(child, lines):
    if "include" in child.tag and "href" in child.attrib:
        m = re.match(r".*reference\.RFC\.([0-9]+)\.xml", child.attrib["href"])
        if m:
            lines.append("** RFC" + m.group(1) + "\n")
            return

        m = re.match(r".*reference\.I-D.([-a-z0-9]+)\.xml", child.attrib["href"])
        if m:
            lines.append("** I-D." + m.group(1) + "\n")
            return

    if child.tag == "reference":
        assert "anchor" in child.attrib, "Anchor required for reference"
        anchor = child.attrib["anchor"]
        lines.append(f"** {anchor}\n")

        lines.append(":PROPERTIES:\n")

        if "target" in child.attrib:
            lines.append(f":REF_TARGET: {child.attrib['target'].strip()}\n")

        front = child.find("front")
        if front is not None:
            _cvt_front_ref(front, lines)

        lines.append(":END:\n")


def _cvt_table(table, lines):  # pylint: disable=too-many-locals,too-many-statements
    name = table.find("name")
    if name is not None:
        lines.append(f"#+caption: {name.text.strip()}\n")
    add_elt_attr(table, "anchor", "name", lines)

    e = table.find("thead")
    hrows = list(e) if e is not None else []
    e = table.find("tbody")
    brows = list(e) if e is not None else []
    e = table.find("tfoot")
    frows = list(e) if e is not None else []

    # Get the max width of columns
    allrows = hrows + brows + frows
    maxw = []
    for row in allrows:
        elts = list(row)
        widths = [len(x.text.strip()) if x.text else 0 for x in elts]
        maxw = [max(x, y) for x, y in zip(widths, maxw)] if maxw else widths
    maxw = [2 + x for x in maxw]

    # Create the table rule
    rule = "|"
    first = True
    for width in maxw:
        if first:
            first = False
        else:
            rule += "+"
        rule += "-" * (width)
    rule += "|\n"

    # Get the column alignment
    aligns = None
    for row in allrows:
        elts = list(row)
        a = [x.attrib.get("align") for x in elts]
        aligns = [x if x else y for x, y in zip(aligns, a)] if aligns else a
    aligns = [x[0] if x else "l" for x in aligns]

    lines.append(rule)
    aligned = False
    for rows in [hrows, brows, frows]:
        if not rows:
            continue

        def get_row_text(width, align, etext):
            if align == "c":
                align = "^"
            elif align == "r":
                align = ">"
            elif align == "l":
                align = "<"
            else:
                align = ""
            lspace = " "
            rspace = " "
            # if len(etext) == width:
            #     lspace = ""
            #     rspace = ""
            # elif len(etext) == width - 1:
            #     rspace = ""
            return f"|{{:{align}{width}s}}".format(lspace + etext + rspace)

        for row in rows:
            rtext = ""
            for width, align, elt in zip(maxw, aligns, list(row)):
                etext = elt.text.strip() if elt.text is not None else ""
                rtext += get_row_text(width, align, etext)
            rtext += "|\n"
            lines.append(rtext)
        if not aligned:
            aligned = True
            rtext = ""
            for width, align in zip(maxw, aligns):
                etext = "<" + align + ">"
                rtext += get_row_text(width, align, etext)
            rtext += "|\n"
            lines.append(rtext)

        lines.append(rule)


# XXX this won't do good things with embeded artwork
def _cvt_mid_back_text_elt(elt, text, indent, mid, level):
    tlines = [text]

    for child in elt:
        _cvt_mid_back(child, tlines, mid, level)
        if child.tail:
            text = _unindent(child.tail)
            tlines[-1] += text

    # text = " ".join(tlines)
    text = "".join(tlines)
    if indent:
        return _fill(text, 69, subsequent_indent=" " * indent, break_on_hyphens=False)
    return _fill(text, 69, break_on_hyphens=False)


def _cvt_mid_back(
    elt, lines, mid, level
):  # pylint: disable=too-many-branches,too-many-statements,too-many-locals
    """Recursively convert to middle section."""
    logging.debug(
        "processing %s child level %s tag %s attrib %s",
        "middle" if mid else "back",
        level,
        elt.tag,
        elt.attrib,
    )

    # Handle section tags
    if elt.tag == "section":
        title = elt.attrib["title"].strip() if "title" in elt.attrib else ""
        if not title:
            e = elt.find("name")
            if e is not None:
                title = e.text.strip()
        if title:
            header_prefix = "*" * (level + 1)
            lines.append(f"\n{header_prefix} {title}\n")

            anchor = elt.attrib.get("anchor", "").strip()
            if anchor and glb.sec_refs[anchor] > 0:
                lines.append(":PROPERTIES:\n")
                lines.append(f":CUSTOM_ID: {anchor}\n")
                lines.append(":END:\n")

        lines.append("\n")

        for child in elt:
            if child.tag != "title":  # Skip title since it's already handled
                _cvt_mid_back(child, lines, mid, level + 1)

    elif elt.tag == "references":
        assert not mid, "references not allowed in middle section"
        title = elt.find("name")
        if title is not None:
            header_prefix = "*" * (level + 1)
            lines.append(f"\n{header_prefix} {title.text.strip()}\n\n")

        for child in elt:
            if child.tag == "name":
                continue
            _cvt_reference(child, lines)

    # Handle paragraph tags
    elif elt.tag == "t":
        ptext = _unindent(elt.text).lstrip()
        ptext = _cvt_mid_back_text_elt(elt, ptext, 0, mid, level)
        ptext = re.sub("\n\n\n+", "\n\n", ptext)
        if not ptext.endswith("\n\n"):
            if ptext.endswith("\n"):
                ptext += "\n"
            else:
                ptext += "\n\n"
        lines.append(ptext)
    elif elt.tag == "xref":
        target = elt.attrib["target"]
        assert target, "xref missing target attribute"
        if target in glb.sec_refs:
            lines.append(f"[[#{target}]]")
        else:
            lines.append(f"[[{target}]]")

        for child in elt:
            _cvt_mid_back(child, lines, mid, level)

    elif elt.tag == "table":
        _cvt_table(elt, lines)

    # Handle list tags (assumes unordered list)
    elif elt.tag == "dl":
        lines.append("\n")
        it = iter(elt)
        try:
            attrs = ""
            if elt.attrib.get("hanging") in ["true", "yes"]:
                attrs += " :hanging t"
            if elt.attrib.get("spacing") == "compact":
                attrs += " :compact t"
            if attrs:
                lines.append(f"#+ATTR_RFC:{attrs}\n")
            while True:
                dt = next(it)
                term = dt.text.strip()
                dl = next(it)
                itext = _unindent(dl.text).lstrip()
                # itext = _fill(itext, 69, subsequent_indent=" " * 2)
                itext = _cvt_mid_back_text_elt(dl, itext, 2, mid, level)
                lines.append(f"- {term} :: {itext}\n")
        except StopIteration:
            pass
        lines.append("\n")

    # Handle list tags (assumes unordered list)
    elif elt.tag in ["ol", "ul"]:
        lines.append("\n")
        attrs = ""
        if elt.attrib.get("spacing") == "compact":
            attrs += " :compact t"
        if attrs:
            lines.append(f"#+ATTR_RFC:{attrs}\n")
        for item in elt.findall("li"):
            itext = _unindent(item.text).lstrip()
            # itext = _fill(itext, 69, subsequent_indent=" " * 2)
            itext = _cvt_mid_back_text_elt(item, itext, 2, mid, level)
            lines.append(f"- {itext}\n")
        lines.append("\n")

    # Handle code block tags
    elif elt.tag == "figure":
        e = elt.find("name")
        name = "" if e is None else e.text.strip()
        artwork = elt.find("artwork").text
        if name:
            lines.append(f"#+caption: {name}\n")
        add_elt_attr(elt, "anchor", "name", lines)
        lines.append(f"#+begin_src\n{artwork}\n#+end_src\n")

    # Process other nested elts
    else:
        for child in elt:
            _cvt_mid_back(child, lines, mid, level + 1)


def convert_xml_front(elt, lines, level):  # pylint: disable=too-many-branches
    """Convert front section."""
    logging.debug(
        "processing front level %s tag %s atttrib %s", level, elt.tag, elt.attrib
    )

    if elt.tag == "title":
        lines.append("#+TITLE: " + elt.text.strip() + "\n")
        add_elt_attr(elt, "abbrev", "RFC_SHORT_TITLE", lines)
    elif elt.tag == "author":
        fname = elt.attrib.get("fullname")
        email = ""
        org = ""
        org_abbrev = ""

        e = elt.find("address")
        if e is not None:
            e = e.find("email")
            if e is not None:
                email = e.text.strip()
        e = elt.find("organization")
        if e is not None:
            org = e.text.strip()
            org_abbrev = e.attrib.get("abbrev", "").strip()

        if glb.did_author:
            if org_abbrev:
                org = f'("{org_abbrev}" "{org}")'
            else:
                org = f'"{org}"'
            add = f'("{fname}" "{email}" {org})'
            lines.append("#+RFC_ADD_AUTHOR: " + add + "\n")
        else:
            glb.did_author = True
            lines.append(f"#+AUTHOR: {fname:s}\n")
            if email:
                lines.append(f"#+EMAIL: {email:s}\n")
            if org:
                lines.append(f"#+AFFILIATION: {org:s}\n")
            if org_abbrev:
                lines.append(f"#+RFC_SHORT_ORG: {org_abbrev:s}\n")
    elif elt.tag == "abstract":
        lines.append("\n#+begin_abstract\n")
        for child in elt:
            _cvt_mid_back(child, lines, True, level + 1)
        lines.append("#+end_abstract\n")
    elif elt.tag == "area":
        lines.append("#+RFC_AREA: " + elt.text.strip() + "\n")
    elif elt.tag == "workgroup":
        lines.append("#+RFC_WORKGROUP: " + elt.text.strip() + "\n")
    elif elt.tag == "keyword":
        glb.keywords.add(elt.text.strip())
    else:
        for child in elt:
            convert_xml_front(child, lines, level + 1)


def convert_xml_middle(elt, lines):
    """Convert middle section."""
    _cvt_mid_back(elt, lines, True, 0)


def convert_xml_back(elt, lines):
    """Convert back section."""
    _cvt_mid_back(elt, lines, False, 0)


def top_level(root):
    """Convert the document."""
    lines = []

    if root.tag == "rfc":
        _cvt_rfc_attr(root, lines)

        for elt in root:
            if elt.tag == "front":
                logging.debug("processing front tag")
                for child in elt:
                    convert_xml_front(child, lines, 0)
                keylist = [f'"{x}"' for x in glb.keywords]
                if keylist:
                    lines.append(f'#+RFC_KEYWORDS: ({" ".join(reversed(keylist))})\n')
            elif elt.tag == "middle":
                logging.debug("processing middle tag")
                for child in elt:
                    convert_xml_middle(child, lines)
            elif elt.tag == "back":
                logging.debug("processing back tag")
                for child in elt:
                    convert_xml_back(child, lines)
            else:
                logging.debug("Ignoring RFC child tag %s", elt.tag)
    else:
        logging.debug("Ignoring tag %s", elt.tag)

    return lines


def gather_section_refs(root):
    """Create dictionary of all section references."""
    for elt in root.findall(".//section"):
        if anchor := elt.attrib.get("anchor", ""):
            glb.sec_refs[anchor] = 0

    for elt in root.findall(".//xref"):
        if target := elt.attrib.get("target", ""):
            if target in glb.sec_refs:
                glb.sec_refs[target] += 1


def convert_xml_to_markdown(xml_string):
    """Convert to markdown."""
    # Parse the XML string into an ElementTree object
    root = ET.fromstring(xml_string)

    # ET.dump(root)

    gather_section_refs(root)

    # Convert the entire XML structure to Markdown
    lines = top_level(root)

    return "".join(lines)


# Convert the XML to Markdown
def main():
    """Main function."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--verbose", action="store_true", help="Be verbose")
    parser.add_argument("file")
    args = parser.parse_args()

    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s: %(message)s")

    if args.file:
        infile = open(args.file)
    else:
        infile = sys.stdin
    inp = infile.read()
    markdown_output = convert_xml_to_markdown(inp)

    print(
        r"""# Do: title, toc:table-of-contents ::fixed-width-sections |tables
# Do: ^:sup/sub with curly -:special-strings *:emphasis
# Don't: prop:no-prop-drawers \n:preserve-linebreaks ':use-smart-quotes
#+OPTIONS: prop:nil title:t toc:t \n:nil ::t |:t ^:{} -:t *:t ':nil
"""
    )
    print(markdown_output)


if __name__ == "__main__":
    main()
