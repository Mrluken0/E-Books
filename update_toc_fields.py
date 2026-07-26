import sys
import win32com.client
import pythoncom

def update_toc_fields(docx_path):
    pythoncom.CoInitialize()
    word = win32com.client.Dispatch("Word.Application")
    word.Visible = False
    try:
        doc = word.Documents.Open(docx_path)
        doc.Fields.Update()
        # Belt-and-suspenders : force aussi la mise à jour de l'objet TOC lui-même
        for toc in doc.TablesOfContents:
            toc.Update()
        doc.Save()
        doc.Close()
    finally:
        word.Quit()
        pythoncom.CoUninitialize()

if __name__ == "__main__":
    docx_path = sys.argv[1]
    update_toc_fields(docx_path)
    print(f"Champs mis à jour : {docx_path}")