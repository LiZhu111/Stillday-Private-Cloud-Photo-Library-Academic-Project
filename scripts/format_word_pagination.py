"""Format existing Word report without rebuilding its application content."""
from copy import deepcopy
from pathlib import Path
from datetime import datetime
import shutil
import sys

from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt


def format_report(path, backup=True):
    path = Path(path)
    if backup:
        folder = path.parent.parent / '.data' / 'report-backups'
        folder.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, folder / (path.stem + '-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '.docx'))
    doc = Document(path)
    paragraphs = doc.paragraphs
    toc_heading = next(p for p in paragraphs if p.text.strip() == 'Table of contents')
    body_heading = next(p for p in paragraphs if p.text.strip() == '1. Introduction and requirements')
    toc_index = next(i for i, p in enumerate(paragraphs) if p._p is toc_heading._p)
    body_index = next(i for i, p in enumerate(paragraphs) if p._p is body_heading._p)

    # Remove the old TOC field, manual chapter list and editing instructions.
    # Preserve the final paragraph if it already carries the section boundary.
    between = paragraphs[toc_index + 1:body_index]
    boundary = between[-1] if between else body_heading.insert_paragraph_before()
    for p in between[:-1]:
        p._p.getparent().remove(p._p)
    boundary.clear()
    if boundary._p.get_or_add_pPr().find(qn('w:sectPr')) is None:
        sect = deepcopy(doc.sections[0]._sectPr)
        kind = sect.find(qn('w:type'))
        if kind is None:
            kind = OxmlElement('w:type')
            sect.insert(0, kind)
        kind.set(qn('w:val'), 'nextPage')
        boundary._p.get_or_add_pPr().append(sect)

    toc = boundary.insert_paragraph_before()
    for kind, text in [('begin', None), (None, ' TOC \\o "1-2" \\h \\z '), ('separate', None), (None, 'Table of contents will be updated by Word.'), ('end', None)]:
        run = toc.add_run()
        if kind:
            element = OxmlElement('w:fldChar')
            element.set(qn('w:fldCharType'), kind)
            run._r.append(element)
        elif text.startswith(' TOC'):
            element = OxmlElement('w:instrText')
            element.set(qn('xml:space'), 'preserve')
            element.text = text
            run._r.append(element)
        else:
            run.text = text

    if 'Front Matter Heading' not in doc.styles:
        style = doc.styles.add_style('Front Matter Heading', WD_STYLE_TYPE.PARAGRAPH)
        style.base_style = doc.styles['Normal']
        style.font.size = Pt(19)
        style.font.bold = True
        style.paragraph_format.keep_with_next = True
        style.paragraph_format.space_after = Pt(8)
    for p in doc.paragraphs:
        if p._p is body_heading._p:
            break
        if p.text.strip() == 'CAB432 Assignment 3 2026':
            p.style = 'Title'
        elif p.text.strip() == 'Stillday - Private Cloud Photo Library':
            p.style = 'Subtitle'
        elif p.style.name in ('Heading 1', 'Heading 2'):
            p.style = 'Front Matter Heading'

    sections = doc.sections
    # Keep one unnumbered front-matter footer and a distinct numbered body footer.
    for section in sections[1:]:
        for ref in list(section._sectPr.findall(qn('w:footerReference'))):
            section._sectPr.remove(ref)
    front = sections[0].footer
    for p in front.paragraphs:
        p.clear()
    front.paragraphs[0].text = 'Stillday | Draft for review'
    body = sections[1]
    body.footer.is_linked_to_previous = False
    footer = body.footer.paragraphs[0]
    footer.text = 'Stillday | Draft for review | '
    page = OxmlElement('w:fldSimple')
    page.set(qn('w:instr'), 'PAGE')
    footer._p.append(page)
    for i, section in enumerate(sections):
        numbering = section._sectPr.find(qn('w:pgNumType'))
        if numbering is not None:
            section._sectPr.remove(numbering)
        if i == 1:
            numbering = OxmlElement('w:pgNumType')
            numbering.set(qn('w:start'), '1')
            numbering.set(qn('w:fmt'), 'decimal')
            section._sectPr.append(numbering)
    settings = doc.settings.element
    for item in list(settings.findall(qn('w:updateFields'))):
        settings.remove(item)
    update = OxmlElement('w:updateFields')
    update.set(qn('w:val'), 'true')
    settings.append(update)
    doc.save(path)
    print('Formatted report: automatic TOC, unnumbered front matter, body starts at 1; sections=' + str(len(sections)))


if __name__ == '__main__':
    format_report(sys.argv[1])
