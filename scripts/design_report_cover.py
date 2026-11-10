"""Apply a restrained engineering-report cover to the existing DOCX."""
from pathlib import Path
from datetime import datetime
import shutil
import sys
from docx import Document
from docx.shared import Pt, Inches, RGBColor
from docx.oxml import OxmlElement
from docx.oxml.ns import qn


def design_cover(path, backup=True):
    path = Path(path)
    if backup:
        folder = path.parent.parent / '.data' / 'report-backups'
        folder.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, folder / ('before-cover-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '.docx'))
    doc = Document(path)
    paragraphs = doc.paragraphs
    summary_index = next(i for i, p in enumerate(paragraphs) if p.text.strip() == 'Executive summary')
    old = paragraphs[:summary_index]
    author = next((p.text.replace('AUTHOR\t', 'Author:', 1).partition(':')[2].strip() for p in old if p.text.startswith(('Author:', 'AUTHOR\t'))), 'NAME TO BE CONFIRMED')
    student = next((p.text.replace('STUDENT ID\t', 'Student ID:', 1).partition(':')[2].strip() for p in old if p.text.startswith(('Student ID:', 'STUDENT ID\t'))), 'n00000000')
    declaration = next((p.text.partition(':')[2].strip() for p in old if p.text.startswith('AI-use declaration:')), '')
    anchor = old[-1]
    if not anchor._p.xpath('.//w:br[@w:type="page"]'):
        raise ValueError('Expected the existing cover page break immediately before the summary.')
    for p in old[:-1]:
        p._p.getparent().remove(p._p)

    navy, teal, gray = '18334D', '167D8D', '51616F'
    def paragraph(text='', size=12, color=navy, bold=False, before=0, after=8):
        p = anchor.insert_paragraph_before()
        p.style = 'Normal'
        f = p.paragraph_format
        f.space_before, f.space_after = Pt(before), Pt(after)
        f.line_spacing = 1.05
        f.keep_with_next = False
        f.widow_control = True
        r = p.add_run(text)
        r.font.name, r.font.size = 'Calibri', Pt(size)
        r.font.bold, r.font.color.rgb = bold, RGBColor.from_string(color)
        return p

    def rule(p, color, size=12, space=12):
        borders = OxmlElement('w:pBdr')
        bottom = OxmlElement('w:bottom')
        for key, value in {'val':'single', 'sz':str(size), 'space':str(space), 'color':color}.items():
            bottom.set(qn('w:' + key), value)
        borders.append(bottom)
        p._p.get_or_add_pPr().append(borders)

    paragraph('QUEENSLAND UNIVERSITY OF TECHNOLOGY', 11, gray, True, before=24, after=10)
    p = paragraph('CLOUD COMPUTING  /  ENGINEERING REPORT', 10, teal, True, after=54)
    rule(p, teal, 16, 14)
    paragraph('CAB432 Assignment 3 2026', 26, navy, True, after=26)
    paragraph('Stillday', 44, navy, True, after=5)
    paragraph('Private Cloud Photo Library', 21, gray, after=18)
    paragraph('Architecture, implementation and evaluation', 12, gray, after=48)

    for label, value in [('AUTHOR', author), ('STUDENT ID', student), ('REPORT DATE', '24 October 2026'), ('STATUS', 'Draft for review')]:
        p = paragraph('', 12, navy, after=9)
        p.paragraph_format.tab_stops.add_tab_stop(Inches(1.5))
        r = p.add_run(label + '\t')
        r.font.name, r.font.size = 'Calibri', Pt(10)
        r.font.bold, r.font.color.rgb = True, RGBColor.from_string(gray)
        r = p.add_run(value)
        r.font.name, r.font.size = 'Calibri', Pt(12)
        r.font.color.rgb = RGBColor.from_string(navy)

    p = paragraph('', after=14, before=28)
    rule(p, 'CBD5DE', 4, 6)
    paragraph('AI USE AND ACKNOWLEDGEMENTS', 10, teal, True, after=6)
    paragraph('AI-use declaration: ' + declaration, 10, gray, after=0)

    # Hide the draft footer only on the cover, retaining existing front-matter/body behaviour.
    first = doc.sections[0]
    first.different_first_page_header_footer = True
    for p in first.first_page_footer.paragraphs:
        p.clear()
    doc.save(path)
    print('Cover redesigned; existing section boundaries and TOC retained.')


if __name__ == '__main__':
    design_cover(sys.argv[1])
