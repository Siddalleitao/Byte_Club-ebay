"""Internationalisation strings for PartProof UI.

English and Arabic translations. Arabic text is right-to-left.
"""

from __future__ import annotations

STRINGS: dict[str, dict[str, str]] = {
    "en": {
        # App
        "app_title": "PartProof",
        "app_subtitle": "Evidence-first listing assistant for Dubai used-auto-parts",
        # Login
        "login_select_profile": "Select your profile",
        "login_continue": "Continue",
        "login_welcome": "Welcome",
        # Sidebar
        "seller_card": "Seller card",
        "business": "Business",
        "location": "Location",
        "languages": "Languages",
        "corridor": "Corridor",
        "handling_days": "Handling days",
        "rack_codes": "Rack codes",
        "marketplace": "Marketplace",
        "seller_type": "Type",
        # Upload
        "upload_title": "Upload inventory",
        "upload_photos": "Photo folder",
        "upload_spreadsheet": "Inventory spreadsheet",
        "upload_donor_notes": "Donor notes",
        "upload_drag_here": "Drag files here or click to browse",
        "upload_start": "Start processing",
        "upload_progress": "Processing parts",
        "upload_complete": "Upload complete",
        "upload_assign_tray": "Unlinked files — assign manually",
        "upload_assign_tray_empty": "No unlinked files",
        "upload_column_mapping": "Column mapping confirmation",
        "upload_column_mapping_desc": "Review how spreadsheet columns map to part attributes",
        "upload_confirm_mapping": "Confirm mapping",
        "upload_files_queued": "files queued",
        "upload_no_files": "No files selected yet",
        # Board
        "board_title": "Inventory board",
        "board_search": "Search by SKU, make, or part number",
        "board_filter_status": "Status",
        "board_filter_issue": "Issue type",
        "board_filter_rack": "Rack",
        "board_all": "All",
        "board_open_questions": "open questions",
        "board_no_questions": "No open questions",
        "board_part_number": "Part number",
        "board_donor": "Donor",
        "board_rack": "Rack",
        "board_price": "Price",
        "board_issues": "Issues",
        "board_view_questions": "View questions",
        # Badges
        "badge_ready": "Ready for seller review",
        "badge_missing": "Information missing",
        "badge_specialist": "Specialist review required",
        "badge_approved": "Approved",
        # Admin
        "admin_title": "Admin — seller details",
        "admin_edit_profile": "Edit seller profile",
        "admin_edit_profile_desc": "Changes invalidate the attribute cache",
        "admin_save": "Save changes",
        "admin_saved": "Profile saved. Cache invalidated.",
        "admin_reference_data": "Reference data",
        "admin_compat_version": "Compatibility reference",
        "admin_compat_rows": "Rows",
        "admin_checklist_version": "Checklist version",
        "admin_checklist_date": "Date",
        "admin_checklist_purpose": "Purpose",
        "admin_checklist_disclaimer": "Disclaimer",
        "admin_test_label": "TEST LABEL",
        "admin_test_label_desc": "This checklist is for hackathon testing only — not customs or legal guidance",
        # Questions dialog
        "questions_title": "Open questions",
        "questions_answer_placeholder": "Type the seller's answer...",
        "questions_submit": "Submit answer",
        "questions_no_questions": "No open questions for this part",
        "questions_edge_case": "Edge case",
        "questions_priority": "Priority",
        # Language
        "lang_toggle": "العربية",
        "lang_label": "Language",
        # Misc
        "not_logged_in": "Please log in to continue",
        "api_offline": "API offline — showing mock data",
        "api_online": "API connected",
    },
    "ar": {
        # App
        "app_title": "PartProof",
        "app_subtitle": "مساعد قوائم الأدلة أولاً لقطع غيار السيارات المستعملة في دبي",
        # Login
        "login_select_profile": "اختر ملفك الشخصي",
        "login_continue": "متابعة",
        "login_welcome": "مرحباً",
        # Sidebar
        "seller_card": "بطاقة البائع",
        "business": "النشاط",
        "location": "الموقع",
        "languages": "اللغات",
        "corridor": "الممر التجاري",
        "handling_days": "أيام التداول",
        "rack_codes": "رموز الأرفف",
        "marketplace": "السوق",
        "seller_type": "النوع",
        # Upload
        "upload_title": "رفع المخزون",
        "upload_photos": "مجلد الصور",
        "upload_spreadsheet": "جدول المخزون",
        "upload_donor_notes": "ملاحظات المركبة المانحة",
        "upload_drag_here": "اسحب الملفات هنا أو انقر للتصفح",
        "upload_start": "بدء المعالجة",
        "upload_progress": "معالجة القطع",
        "upload_complete": "اكتمل الرفع",
        "upload_assign_tray": "ملفات غير مرتبطة — اربطها يدوياً",
        "upload_assign_tray_empty": "لا توجد ملفات غير مرتبطة",
        "upload_column_mapping": "تأكيد تعيين الأعمدة",
        "upload_column_mapping_desc": "راجع كيفية تعيين أعمدة الجدول إلى سمات القطعة",
        "upload_confirm_mapping": "تأكيد التعيين",
        "upload_files_queued": "ملفات في الانتظار",
        "upload_no_files": "لم يتم اختيار ملفات بعد",
        # Board
        "board_title": "لوحة المخزون",
        "board_search": "ابحث برقم القطعة أو العلامة أو رقم الجزء",
        "board_filter_status": "الحالة",
        "board_filter_issue": "نوع المشكلة",
        "board_filter_rack": "الرف",
        "board_all": "الكل",
        "board_open_questions": "أسئلة مفتوحة",
        "board_no_questions": "لا توجد أسئلة مفتوحة",
        "board_part_number": "رقم الجزء",
        "board_donor": "المركبة المانحة",
        "board_rack": "الرف",
        "board_price": "السعر",
        "board_issues": "المشاكل",
        "board_view_questions": "عرض الأسئلة",
        # Badges
        "badge_ready": "جاهز لمراجعة البائع",
        "badge_missing": "معلومات ناقصة",
        "badge_specialist": "يتطلب مراجعة مختص",
        "badge_approved": "معتمد",
        # Admin
        "admin_title": "الإدارة — تفاصيل البائع",
        "admin_edit_profile": "تعديل ملف البائع",
        "admin_edit_profile_desc": "التغييرات تلغي صلاحية ذاكرة التخزين المؤقت",
        "admin_save": "حفظ التغييرات",
        "admin_saved": "تم حفظ الملف. تم تحديث الذاكرة المؤقتة.",
        "admin_reference_data": "البيانات المرجعية",
        "admin_compat_version": "المرجعCompatibility المرجع",
        "admin_compat_rows": "الصفوف",
        "admin_checklist_version": "إصدار قائمة التحقق",
        "admin_checklist_date": "التاريخ",
        "admin_checklist_purpose": "الغرض",
        "admin_checklist_disclaimer": "إخلاء المسؤولية",
        "admin_test_label": "علامة اختبار",
        "admin_test_label_desc": "هذه القائمة لاختبار الهاكاثون فقط — ليست إرشادات جمركية أو قانونية",
        # Questions dialog
        "questions_title": "الأسئلة المفتوحة",
        "questions_answer_placeholder": "اكتب إجابة البائع...",
        "questions_submit": "إرسال الإجابة",
        "questions_no_questions": "لا توجد أسئلة مفتوحة لهذه القطعة",
        "questions_edge_case": "حالة استثنائية",
        "questions_priority": "الأولوية",
        # Language
        "lang_toggle": "English",
        "lang_label": "اللغة",
        # Misc
        "not_logged_in": "يرجى تسجيل الدخول للمتابعة",
        "api_offline": "API غير متصل — عرض بيانات تجريبية",
        "api_online": "API متصل",
    },
}


def t(key: str, lang: str = "en") -> str:
    """Translate a key to the given language."""
    return STRINGS.get(lang, STRINGS["en"]).get(key, key)
