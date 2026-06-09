pip install pdf2docx
python scripts\pdf_to_docx.py "C:\path\to\file.pdf"
# or specify output + page range
python scripts\pdf_to_docx.py input.pdf output.docx --start 0 --end 10