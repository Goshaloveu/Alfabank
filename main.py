from fastapi import FastAPI, UploadFile, File, Request, Form, BackgroundTasks
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import RedirectResponse  # Добавить эту строку
from urllib.parse import quote
from openai import OpenAI
from openai import APIConnectionError, AuthenticationError, APIError
from pathlib import Path
import io
import json
import docx
from docx import Document
import openpyxl
import PyPDF2
from lxml import etree
import os
from dotenv import load_dotenv

load_dotenv() # Загружает переменные из .env

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

GLOSSARY_FILE = "glossary.json"
DEEPSEEK_KEY = os.getenv("DEEPSEEK_API_KEY") # Замените на действительный ключ


def load_glossary():
    if Path(GLOSSARY_FILE).exists():
        with open(GLOSSARY_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return []


def save_glossary(glossary):
    with open(GLOSSARY_FILE, 'w', encoding='utf-8') as f:
        json.dump(glossary, f, ensure_ascii=False, indent=2)


def translate_legal_text(api_token: str, text_to_translate: str, source_lang: str = "английского") -> str:
    client = OpenAI(
        base_url="https://api.deepseek.com/v1",
        api_key=DEEPSEEK_KEY
    )

    system_prompt = """Вы профессиональный юридический переводчик. Правила перевода:
1. Буквальная точность: сохраняйте исходные термины даже если они звучат непривычно
2. Форматирование: 
   - Сохраняйте нумерацию (Article 2.3 → Статья 2.3)
   - Кавычки " → «»
   - Переносы строк и абзацев
3. Запрещено:
   - Добавлять пояснения или комментарии
   - Изменять структуру документа
   - Пропускать части текста
4. Особые случаи:
   - Латинские термины (habeas corpus) оставлять без перевода и писать в кавычках
   - Названия законов и документов в оригинале
   - Сноски и ссылки сохранять как в оригинале"""

    try:
        response = client.chat.completions.create(
            model="deepseek-reasoner",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",
                 "content": f"""Запрещено!!!:
   - Добавлять ЛЮБЫЕ пояснения или комментарии
   - Изменять структуру документа
   - Пропускать части текста"""
                            f"Переведи следующий текст с {source_lang} на русский язык:\n\n{text_to_translate}"}

            ],
            temperature=0.1,
            top_p=0.3,
            frequency_penalty=0.5,
            max_tokens=4000,
            stream=False
        )
        return response.choices[0].message.content

    except APIConnectionError as e:
        return f"Ошибка сети: {e}"
    except AuthenticationError as e:
        return "Неверный API-ключ"
    except APIError as e:
        return f"Ошибка API: {e}"
    except Exception as e:
        return f"Неизвестная ошибка: {e}"


async def process_file(file: UploadFile, translate_func):
    file_ext = Path(file.filename).suffix.lower()
    content = await file.read()

    processors = {
        '.pdf': process_pdf,
        '.docx': process_docx,
        '.xlsx': process_excel,
        '.xml': process_xml,
        '.txt': process_txt
    }

    if file_ext not in processors:
        raise ValueError("Неподдерживаемый формат файла")

    return processors[file_ext](content, translate_func)


def process_pdf(content, translate_func):
    try:
        pdf_reader = PyPDF2.PdfReader(io.BytesIO(content))
        text = "\n".join([page.extract_text() or "" for page in pdf_reader.pages])

        # Явное преобразование текста
        cleaned_text = text.encode('utf-8', errors='replace').decode('utf-8')
        translated = translate_func(cleaned_text)

        output = io.BytesIO()
        pdf_writer = PyPDF2.PdfWriter()

        # Сохраняем метаданные с кодировкой
        if pdf_reader.metadata:
            pdf_writer.add_metadata(pdf_reader.metadata)

        for page in pdf_reader.pages:
            pdf_writer.add_page(page)

        pdf_writer.write(output)
        output.seek(0)
        return output, "application/pdf"
    except Exception as e:
        print(f"Error processing PDF: {str(e)}")
        raise


def process_docx(content, translate_func):
    doc = Document(io.BytesIO(content))
    for paragraph in doc.paragraphs:
        if paragraph.text.strip():
            paragraph.text = translate_func(paragraph.text)

    output = io.BytesIO()
    doc.save(output)
    output.seek(0)
    return output, "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def process_excel(content, translate_func):
    wb = openpyxl.load_workbook(io.BytesIO(content))
    for sheet in wb:
        for row in sheet.iter_rows():
            for cell in row:
                if cell.value and isinstance(cell.value, str):
                    cell.value = translate_func(cell.value)

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    return output, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def process_xml(content, translate_func):
    try:
        # Парсим с указанием кодировки
        parser = etree.XMLParser(encoding='utf-8')
        root = etree.fromstring(content, parser=parser)

        for element in root.iter():
            if element.text and element.text.strip():
                # Переводим текст
                translated = translate_func(element.text)
                # Сохраняем как строку Unicode
                element.text = translated

        # Сериализуем с указанием кодировки
        output = io.BytesIO(etree.tostring(root,
                                           encoding='utf-8',
                                           pretty_print=True))
        return output, "application/xml; charset=utf-8"
    except Exception as e:
        print(f"Error processing XML file: {str(e)}")
        raise


def process_txt(content, translate_func):
    try:
        # Декодируем с использованием UTF-8 и обработкой ошибок
        text = content.decode('utf-8', errors='replace')
        translated = translate_func(text)
        # Кодируем результат обратно в UTF-8
        output = io.BytesIO(translated.encode('utf-8'))
        return output, "text/plain; charset=utf-8"
    except Exception as e:
        print(f"Error processing TXT file: {str(e)}")
        raise


@app.get("/", response_class=HTMLResponse)
async def main_page(request: Request):
    return templates.TemplateResponse("index.html", {
        "request": request,
        "glossary": load_glossary()
    })


@app.post("/translate/")
async def translate(
        request: Request,
        text: str = Form(None),
        file: UploadFile = File(None),
        source_lang: str = Form("английского")
):
    try:
        # Исправленная проверка на наличие файла
        if file and file.filename != '' and file.size > 0:
            output, media_type = await process_file(file, lambda t: translate_legal_text(API_TOKEN, t, source_lang))

            def cleanup():
                if isinstance(output, io.BytesIO):
                    output.close()

            return StreamingResponse(
                output,
                media_type=media_type,
                headers={
                    "Content-Disposition": f"attachment; filename*=UTF-8''{quote(file.filename)}",
                    "Content-Type": f"{media_type}; charset=utf-8"
                }
            )

        if text:
            translation = translate_legal_text(API_TOKEN, text, source_lang)
            return templates.TemplateResponse("index.html", {
                "request": request,
                "original_text": text,
                "translation": translation,
                "glossary": load_glossary()
            })

        return templates.TemplateResponse("index.html", {
            "request": request,
            "error": "Введите текст или выберите файл",
            "glossary": load_glossary()
        })

    except Exception as e:
        return templates.TemplateResponse("index.html", {
            "request": request,
            "error": f"Ошибка: {str(e)}",
            "glossary": load_glossary()
        })
@app.post("/glossary/add")
async def add_term(
        request: Request,
        term: str = Form(...),
        translation: str = Form(...)
):
    glossary = load_glossary()
    glossary.append({"term": term, "translation": translation})
    save_glossary(glossary)
    return RedirectResponse(url="/", status_code=303)


@app.post("/glossary/delete/{term_index}")
async def delete_term(request: Request, term_index: int):
    glossary = load_glossary()
    if 0 <= term_index < len(glossary):
        del glossary[term_index]
        save_glossary(glossary)
    return RedirectResponse(url="/", status_code=303)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)