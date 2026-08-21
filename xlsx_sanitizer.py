"""Feature — Excel (.xlsx/.xlsm) ফাইলে ব্যক্তিগত তথ্য অটো-রিড্যাকশন।

কোনো Source Channel থেকে .xlsx ফাইলসহ পোস্ট এলে (Destination Channel বা
Channel → Group ফরওয়ার্ড — দুই জায়গাতেই), পাঠানোর আগে প্রতিটা sheet-এর
প্রতিটা cell স্ক্যান করে সেই টেক্সটে Username/Phone/Email/t.me link
(🛡️ প্রাইভেসি ফিল্টার-এ যেগুলো ON করা আছে, ঠিক Post-এর টেক্সটে যেভাবে কাজ
করে) থাকলে মুছে/replace করে দেয়। মূল ফাইল কখনো বদলানো হয় না — sanitize করা
একটা আলাদা কপি বানিয়ে সেটা পাঠানো হয়, মূল ফাইল শুধু temp storage-এই থাকে
এবং কাজ শেষে মুছে ফেলা হয়।

কোনো কারণে ফাইল পড়া/সেভ করা ব্যর্থ হলে (corrupt ফাইল, পাসওয়ার্ড-প্রোটেক্টেড
ইত্যাদি), sanitize না করে মূল ফাইলই পাঠানো হয় — পোস্ট আটকে থাকবে না, শুধু
নিরাপদে যতটা সম্ভব রক্ষা করার চেষ্টা করা হয়।
"""
from pathlib import Path

from privacy import clean_personal

XLSX_EXTENSIONS = (".xlsx", ".xlsm")


def is_excel_file(filename) -> bool:
    return str(filename or "").lower().endswith(XLSX_EXTENSIONS)


def sanitize_xlsx(source_path: Path, settings: dict) -> tuple[Path, int]:
    """Cell-by-cell রিড্যাকশন চালিয়ে (sanitized_path, redacted_cell_count)
    রিটার্ন করে। redacted_cell_count == 0 মানে হয় কিছু পাওয়া যায়নি, নয়তো
    sanitize ব্যর্থ হয়েছে — দুই ক্ষেত্রেই sanitized_path == source_path,
    অর্থাৎ কলার নিরাপদে মূল ফাইলটাই ব্যবহার করতে পারে।"""
    try:
        import openpyxl
    except ImportError:
        print("⚠️ openpyxl ইনস্টল করা নেই — Excel sanitize বাদ দিয়ে মূল ফাইল পাঠানো হচ্ছে।")
        return source_path, 0

    try:
        workbook = openpyxl.load_workbook(source_path)
    except Exception as error:
        print(f"⚠️ Excel ফাইল পড়া যায়নি, sanitize বাদ দেওয়া হলো: {error}")
        return source_path, 0

    privacy_rules = settings.get("privacy")
    replacements = settings.get("replacements")
    redacted = 0
    for sheet in workbook.worksheets:
        for row in sheet.iter_rows():
            for cell in row:
                value = cell.value
                if isinstance(value, str) and value.strip():
                    cleaned = clean_personal(value, privacy_rules, replacements)
                    if cleaned != value:
                        cell.value = cleaned
                        redacted += 1

    if redacted == 0:
        return source_path, 0

    output_path = source_path.with_name(f"sanitized_{source_path.name}")
    try:
        workbook.save(output_path)
    except Exception as error:
        print(f"⚠️ Sanitized Excel ফাইল সেভ করা যায়নি, মূল ফাইল পাঠানো হচ্ছে: {error}")
        return source_path, 0

    print(f"🛡️ Excel ফাইল sanitize হয়েছে — {redacted}টি cell-এ ব্যক্তিগত তথ্য মুছে দেওয়া হয়েছে।")
    return output_path, redacted
