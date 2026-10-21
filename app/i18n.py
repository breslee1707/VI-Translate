"""Every word the desktop app shows, in each interface language it offers.

Vietnamese stays the default: it is who the app was built for. English opens
it to everyone else who uses it and files issues here (#21). Another language
is one more dictionary with the same keys and the same {placeholders}; the
tests hold every catalog to the Vietnamese one, so a missing entry fails a
test instead of showing a key to a user.
"""

from __future__ import annotations

UI_LANGUAGES = {"vi": "Tiếng Việt", "en": "English"}
DEFAULT_UI_LANGUAGE = "vi"

_VI: dict[str, str] = {
    "app.tagline": "Dịch PDF, giữ nguyên bố cục",
    "window.progress": "{percent}% · PDF Translate",
    "drop.prompt": "Kéo thả file PDF hoặc thư mục vào đây",
    "drop.hint": "Bản dịch được lưu vào thư mục translated, ngay cạnh file gốc",
    "drop.pick_files": "Chọn file",
    "drop.pick_folder": "Chọn thư mục",
    "controls.target": "Dịch sang",
    "controls.ocr": "Trang ảnh scan",
    "controls.ocr_hint": "OCR thử nghiệm: bảng, công thức và hình không an toàn sẽ được giữ nguyên.",
    "controls.bilingual": "Bản song ngữ",
    "controls.bilingual_hint.side-by-side": (
        "Bản song ngữ: thêm file -dual.pdf, mỗi trang gốc nằm bên trái trang dịch."
    ),
    "controls.bilingual_hint.alternating": (
        "Bản song ngữ: thêm file -dual.pdf, trang gốc rồi đến trang dịch, lần lượt đến hết."
    ),
    "controls.bilingual_hint.layers": (
        "Bản song ngữ: thêm file -dual.pdf; bật lớp Original trong bảng Layers của trình xem "
        "PDF để thấy bản gốc."
    ),
    "controls.overwrite": "Ghi đè file đã dịch trước đó",
    "controls.google_browser": "Google qua trình duyệt (tự chọn, tự mở xác minh)",
    "controls.translate": "Dịch",
    "controls.translate_one": "Dịch 1 file",
    "controls.translate_many": "Dịch {count} file",
    "controls.translating": "Đang dịch…",
    "controls.resume": "Tiếp tục dịch",
    "ocr.off": "Tắt OCR",
    "ocr.standard": "Tự động",
    "ocr.enhanced": "Nâng cao (chậm)",
    "bilingual.off": "Không tạo",
    "bilingual.side-by-side": "Hai trang cạnh nhau",
    "bilingual.alternating": "Xen kẽ từng trang",
    "bilingual.layers": "Bản gốc ở lớp ẩn",
    "queue.title": "Hàng đợi",
    "queue.title_count": "Hàng đợi ({count})",
    "queue.clear": "Xoá tất cả",
    "queue.add_files": "Thêm file",
    "queue.add_folder": "Thêm thư mục",
    "status.empty": "Chưa có file nào",
    "status.queued": "{count} file trong hàng đợi",
    "output.open_folder": "Mở thư mục kết quả",
    "status.nothing_left": "Không còn file nào cần dịch",
    "status.preparing_pages": "Đang chuẩn bị {pages} trang…",
    "status.preparing": "Đang chuẩn bị…",
    "status.paused_verification": "Đang tạm dừng — phần đã dịch được giữ để dùng lại",
    "status.translating_page": "Đang dịch {name} · trang {done}/{total}",
    "verification.title": "Google cần xác minh",
    "verification.show": "Hiện lại cửa sổ Google",
    "common.later": "Để sau",
    "common.close": "Đóng",
    "health.title": "Bản cài đặt chưa đầy đủ",
    "health.missing": (
        "Thiếu thành phần: {modules}. Thường do phần mềm diệt virus xoá file hoặc "
        "giải nén chưa đủ. Hãy {reinstall}."
    ),
    "health.reinstall_dmg": "cài lại từ file .dmg",
    "health.reinstall_zip": "giải nén lại TOÀN BỘ file .zip và thêm ngoại lệ cho thư mục ứng dụng",
    "update.available": "● Có bản mới {tag}",
    "update.downloading": "↓ Đang tải bản mới {tag}…",
    "update.progress": "↓ Đang tải {tag} — {percent}%",
    "update.ready": "● Cài {tag} & khởi động lại",
    "update.busy": "Đang dịch — cài bản mới sau khi xong",
    "update.helper_failed": "Không chạy được trình cập nhật — tải thủ công",
    "update.dialog_title": "Cài bản mới",
    "update.downloaded": "Bản {tag} đã tải xong",
    "update.dialog_body": "App sẽ đóng, cài bản mới rồi tự mở lại. Mất khoảng 10 giây.",
    "update.install": "Cài & khởi động lại",
    "close.title": "Đang dịch",
    "close.heading": "Đóng ứng dụng khi đang dịch?",
    "close.body": (
        "File đang dịch sẽ dừng lại. Các đoạn đã dịch được giữ trong bộ nhớ đệm, nên lần "
        "dịch lại sẽ nhanh hơn."
    ),
    "close.quit": "Đóng ứng dụng",
    "close.keep": "Tiếp tục dịch",
    "failure.title": "Chi tiết lỗi",
    "failure.copy": "Sao chép báo cáo",
    "failure.copied": "Đã sao chép",
    "failure.open_log": "Mở file log",
    "failure.webview2": "WebView2 dự phòng",
    "row.needs_verification": "Cần xác minh",
    "row.verification_open": "Cửa sổ Google đã mở. Hãy hoàn tất CAPTCHA để app tự tiếp tục.",
    "row.translating": "Đang dịch",
    "row.done": "Đã dịch xong",
    "row.open": "Mở",
    "row.reveal": "Thư mục",
    "row.dual": "Song ngữ",
    "row.details": "Chi tiết",
    "info.pages_one": "1 trang",
    "info.pages_many": "{count} trang",
    "number.decimal": ",",
    "stage.preparing": "Đang chuẩn bị",
    "stage.ocr": "Đang nhận dạng chữ OCR",
    "stage.translating": "Đang phân tích bố cục và dịch",
    "stage.layout": "Đang phân tích bố cục",
    "stage.request": "Đang chờ bản dịch từ Google",
    "stage.pacing": "Đang nghỉ giữa các yêu cầu Google",
    "stage.verification": "Hãy xác minh CAPTCHA trong cửa sổ Google",
    "stage.verified": "Đã xác minh Google — đang tiếp tục dịch",
    "stage.waiting": "Kết nối gián đoạn — đang chờ thử lại",
    "stage.saving": "Đang xuất PDF",
    "stage.bilingual": "Đang tạo bản song ngữ",
    "stage.other": "Đang xử lý",
    "stage.page": " · trang {done}/{total}",
    "summary.completed": "Hoàn thành {completed}/{total} file",
    "summary.partial": " · {count} file còn phần chưa dịch",
    "summary.failed": " · {count} file lỗi",
    "summary.paused": "Đã tạm dừng · Phần đã dịch được lưu để dùng lại",
    "summary.waiting": " · {count} file đang chờ",
    "outcome.done": "Hoàn thành — {details}",
    "outcome.untranslated": "còn {count} đoạn chưa dịch",
    "outcome.kept": " ({causes}; phần đã dịch được giữ lại cho lần sau)",
    "outcome.scan_pages": "trang scan giữ nguyên: {pages}",
    "outcome.ocr_kept": "OCR giữ lại {count} vùng/trang không an toàn",
    "outcome.bilingual_failed": "không tạo được bản song ngữ",
    "service.RateLimitedError": (
        "Google tạm chặn yêu cầu từ kết nối này; "
        "hiện chưa xác định được thời gian khôi phục, hãy thử lại sau"
    ),
    "service.ServiceUnavailableError": "Google Dịch không phản hồi, hãy kiểm tra mạng rồi dịch lại",
    "report.title": "PDF Translate - báo cáo lỗi",
    "report.time": "Thời điểm : {value}",
    "report.version": "Phiên bản : {value}",
    "report.system": "Hệ điều hành: {value}",
    "report.python": "Python    : {value}",
    "report.file": "File      : {value}",
    "report.code": "Mã lỗi    : {value}",
    "report.details": "Chi tiết kỹ thuật:",
    "report.log": "Log đầy đủ: {value}",
    "E-UNK-99.summary": "Lỗi không xác định",
    "E-UNK-99.advice": "Hãy gửi báo cáo bên dưới cho nhà phát triển để được hỗ trợ.",
    "E-VERIFY-01.summary": "Đã để xác minh Google sau",
    "E-VERIFY-01.advice": (
        "Đợt dịch đã tạm dừng theo lựa chọn của bạn. Phần đã dịch được giữ để dùng lại. "
        "Chọn Tiếp tục dịch khi bạn sẵn sàng xác minh."
    ),
    "E-BROWSER-01.summary": "Không khởi động được cửa sổ xác minh Google",
    "E-BROWSER-01.advice": (
        "Giải nén đầy đủ ứng dụng, đóng cửa sổ Google của lần dịch trước rồi thử lại. "
        "App tự tìm Chrome, Edge, Brave, Cốc Cốc hoặc Firefox đã cài. Trên Windows, "
        "WebView2 Runtime là lựa chọn dự phòng nếu trình duyệt không dùng được. "
        "Phần đã dịch được giữ trong bộ nhớ đệm để dùng lại."
    ),
    "E-NET-08.summary": "Google hiện từ chối yêu cầu từ kết nối này",
    "E-NET-08.advice": (
        "Đợt dịch đã tạm dừng. Các đoạn dịch thành công được lưu để dùng lại. "
        "Chọn Tiếp tục dịch sau để thử lại; hiện chưa xác định được thời gian "
        "Google khôi phục. Không cần gửi lại liên tục."
    ),
    "E-NET-09.summary": "Dịch vụ dịch hiện không phản hồi hoặc từ chối truy cập",
    "E-NET-09.advice": (
        "Đợt dịch đã tạm dừng. Kiểm tra kết nối rồi chọn Tiếp tục dịch. "
        "Các đoạn dịch thành công được lưu để dùng lại."
    ),
    "E-OCR-07.summary": "OCR không thể xử lý trang scan an toàn",
    "E-OCR-07.advice": (
        "Ứng dụng đã giữ nguyên trang để tránh làm hỏng bảng, công thức hoặc hình. "
        "Thử chế độ OCR Nâng cao (chậm); nếu vẫn lỗi, dùng PDF có lớp chữ hoặc gửi báo cáo này."
    ),
    "E-OUT-11.summary": "Không lưu được file kết quả",
    "E-OUT-11.advice": (
        "Đóng file PDF kết quả nếu nó đang mở trong trình đọc PDF rồi dịch lại. Nếu vẫn "
        "lỗi, cho phép PDF Translate trong phần mềm diệt virus hoặc mục chống "
        "ransomware (Windows Security → Ransomware protection), hoặc chép file nguồn "
        "sang thư mục khác rồi dịch."
    ),
    "E-MODEL-10.summary": "Mô hình phân tích bố cục không chạy được trên máy này",
    "E-MODEL-10.advice": (
        "Ứng dụng đã tự chuyển sang chạy bằng CPU nhưng vẫn lỗi. Hãy cập nhật lên "
        "bản mới nhất, khởi động lại máy rồi dịch lại; nếu vẫn lỗi, gửi báo cáo này "
        "cho nhà phát triển."
    ),
    "E-CORE-01.summary": "Ứng dụng thiếu thư viện nội bộ",
    "E-CORE-01.advice": (
        "Hãy giải nén TOÀN BỘ thư mục rồi chạy lại từ thư mục đã giải nén "
        "(không chạy trực tiếp trong file .zip), và thêm ngoại lệ cho phần mềm "
        "diệt virus. Nếu vẫn lỗi, gửi báo cáo này cho nhà phát triển."
    ),
    "E-OUT-05.summary": "Đã có bản dịch trước đó",
    "E-OUT-05.advice": (
        'Tick ô "Ghi đè file đã dịch trước đó" rồi dịch lại, hoặc xoá file cũ '
        "trong thư mục translated."
    ),
    "E-PDF-02.summary": "File PDF bị lỗi cấu trúc",
    "E-PDF-02.advice": (
        "File này hỏng ở mức không sửa được. Hãy mở bằng trình đọc PDF rồi "
        '"In ra PDF" (Print to PDF) để tạo bản sạch, sau đó dịch bản đó.'
    ),
    "E-PDF-01.summary": "File không phải PDF hợp lệ",
    "E-PDF-01.advice": "Kiểm tra lại file: nó phải là PDF thật và vẫn còn ở đúng vị trí cũ.",
    "E-PDF-03.summary": "PDF chỉ chứa ảnh scan",
    "E-PDF-03.advice": (
        "Bật OCR: chọn Trang ảnh scan → Tự động rồi dịch lại. Vùng bảng, công thức "
        "hoặc hình không an toàn sẽ được giữ nguyên và kết quả được báo là dịch một phần."
    ),
    "E-NET-04.summary": "Không kết nối được dịch vụ dịch",
    "E-NET-04.advice": (
        "Kiểm tra mạng hoặc VPN rồi dịch lại. Các đoạn đã dịch được vẫn giữ "
        "trong bộ nhớ đệm nên lần chạy sau sẽ nhanh hơn."
    ),
    "E-MEM-06.summary": "Máy không đủ bộ nhớ cho file này",
    "E-MEM-06.advice": "Đóng bớt ứng dụng khác, hoặc dịch file lớn thành từng phần nhỏ hơn.",
}

_EN: dict[str, str] = {
    "app.tagline": "Translate PDFs, keep the layout",
    "window.progress": "{percent}% · PDF Translate",
    "drop.prompt": "Drop PDF files or a folder here",
    "drop.hint": "Translations are saved to a translated folder next to each file",
    "drop.pick_files": "Choose files",
    "drop.pick_folder": "Choose folder",
    "controls.target": "Translate to",
    "controls.ocr": "Scanned pages",
    "controls.ocr_hint": "Experimental OCR: tables, formulas and figures it cannot handle safely stay as they are.",
    "controls.bilingual": "Bilingual copy",
    "controls.bilingual_hint.side-by-side": (
        "Bilingual copy: also writes -dual.pdf, each original page left of its translation."
    ),
    "controls.bilingual_hint.alternating": (
        "Bilingual copy: also writes -dual.pdf, each original page followed by its translation."
    ),
    "controls.bilingual_hint.layers": (
        "Bilingual copy: also writes -dual.pdf; turn on the Original layer in your PDF "
        "viewer's Layers panel to see the source."
    ),
    "controls.overwrite": "Overwrite earlier translations",
    "controls.google_browser": "Google through a browser (opens verification when needed)",
    "controls.translate": "Translate",
    "controls.translate_one": "Translate 1 file",
    "controls.translate_many": "Translate {count} files",
    "controls.translating": "Translating…",
    "controls.resume": "Continue",
    "ocr.off": "OCR off",
    "ocr.standard": "Automatic",
    "ocr.enhanced": "Enhanced (slow)",
    "bilingual.off": "None",
    "bilingual.side-by-side": "Side by side",
    "bilingual.alternating": "Alternating pages",
    "bilingual.layers": "Original on a hidden layer",
    "queue.title": "Queue",
    "queue.title_count": "Queue ({count})",
    "queue.clear": "Clear all",
    "queue.add_files": "Add files",
    "queue.add_folder": "Add folder",
    "status.empty": "No files yet",
    "status.queued": "{count} file(s) in the queue",
    "output.open_folder": "Open output folder",
    "status.nothing_left": "Nothing left to translate",
    "status.preparing_pages": "Preparing {pages} pages…",
    "status.preparing": "Preparing…",
    "status.paused_verification": "Paused — what is translated is kept for later",
    "status.translating_page": "Translating {name} · page {done}/{total}",
    "verification.title": "Google needs a verification",
    "verification.show": "Show the Google window",
    "common.later": "Later",
    "common.close": "Close",
    "health.title": "This installation is incomplete",
    "health.missing": (
        "Missing: {modules}. Antivirus software removing a file, or an incomplete "
        "extraction, usually causes this. Please {reinstall}."
    ),
    "health.reinstall_dmg": "reinstall from the .dmg",
    "health.reinstall_zip": "extract the WHOLE .zip again and add an antivirus exception for the app folder",
    "update.available": "● New version {tag}",
    "update.downloading": "↓ Downloading {tag}…",
    "update.progress": "↓ Downloading {tag} — {percent}%",
    "update.ready": "● Install {tag} & restart",
    "update.busy": "Translating — the update installs once it finishes",
    "update.helper_failed": "The updater could not start — download the update manually",
    "update.dialog_title": "Install update",
    "update.downloaded": "Version {tag} is downloaded",
    "update.dialog_body": "The app closes, installs the update and reopens. It takes about 10 seconds.",
    "update.install": "Install & restart",
    "close.title": "Translation in progress",
    "close.heading": "Quit while translating?",
    "close.body": (
        "The file being translated stops. Segments already translated stay in the cache, "
        "so translating it again is faster."
    ),
    "close.quit": "Quit",
    "close.keep": "Keep translating",
    "failure.title": "Error details",
    "failure.copy": "Copy report",
    "failure.copied": "Copied",
    "failure.open_log": "Open log file",
    "failure.webview2": "WebView2 fallback",
    "row.needs_verification": "Verify",
    "row.verification_open": "The Google window is open. Complete the CAPTCHA and the app continues by itself.",
    "row.translating": "Translating",
    "row.done": "Translated",
    "row.open": "Open",
    "row.reveal": "Folder",
    "row.dual": "Bilingual",
    "row.details": "Details",
    "info.pages_one": "1 page",
    "info.pages_many": "{count} pages",
    "number.decimal": ".",
    "stage.preparing": "Preparing",
    "stage.ocr": "Recognising text (OCR)",
    "stage.translating": "Analysing the layout and translating",
    "stage.layout": "Analysing the layout",
    "stage.request": "Waiting for Google's translation",
    "stage.pacing": "Pausing between Google requests",
    "stage.verification": "Complete the CAPTCHA in the Google window",
    "stage.verified": "Google verified — translating again",
    "stage.waiting": "Connection lost — waiting to retry",
    "stage.saving": "Writing the PDF",
    "stage.bilingual": "Building the bilingual copy",
    "stage.other": "Working",
    "stage.page": " · page {done}/{total}",
    "summary.completed": "Finished {completed}/{total} file(s)",
    "summary.partial": " · {count} with untranslated parts",
    "summary.failed": " · {count} failed",
    "summary.paused": "Paused · What is translated is saved for later",
    "summary.waiting": " · {count} waiting",
    "outcome.done": "Done — {details}",
    "outcome.untranslated": "{count} segment(s) left untranslated",
    "outcome.kept": " ({causes}; what did translate is kept for next time)",
    "outcome.scan_pages": "scanned pages left as they are: {pages}",
    "outcome.ocr_kept": "OCR left {count} unsafe regions or pages untouched",
    "outcome.bilingual_failed": "the bilingual copy could not be built",
    "service.RateLimitedError": (
        "Google is refusing requests from this connection for now; "
        "when that ends is unknown, so try again later"
    ),
    "service.ServiceUnavailableError": "Google Translate did not answer; check the connection and translate again",
    "report.title": "PDF Translate - error report",
    "report.time": "Time      : {value}",
    "report.version": "Version   : {value}",
    "report.system": "System    : {value}",
    "report.python": "Python    : {value}",
    "report.file": "File      : {value}",
    "report.code": "Error code: {value}",
    "report.details": "Technical details:",
    "report.log": "Full log: {value}",
    "E-UNK-99.summary": "Unknown error",
    "E-UNK-99.advice": "Send the report below to the developer for help.",
    "E-VERIFY-01.summary": "Google verification put off",
    "E-VERIFY-01.advice": (
        "The batch is paused as you chose. What is translated is kept for later. "
        "Choose Continue when you are ready to verify."
    ),
    "E-BROWSER-01.summary": "The Google verification window could not start",
    "E-BROWSER-01.advice": (
        "Extract the whole app, close the Google window of the previous run and try again. "
        "The app looks for an installed Chrome, Edge, Brave, Cốc Cốc or Firefox. On Windows, "
        "the WebView2 Runtime is the fallback when no browser can be used. "
        "What is translated stays in the cache for later."
    ),
    "E-NET-08.summary": "Google is refusing requests from this connection",
    "E-NET-08.advice": (
        "The batch is paused. Successful translations are saved for later. "
        "Choose Continue later to try again; when Google lifts the block is unknown. "
        "There is no need to keep retrying."
    ),
    "E-NET-09.summary": "The translation service is not answering or refuses access",
    "E-NET-09.advice": (
        "The batch is paused. Check the connection, then choose Continue. "
        "Successful translations are saved for later."
    ),
    "E-OCR-07.summary": "OCR could not process the scanned page safely",
    "E-OCR-07.advice": (
        "The app left the page as it was so no table, formula or figure is damaged. "
        "Try the Enhanced (slow) OCR mode; if it still fails, use a PDF with a text layer "
        "or send this report."
    ),
    "E-OUT-11.summary": "The result could not be saved",
    "E-OUT-11.advice": (
        "Close the translated PDF if a PDF reader has it open, then translate again. If it "
        "still fails, allow PDF Translate in your antivirus or ransomware protection "
        "(Windows Security → Ransomware protection), or copy the source file to "
        "another folder and translate it there."
    ),
    "E-MODEL-10.summary": "The layout model cannot run on this computer",
    "E-MODEL-10.advice": (
        "The app already switched to the CPU and it still failed. Update to the latest "
        "version, restart the computer and translate again; if it still fails, send this "
        "report to the developer."
    ),
    "E-CORE-01.summary": "The app is missing an internal library",
    "E-CORE-01.advice": (
        "Extract the WHOLE folder and run the app from the extracted folder "
        "(not from inside the .zip), and add an antivirus exception for it. "
        "If it still fails, send this report to the developer."
    ),
    "E-OUT-05.summary": "A translation already exists",
    "E-OUT-05.advice": (
        'Tick "Overwrite earlier translations" and translate again, or delete the old '
        "file in the translated folder."
    ),
    "E-PDF-02.summary": "The PDF's structure is damaged",
    "E-PDF-02.advice": (
        "This file is damaged beyond repair. Open it in a PDF reader and use "
        '"Print to PDF" to make a clean copy, then translate that copy.'
    ),
    "E-PDF-01.summary": "Not a valid PDF file",
    "E-PDF-01.advice": "Check the file: it must be a real PDF and still be where it was.",
    "E-PDF-03.summary": "The PDF holds only scanned images",
    "E-PDF-03.advice": (
        "Turn on OCR: set Scanned pages → Automatic and translate again. Tables, formulas "
        "and figures it cannot handle safely stay as they are, and the result is reported "
        "as partly translated."
    ),
    "E-NET-04.summary": "The translation service could not be reached",
    "E-NET-04.advice": (
        "Check the network or VPN and translate again. Segments that did translate stay "
        "in the cache, so the next run is faster."
    ),
    "E-MEM-06.summary": "Not enough memory for this file",
    "E-MEM-06.advice": "Close other apps, or translate a large file in smaller parts.",
}

CATALOGS: dict[str, dict[str, str]] = {"vi": _VI, "en": _EN}

_language = DEFAULT_UI_LANGUAGE


def set_ui_language(code: str | None) -> str:
    """Switch the interface language; an unknown code keeps the default."""
    global _language
    _language = code if code in CATALOGS else DEFAULT_UI_LANGUAGE
    return _language


def ui_language() -> str:
    return _language


def t(key: str, **values: object) -> str:
    """The text for ``key`` in the current language, with its placeholders filled.

    A key one catalog lacks falls back to Vietnamese rather than raising in
    front of the user: the tests are where a missing entry should fail.
    """
    text = CATALOGS[_language].get(key)
    if text is None:
        text = CATALOGS[DEFAULT_UI_LANGUAGE][key]
    return text.format(**values) if values else text
