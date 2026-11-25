"""Builders for test CV documents (PDF via reportlab, DOCX via python-docx)."""
import io

CV_LINES = [
    "Asha Verma",
    "Senior Software Engineer | asha.verma@example.com | +91 98765 43210 | linkedin.com/in/asha",
    "Date of Birth: 01/02/1995",
    "Summary",
    "Backend engineer with Java and Spring Boot experience in payments systems.",
    "Experience",
    "Acme Payments | Senior Software Engineer | Apr 2022 - Present",
    "- Built Spring Boot services for card settlement",
    "- Designed event pipelines on Apache Kafka for settlement batches",
    "Globex Fintech | Software Engineer | Jul 2018 - Mar 2022",
    "- Developed REST APIs in Java 11 for merchant onboarding",
    "- Wrote PostgreSQL queries and tuned indexes for reporting",
    "Skills",
    "Java | Spring Boot | Kafka | PostgreSQL | Docker",
    "Education",
    "XYZ Institute of Technology | B.E. Computer Engineering | 2014 - 2018",
    "Declaration",
    "I hereby declare that the above information is true.",
]


def pdf(pages, hidden=None, footer=None):
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    for lines in pages:
        y = 800
        c.setFont("Helvetica", 10)
        c.setFillColorRGB(0, 0, 0)
        for ln in lines:
            c.drawString(40, y, ln)
            y -= 14
        if hidden:
            c.setFillColorRGB(1, 1, 1)
            c.drawString(40, 100, hidden)
            c.setFillColorRGB(0, 0, 0)
        if footer:
            c.drawString(40, 30, footer)
        c.showPage()
    c.save()
    return buf.getvalue()


def docx(lines, table=None):
    import docx as d
    doc = d.Document()
    for ln in lines:
        doc.add_paragraph(ln)
    if table:
        t = doc.add_table(rows=len(table), cols=len(table[0]))
        for i, row in enumerate(table):
            for j, cell in enumerate(row):
                t.cell(i, j).text = cell
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()
