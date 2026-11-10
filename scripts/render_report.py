"""Render the reviewed Markdown source into a draft Word report and PDF."""
from pathlib import Path
from html import escape
import re,json
from docx import Document
from docx.shared import Inches,Pt
from docx.enum.section import WD_SECTION_START,WD_ORIENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from reportlab.platypus import BaseDocTemplate,PageTemplate,Frame,Paragraph,Spacer,PageBreak,NextPageTemplate,Table,TableStyle,Image
from reportlab.lib.pagesizes import A4,landscape
from reportlab.lib.styles import getSampleStyleSheet,ParagraphStyle
from reportlab.lib import colors
from pypdf import PdfReader
ROOT=Path(__file__).resolve().parents[1];SOURCE=ROOT/'docs/report-draft.md';OUT=ROOT/'deliverables';OUT.mkdir(exist_ok=True)

def main():
 text=SOURCE.read_text(encoding='utf-8-sig');blocks=[];lines=text.splitlines();i=0
 while i<len(lines):
  line=lines[i].strip()
  if not line:i+=1;continue
  if line=='<!-- PAGE -->':blocks.append(('page',None));i+=1;continue
  if line.startswith('!FIGURE['):
   m=re.fullmatch(r'!FIGURE\[(.*?)\]\((.*?)\)',line);blocks.append(('figure',m.groups()));i+=1;continue
  if line.startswith('|'):
   rows=[]
   while i<len(lines) and lines[i].strip().startswith('|'):
    row=[c.strip() for c in lines[i].strip().strip('|').split('|')]
    if not all(re.fullmatch(r'[-: ]+',c) for c in row):rows.append(row)
    i+=1
   blocks.append(('table',rows));continue
  if line.startswith('#'):
   level=len(line)-len(line.lstrip('#'));blocks.append(('heading',(level,line[level:].strip())));i+=1;continue
  para=[line];i+=1
  while i<len(lines) and lines[i].strip() and not lines[i].startswith(('#','|','<!--','!FIGURE')):para.append(lines[i].strip());i+=1
  blocks.append(('paragraph',' '.join(para)))
 doc=Document();normal=doc.styles['Normal'];normal.font.name='Calibri';normal.font.size=Pt(12);normal.paragraph_format.space_after=Pt(8)
 def setup(section,wide=False):
  section.orientation=WD_ORIENT.LANDSCAPE if wide else WD_ORIENT.PORTRAIT
  section.page_width=Inches(11.69 if wide else 8.27);section.page_height=Inches(8.27 if wide else 11.69)
  section.top_margin=section.bottom_margin=Inches(.65);section.left_margin=section.right_margin=Inches(.7)
 setup(doc.sections[0]);footer=doc.sections[0].footer.paragraphs[0];footer.text='Stillday | Draft for review | '
 field=OxmlElement('w:fldSimple');field.set(qn('w:instr'),'PAGE');footer._p.append(field)
 styles=getSampleStyleSheet();styles.add(ParagraphStyle(name='Body12',fontName='Helvetica',fontSize=12,leading=16,spaceAfter=8,splitLongWords=True));styles.add(ParagraphStyle(name='Cell12',fontName='Helvetica',fontSize=11,leading=14,spaceAfter=0));styles.add(ParagraphStyle(name='Caption12',fontName='Helvetica',fontSize=12,leading=15,spaceAfter=12))
 styles['Heading1'].fontSize=19;styles['Heading1'].leading=24;styles['Heading2'].fontSize=14;styles['Heading2'].leading=18
 def para(s,style='Body12'):return Paragraph(escape(s),styles[style])
 story=[]
 class Report(BaseDocTemplate):
  def afterFlowable(self,flow):
   if isinstance(flow,Paragraph) and flow.style.name=='Heading1':
    title=flow.getPlainText();key='section-'+str(self.page)+'-'+str(len(title));self.canv.bookmarkPage(key);self.canv.addOutlineEntry(title,key,0,False)
 def chrome(canvas,document):
  w,h=canvas._pagesize;canvas.saveState();canvas.setFont('Helvetica',9);canvas.setFillColor(colors.HexColor('#64748b'));canvas.drawString(40,24,'Stillday | DRAFT - outstanding items identified in text');canvas.drawRightString(w-40,24,str(document.page));canvas.restoreState()
 pdf=Report(str(OUT/'Stillday-report-draft.pdf'),pagesize=A4,title='CAB432 Assignment 3 2026 - Stillday (draft)',author='n00000000',leftMargin=42,rightMargin=42,topMargin=40,bottomMargin=40)
 portrait=PageTemplate(id='portrait',frames=[Frame(42,42,A4[0]-84,A4[1]-84,id='normal',leftPadding=0,rightPadding=0,topPadding=0,bottomPadding=0)],onPage=chrome,pagesize=A4)
 w,h=landscape(A4);wide=PageTemplate(id='wide',frames=[Frame(36,40,w-72,h-80,id='wideframe',leftPadding=0,rightPadding=0,topPadding=0,bottomPadding=0)],onPage=chrome,pagesize=(w,h));pdf.addPageTemplates([portrait,wide])
 for block_index,(kind,value) in enumerate(blocks):
  if kind=='page':
   doc.add_page_break()
   if story and not isinstance(story[-1],PageBreak):story.append(PageBreak())
  elif kind=='heading':
   level,title=value;doc.add_heading(title,level=min(level,3));story.append(para(title,'Heading1' if level==1 else 'Heading2'))
   if title=='Table of contents':
    p=doc.add_paragraph();f=OxmlElement('w:fldSimple');f.set(qn('w:instr'),'TOC \\o "1-2" \\h \\z \\u');p._p.append(f)
  elif kind=='paragraph':doc.add_paragraph(value);story.append(para(value))
  elif kind=='table':
   t=doc.add_table(rows=1,cols=len(value[0]));t.style='Light Shading Accent 1'
   for j,c in enumerate(value[0]):t.rows[0].cells[j].text=c
   for row in value[1:]:
    cells=t.add_row().cells
    for j,c in enumerate(row):cells[j].text=c
   widths=[(A4[0]-84)/len(value[0])]*len(value[0]);table=Table([[para(c,'Cell12') for c in row] for row in value],colWidths=widths,repeatRows=1,hAlign='LEFT')
   table.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#dbeafe')),('VALIGN',(0,0),(-1,-1),'TOP'),('GRID',(0,0),(-1,-1),.4,colors.HexColor('#cbd5e1')),('TOPPADDING',(0,0),(-1,-1),7),('BOTTOMPADDING',(0,0),(-1,-1),7)]));story.extend([table,Spacer(1,12)])
  elif kind=='figure':
   caption,relative=value;path=SOURCE.parent/relative
   section=doc.add_section(WD_SECTION_START.NEW_PAGE);setup(section,True);doc.add_picture(str(path),width=Inches(9.9));doc.add_paragraph(caption,style='Caption')
   if block_index+1>=len(blocks) or blocks[block_index+1][0]!='figure':
    section=doc.add_section(WD_SECTION_START.NEW_PAGE);setup(section)
   story.extend([NextPageTemplate('wide'),PageBreak(),para(caption,'Caption12')]);im=Image(str(path));scale=min((w-72)/im.imageWidth,(h-130)/im.imageHeight);im.drawWidth=im.imageWidth*scale;im.drawHeight=im.imageHeight*scale;story.extend([im,NextPageTemplate('portrait'),PageBreak()])
 settings=doc.settings.element;update=OxmlElement('w:updateFields');update.set(qn('w:val'),'true');settings.append(update)
 doc.save(OUT/'Stillday-report-draft.docx')
 from format_word_pagination import format_report
 format_report(OUT/'Stillday-report-draft.docx', backup=False)
 from design_report_cover import design_cover
 design_cover(OUT/'Stillday-report-draft.docx', backup=False)
 if '--pdf' not in __import__('sys').argv:
  result={'format':'docx','draft':True,'words':len(text.split()),'pagination':'Recheck in Word after final edits; no PDF generated.'}
  (OUT/'report-build-check.json').write_text(json.dumps(result,indent=2))
  print(json.dumps(result));return
 for j in range(len(story)-4,-1,-1):
  if isinstance(story[j],NextPageTemplate) and isinstance(story[j+1],PageBreak) and isinstance(story[j+2],NextPageTemplate) and isinstance(story[j+3],PageBreak):
   del story[j:j+2]
 pdf.build(story)
 reader=PdfReader(OUT/'Stillday-report-draft.pdf');pages=len(reader.pages);references=next((i+1 for i,p in enumerate(reader.pages) if '8. References' in (p.extract_text() or '') and '[R1]' in (p.extract_text() or '')),None)
 result={'pdfPages':pages,'mainReportPagesBeforeReferences':references-1 if references else None,'words':len(text.split()),'draft':True,'openItems':['Author name','Actual interface screenshots','Complete AWS Pricing Calculator total and public share link','Personal reflection','Video recording and submission','Gradescope tag/region validation']}
 (OUT/'report-build-check.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2));assert references and references-1<=30
if __name__=='__main__':main()
