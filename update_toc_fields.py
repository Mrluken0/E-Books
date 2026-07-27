import sys
import json
import os
import win32com.client
import pythoncom

def update_toc_fields(docx_path):
    docx_path = os.path.normpath(docx_path)
    pythoncom.CoInitialize()
    word = win32com.client.Dispatch("Word.Application")
    word.Visible = False
    try:
        doc = word.Documents.Open(docx_path)
        doc.Fields.Update()
        for toc in doc.TablesOfContents:
            toc.Update()
        doc.Save()
        doc.Close()
        return {"status": "ok", "output": docx_path}
    except Exception as e:
        return {"status": "error", "message": str(e)}
    finally:
        word.Quit()
        pythoncom.CoUninitialize()

if __name__ == "__main__":
    docx_path = sys.argv[1]
    result = update_toc_fields(docx_path)
    print(json.dumps(result))
